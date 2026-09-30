from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from time import perf_counter

from reminder_agent.clock import utc_now
from reminder_agent.log_values import keyboard_labels, log_interaction, private_text
from reminder_agent.telegram.gateway import TelegramGateway
from reminder_agent.telegram.outbox import OutboxService


logger = logging.getLogger(__name__)
interaction_logger = logging.getLogger("reminder_agent.telegram.interaction")


class OutboxWorker:
    def __init__(
        self,
        outbox: OutboxService,
        gateway: TelegramGateway,
        *,
        clock: Callable[[], datetime] = utc_now,
        lease_seconds: float = 60.0,
        max_attempts: int = 3,
        retry_base_seconds: float = 30.0,
        log_private_content: bool = False,
    ) -> None:
        if lease_seconds <= 0 or retry_base_seconds <= 0:
            raise ValueError("Worker timing settings must be positive.")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        self.outbox = outbox
        self.gateway = gateway
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.log_private_content = log_private_content

    async def run_once(self, now_utc: datetime | None = None) -> bool:
        now_utc = now_utc or self.clock()
        item = self.outbox.claim_ready(
            now_utc,
            lease_seconds=self.lease_seconds,
        )
        if item is None:
            return False
        started = perf_counter()
        try:
            message_id = await self.gateway.send_outbox(item)
        except Exception as exc:
            retry_at = now_utc + timedelta(
                seconds=self.retry_base_seconds * (2 ** (item.attempt_count - 1))
            )
            status = self.outbox.retry(
                item,
                now_utc=now_utc,
                retry_at_utc=retry_at,
                error=str(exc),
                max_attempts=self.max_attempts,
            )
            duration_ms = round((perf_counter() - started) * 1000)
            log_interaction(
                interaction_logger,
                heading=_outbound_heading(item.delivery_id, failed=True),
                content=private_text(
                    item.text,
                    enabled=self.log_private_content,
                ),
                metadata=(
                    f"source={item.dedupe_key} outbox_id={item.id} "
                    f"delivery_id={item.delivery_id}"
                ),
                result=(
                    f"status={status.value} attempt={item.attempt_count} "
                    f"retry_at={retry_at.isoformat()} duration_ms={duration_ms} "
                    f"error={str(exc)!r}"
                ),
                level=logging.WARNING,
            )
            return True

        self.outbox.mark_sent(
            item,
            telegram_message_id=message_id,
            now_utc=now_utc,
        )
        log_interaction(
            interaction_logger,
            heading=_outbound_heading(item.delivery_id),
            content=private_text(
                item.text,
                enabled=self.log_private_content,
            ),
            metadata=(
                f"source={item.dedupe_key} outbox_id={item.id} "
                f"delivery_id={item.delivery_id} "
                f"telegram_message_id={message_id}"
            ),
            result=(
                f"status=sent attempt={item.attempt_count} "
                f"duration_ms={round((perf_counter() - started) * 1000)} "
                f"buttons={keyboard_labels(item.keyboard)}"
            ),
        )
        return True


def _outbound_heading(delivery_id: int | None, *, failed: bool = False) -> str:
    kind = "APP REMINDER" if delivery_id is not None else "APP REPLY"
    return f"{kind} · DELIVERY FAILED" if failed else kind


__all__ = ["OutboxWorker"]
