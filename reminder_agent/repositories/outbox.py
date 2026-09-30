from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
import sqlite3

from reminder_agent.domain.enums import OutboxStatus
from reminder_agent.domain.models import OutboxMessage


_COLUMNS = """
    id, dedupe_key, application_user_id, delivery_id, telegram_chat_id,
    text, keyboard_json, rich_message_json, status, attempt_count, last_error,
    next_attempt_at_utc, claim_token, claimed_at_utc, lease_expires_at_utc,
    telegram_message_id
"""


class OutboxRepository:
    def create(self, conn: sqlite3.Connection, item: OutboxMessage) -> tuple[OutboxMessage, bool]:
        now = item.next_attempt_at_utc.isoformat()
        cursor = conn.execute(
            """
            INSERT INTO telegram_outbox (
                dedupe_key, application_user_id, delivery_id, telegram_chat_id,
                text, keyboard_json, rich_message_json, status, attempt_count,
                last_error,
                next_attempt_at_utc, claim_token, claimed_at_utc,
                lease_expires_at_utc, telegram_message_id,
                created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dedupe_key) DO NOTHING
            """,
            (
                item.dedupe_key,
                item.application_user_id,
                item.delivery_id,
                item.telegram_chat_id,
                item.text,
                json.dumps(item.keyboard, ensure_ascii=False) if item.keyboard else None,
                (
                    json.dumps(item.rich_message, ensure_ascii=False)
                    if item.rich_message
                    else None
                ),
                item.status.value,
                item.attempt_count,
                item.last_error,
                item.next_attempt_at_utc.isoformat(),
                item.claim_token,
                _iso(item.claimed_at_utc),
                _iso(item.lease_expires_at_utc),
                item.telegram_message_id,
                now,
                now,
            ),
        )
        if cursor.rowcount == 1:
            return replace(item, id=int(cursor.lastrowid)), True
        existing = self.get_by_dedupe_key(conn, item.dedupe_key)
        if existing is None:
            raise RuntimeError("Outbox conflict could not be resolved.")
        return existing, False

    def get(self, conn: sqlite3.Connection, outbox_id: int) -> OutboxMessage | None:
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM telegram_outbox WHERE id = ?",
            (outbox_id,),
        ).fetchone()
        return _row(row) if row is not None else None

    def get_by_dedupe_key(self, conn: sqlite3.Connection, key: str) -> OutboxMessage | None:
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM telegram_outbox WHERE dedupe_key = ?",
            (key,),
        ).fetchone()
        return _row(row) if row is not None else None

    def claim_next(
        self,
        conn: sqlite3.Connection,
        *,
        now_utc: datetime,
        lease_expires_at_utc: datetime,
        claim_token: str,
    ) -> OutboxMessage | None:
        row = conn.execute(
            """
            SELECT id FROM telegram_outbox
            WHERE (status IN (?, ?) AND next_attempt_at_utc <= ?)
               OR (status = ? AND lease_expires_at_utc <= ?)
            ORDER BY next_attempt_at_utc, id LIMIT 1
            """,
            (
                OutboxStatus.PENDING.value,
                OutboxStatus.RETRY_WAIT.value,
                now_utc.isoformat(),
                OutboxStatus.CLAIMED.value,
                now_utc.isoformat(),
            ),
        ).fetchone()
        if row is None:
            return None
        outbox_id = int(row["id"])
        conn.execute(
            """
            UPDATE telegram_outbox
            SET status = ?, attempt_count = attempt_count + 1,
                last_error = NULL, claim_token = ?, claimed_at_utc = ?,
                lease_expires_at_utc = ?, updated_at_utc = ?
            WHERE id = ?
            """,
            (
                OutboxStatus.CLAIMED.value,
                claim_token,
                now_utc.isoformat(),
                lease_expires_at_utc.isoformat(),
                now_utc.isoformat(),
                outbox_id,
            ),
        )
        return self.get(conn, outbox_id)

    def finish(
        self,
        conn: sqlite3.Connection,
        *,
        outbox_id: int,
        claim_token: str,
        status: OutboxStatus,
        now_utc: datetime,
        next_attempt_at_utc: datetime | None = None,
        error: str | None = None,
        telegram_message_id: int | None = None,
    ) -> bool:
        cursor = conn.execute(
            """
            UPDATE telegram_outbox
            SET status = ?, last_error = ?, next_attempt_at_utc = ?,
                telegram_message_id = COALESCE(?, telegram_message_id),
                claim_token = NULL, claimed_at_utc = NULL,
                lease_expires_at_utc = NULL, updated_at_utc = ?
            WHERE id = ? AND status = ? AND claim_token = ?
            """,
            (
                status.value,
                error,
                (next_attempt_at_utc or now_utc).isoformat(),
                telegram_message_id,
                now_utc.isoformat(),
                outbox_id,
                OutboxStatus.CLAIMED.value,
                claim_token,
            ),
        )
        return cursor.rowcount == 1


def _row(row: sqlite3.Row) -> OutboxMessage:
    return OutboxMessage(
        id=int(row["id"]),
        dedupe_key=row["dedupe_key"],
        application_user_id=int(row["application_user_id"]),
        delivery_id=int(row["delivery_id"]) if row["delivery_id"] is not None else None,
        telegram_chat_id=int(row["telegram_chat_id"]) if row["telegram_chat_id"] is not None else None,
        text=row["text"],
        keyboard=json.loads(row["keyboard_json"]) if row["keyboard_json"] else None,
        rich_message=(
            json.loads(row["rich_message_json"])
            if row["rich_message_json"]
            else None
        ),
        status=OutboxStatus(row["status"]),
        attempt_count=int(row["attempt_count"]),
        last_error=row["last_error"],
        next_attempt_at_utc=datetime.fromisoformat(row["next_attempt_at_utc"]),
        claim_token=row["claim_token"],
        claimed_at_utc=_datetime(row["claimed_at_utc"]),
        lease_expires_at_utc=_datetime(row["lease_expires_at_utc"]),
        telegram_message_id=int(row["telegram_message_id"]) if row["telegram_message_id"] is not None else None,
    )


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
