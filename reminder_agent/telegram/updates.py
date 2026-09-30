from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timedelta
from uuid import uuid4

from reminder_agent.db.database import Database
from reminder_agent.domain.enums import TelegramUpdateStatus
from reminder_agent.domain.errors import InvalidStateTransitionError
from reminder_agent.domain.models import TelegramUpdateRecord
from reminder_agent.repositories.updates import TelegramUpdateRepository
from reminder_agent.telegram.normalize import TelegramInput, TelegramInputKind


class TelegramUpdateService:
    def __init__(
        self,
        database: Database,
        *,
        repository: TelegramUpdateRepository | None = None,
    ) -> None:
        self.database = database
        self.repository = repository or TelegramUpdateRepository()

    def receive(
        self,
        *,
        update_id: int,
        item: TelegramInput,
        application_user_id: int,
        payload: dict,
        now_utc: datetime,
    ) -> bool:
        with self.database.transaction() as conn:
            return self.repository.create(
                conn,
                TelegramUpdateRecord(
                    update_id=update_id,
                    telegram_user_id=item.telegram_user_id,
                    telegram_chat_id=item.telegram_chat_id,
                    application_user_id=application_user_id,
                    update_kind=item.kind.value,
                    payload=payload,
                    next_attempt_at_utc=now_utc,
                ),
            )

    def get(self, update_id: int) -> TelegramUpdateRecord | None:
        with self.database.connect() as conn:
            return self.repository.get(conn, update_id)

    def claim_ready(
        self,
        now_utc: datetime,
        *,
        lease_seconds: float,
        kinds: Collection[TelegramInputKind] | None = None,
    ) -> TelegramUpdateRecord | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive.")
        if kinds is not None and not kinds:
            raise ValueError("kinds must contain at least one update kind.")
        with self.database.transaction(immediate=True) as conn:
            return self.repository.claim_next(
                conn,
                now_utc=now_utc,
                lease_expires_at_utc=now_utc + timedelta(seconds=lease_seconds),
                claim_token=uuid4().hex,
                update_kinds=(kind.value for kind in kinds) if kinds else None,
            )

    def complete(self, item: TelegramUpdateRecord, now_utc: datetime) -> None:
        self._finish(item, TelegramUpdateStatus.COMPLETED, now_utc)

    def retry(
        self,
        item: TelegramUpdateRecord,
        *,
        now_utc: datetime,
        retry_at_utc: datetime,
        error: str,
        max_attempts: int,
    ) -> TelegramUpdateStatus:
        status = (
            TelegramUpdateStatus.FAILED
            if item.attempt_count >= max_attempts
            else TelegramUpdateStatus.RETRY_WAIT
        )
        self._finish(
            item,
            status,
            now_utc,
            next_attempt_at_utc=retry_at_utc,
            error=error.strip()[:2_000] or "Unknown update processing error",
        )
        return status

    def _finish(
        self,
        item: TelegramUpdateRecord,
        status: TelegramUpdateStatus,
        now_utc: datetime,
        *,
        next_attempt_at_utc: datetime | None = None,
        error: str | None = None,
    ) -> None:
        if item.claim_token is None:
            raise InvalidStateTransitionError("Claimed update has no claim token.")
        with self.database.transaction() as conn:
            changed = self.repository.finish(
                conn,
                update_id=item.update_id,
                claim_token=item.claim_token,
                status=status,
                now_utc=now_utc,
                next_attempt_at_utc=next_attempt_at_utc,
                error=error,
            )
        if not changed:
            raise InvalidStateTransitionError(
                f"Telegram update {item.update_id} has a stale worker claim."
            )


__all__ = ["TelegramUpdateService"]
