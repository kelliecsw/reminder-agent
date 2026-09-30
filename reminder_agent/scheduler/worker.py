from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging

from reminder_agent.clock import utc_now
from reminder_agent.scheduler.gateway import MessageGateway
from reminder_agent.services.deliveries import DeliveryService
from reminder_agent.telegram.outbox import OutboxService


logger = logging.getLogger(__name__)


class DeliveryWorker:
    def __init__(
        self,
        delivery_service: DeliveryService,
        gateway: MessageGateway,
        *,
        clock: Callable[[], datetime] = utc_now,
        max_attempts: int = 3,
        retry_base_seconds: float = 30.0,
        lease_seconds: float = 60.0,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        if retry_base_seconds <= 0:
            raise ValueError("retry_base_seconds must be positive.")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive.")
        self.delivery_service = delivery_service
        self.gateway = gateway
        self.clock = clock
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.lease_seconds = lease_seconds

    async def run_once(self, now_utc: datetime | None = None) -> bool:
        now_utc = now_utc or self.clock()
        delivery = self.delivery_service.claim_ready(
            now_utc,
            lease_seconds=self.lease_seconds,
        )
        if delivery is None:
            return False
        if delivery.id is None:
            raise RuntimeError("Claimed delivery has no id.")

        message = self.delivery_service.prepare_for_send(
            delivery.id,
            claim_token=delivery.claim_token,
        )
        if message is None:
            logger.info("Cancelled stale delivery %s before sending.", delivery.id)
            return True

        try:
            await self.gateway.send(
                delivery_id=message.delivery_id,
                user_id=message.user_id,
                text=message.text,
            )
        except Exception as exc:
            retry_delay = self.retry_base_seconds * (
                2 ** (delivery.attempt_count - 1)
            )
            updated = self.delivery_service.retry(
                delivery.id,
                error=str(exc),
                retry_at_utc=now_utc + timedelta(seconds=retry_delay),
                max_attempts=self.max_attempts,
                claim_token=delivery.claim_token,
            )
            logger.warning(
                "Delivery %s failed on attempt %s; status=%s.",
                delivery.id,
                delivery.attempt_count,
                updated.status.value,
            )
            return True

        self.delivery_service.mark_sent(
            delivery.id,
            claim_token=delivery.claim_token,
        )
        logger.info("Sent delivery %s.", delivery.id)
        return True


class DeliveryDispatcher:
    """Validate due Deliveries and atomically queue their Telegram messages."""

    def __init__(
        self,
        delivery_service: DeliveryService,
        outbox: OutboxService,
        *,
        clock: Callable[[], datetime] = utc_now,
        lease_seconds: float = 60.0,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive.")
        self.delivery_service = delivery_service
        self.outbox = outbox
        self.clock = clock
        self.lease_seconds = lease_seconds

    async def run_once(self, now_utc: datetime | None = None) -> bool:
        now_utc = now_utc or self.clock()
        delivery = self.delivery_service.claim_ready(
            now_utc,
            lease_seconds=self.lease_seconds,
        )
        if delivery is None:
            return False
        if delivery.id is None or delivery.claim_token is None:
            raise RuntimeError("Claimed delivery is missing its id or claim token.")
        queued = self.outbox.enqueue_delivery(
            self.delivery_service,
            delivery_id=delivery.id,
            claim_token=delivery.claim_token,
            now_utc=now_utc,
        )
        if queued is None:
            logger.info("Cancelled stale delivery %s before queueing.", delivery.id)
        else:
            logger.info(
                "Queued delivery %s as outbox message %s.",
                delivery.id,
                queued.id,
            )
        return True


__all__ = ["DeliveryDispatcher", "DeliveryWorker"]
