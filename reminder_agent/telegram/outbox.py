from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from telegram import InlineKeyboardMarkup

from reminder_agent.db.database import Database
from reminder_agent.domain.enums import DeliveryStatus, OutboxStatus
from reminder_agent.domain.errors import InvalidStateTransitionError
from reminder_agent.domain.models import OutboxMessage
from reminder_agent.repositories.deliveries import DeliveryRepository
from reminder_agent.repositories.outbox import OutboxRepository
from reminder_agent.services.deliveries import DeliveryService
from reminder_agent.telegram.views import reminder_actions


class OutboxService:
    def __init__(
        self,
        database: Database,
        *,
        repository: OutboxRepository | None = None,
        delivery_repository: DeliveryRepository | None = None,
    ) -> None:
        self.database = database
        self.repository = repository or OutboxRepository()
        self.delivery_repository = delivery_repository or DeliveryRepository()

    def enqueue(
        self,
        *,
        dedupe_key: str,
        application_user_id: int,
        text: str,
        now_utc: datetime,
        telegram_chat_id: int | None = None,
        delivery_id: int | None = None,
        keyboard: InlineKeyboardMarkup | dict[str, Any] | None = None,
        rich_message: dict[str, Any] | None = None,
    ) -> tuple[OutboxMessage, bool]:
        keyboard_data = (
            keyboard.to_dict() if isinstance(keyboard, InlineKeyboardMarkup) else keyboard
        )
        with self.database.transaction() as conn:
            return self.repository.create(
                conn,
                OutboxMessage(
                    dedupe_key=dedupe_key,
                    application_user_id=application_user_id,
                    telegram_chat_id=telegram_chat_id,
                    delivery_id=delivery_id,
                    text=text,
                    keyboard=keyboard_data,
                    rich_message=rich_message,
                    next_attempt_at_utc=now_utc,
                ),
            )

    def enqueue_delivery(
        self,
        delivery_service: DeliveryService,
        *,
        delivery_id: int,
        claim_token: str,
        now_utc: datetime,
    ) -> OutboxMessage | None:
        with self.database.transaction(immediate=True) as conn:
            message = delivery_service.prepare_for_send(
                delivery_id,
                claim_token=claim_token,
            )
            if message is None:
                return None
            delivery = self.delivery_repository.get(conn, delivery_id)
            if delivery is None or delivery.claim_token != claim_token:
                raise InvalidStateTransitionError(
                    f"Delivery {delivery_id} has a stale worker claim."
                )
            outbox, _ = self.repository.create(
                conn,
                OutboxMessage(
                    dedupe_key=(
                        f"delivery:{delivery_id}:dispatch:{delivery.attempt_count}"
                    ),
                    application_user_id=message.user_id,
                    delivery_id=delivery_id,
                    text=f"🔔 {message.text}",
                    keyboard=reminder_actions(delivery_id).to_dict(),
                    next_attempt_at_utc=now_utc,
                ),
            )
            self.delivery_repository.update(
                conn,
                replace(
                    delivery,
                    status=DeliveryStatus.QUEUED,
                    claim_token=None,
                    claimed_at_utc=None,
                    lease_expires_at_utc=None,
                ),
            )
            return outbox

    def get(self, outbox_id: int) -> OutboxMessage | None:
        with self.database.connect() as conn:
            return self.repository.get(conn, outbox_id)

    def claim_ready(
        self,
        now_utc: datetime,
        *,
        lease_seconds: float,
    ) -> OutboxMessage | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive.")
        with self.database.transaction(immediate=True) as conn:
            return self.repository.claim_next(
                conn,
                now_utc=now_utc,
                lease_expires_at_utc=now_utc + timedelta(seconds=lease_seconds),
                claim_token=uuid4().hex,
            )

    def mark_sent(
        self,
        item: OutboxMessage,
        *,
        telegram_message_id: int | None,
        now_utc: datetime,
    ) -> None:
        if item.claim_token is None or item.id is None:
            raise InvalidStateTransitionError("Outbox message is not claimed.")
        with self.database.transaction() as conn:
            changed = self.repository.finish(
                conn,
                outbox_id=item.id,
                claim_token=item.claim_token,
                status=OutboxStatus.SENT,
                telegram_message_id=telegram_message_id,
                now_utc=now_utc,
            )
            if not changed:
                raise InvalidStateTransitionError(
                    f"Outbox message {item.id} has a stale worker claim."
                )
            if item.delivery_id is not None:
                delivery = self.delivery_repository.get(conn, item.delivery_id)
                if delivery is not None and delivery.status == DeliveryStatus.QUEUED:
                    self.delivery_repository.update(
                        conn,
                        replace(delivery, status=DeliveryStatus.SENT, last_error=None),
                    )

    def retry(
        self,
        item: OutboxMessage,
        *,
        now_utc: datetime,
        retry_at_utc: datetime,
        error: str,
        max_attempts: int,
    ) -> OutboxStatus:
        if item.claim_token is None or item.id is None:
            raise InvalidStateTransitionError("Outbox message is not claimed.")
        status = (
            OutboxStatus.FAILED
            if item.attempt_count >= max_attempts
            else OutboxStatus.RETRY_WAIT
        )
        clean_error = error.strip()[:2_000] or "Unknown Telegram send error"
        with self.database.transaction() as conn:
            changed = self.repository.finish(
                conn,
                outbox_id=item.id,
                claim_token=item.claim_token,
                status=status,
                next_attempt_at_utc=retry_at_utc,
                error=clean_error,
                now_utc=now_utc,
            )
            if not changed:
                raise InvalidStateTransitionError(
                    f"Outbox message {item.id} has a stale worker claim."
                )
            if status == OutboxStatus.FAILED and item.delivery_id is not None:
                delivery = self.delivery_repository.get(conn, item.delivery_id)
                if delivery is not None and delivery.status == DeliveryStatus.QUEUED:
                    self.delivery_repository.update(
                        conn,
                        replace(
                            delivery,
                            status=DeliveryStatus.FAILED,
                            last_error=clean_error,
                        ),
                    )
        return status


__all__ = ["OutboxService"]
