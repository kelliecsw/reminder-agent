from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime
import json
import sqlite3

from reminder_agent.domain.enums import TelegramUpdateStatus
from reminder_agent.domain.models import TelegramUpdateRecord


_COLUMNS = """
    update_id, telegram_user_id, telegram_chat_id, application_user_id,
    update_kind, payload_json, status, attempt_count, last_error,
    next_attempt_at_utc, claim_token, claimed_at_utc, lease_expires_at_utc
"""


class TelegramUpdateRepository:
    def create(self, conn: sqlite3.Connection, item: TelegramUpdateRecord) -> bool:
        now = datetime.now(UTC).isoformat()
        cursor = conn.execute(
            """
            INSERT INTO telegram_updates (
                update_id, telegram_user_id, telegram_chat_id,
                application_user_id, update_kind, payload_json, status,
                attempt_count, last_error, next_attempt_at_utc,
                claim_token, claimed_at_utc, lease_expires_at_utc,
                created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(update_id) DO NOTHING
            """,
            (
                item.update_id,
                item.telegram_user_id,
                item.telegram_chat_id,
                item.application_user_id,
                item.update_kind,
                json.dumps(item.payload, ensure_ascii=False),
                item.status.value,
                item.attempt_count,
                item.last_error,
                item.next_attempt_at_utc.isoformat(),
                item.claim_token,
                _iso(item.claimed_at_utc),
                _iso(item.lease_expires_at_utc),
                now,
                now,
            ),
        )
        return cursor.rowcount == 1

    def get(self, conn: sqlite3.Connection, update_id: int) -> TelegramUpdateRecord | None:
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM telegram_updates WHERE update_id = ?",
            (update_id,),
        ).fetchone()
        return _row(row) if row is not None else None

    def claim_next(
        self,
        conn: sqlite3.Connection,
        *,
        now_utc: datetime,
        lease_expires_at_utc: datetime,
        claim_token: str,
        update_kinds: Collection[str] | None = None,
    ) -> TelegramUpdateRecord | None:
        kind_values = tuple(update_kinds or ())
        kind_filter = ""
        parameters: list[str] = []
        if kind_values:
            placeholders = ", ".join("?" for _ in kind_values)
            kind_filter = f"update_kind IN ({placeholders}) AND"
            parameters.extend(kind_values)
        parameters.extend(
            (
                TelegramUpdateStatus.RECEIVED.value,
                TelegramUpdateStatus.RETRY_WAIT.value,
                now_utc.isoformat(),
                TelegramUpdateStatus.CLAIMED.value,
                now_utc.isoformat(),
            )
        )
        row = conn.execute(
            f"""
            SELECT update_id
            FROM telegram_updates
            WHERE {kind_filter} (
                (
                    status IN (?, ?) AND next_attempt_at_utc <= ?
                ) OR (
                    status = ? AND lease_expires_at_utc <= ?
                )
            )
            ORDER BY next_attempt_at_utc, update_id
            LIMIT 1
            """,
            parameters,
        ).fetchone()
        if row is None:
            return None
        update_id = int(row["update_id"])
        conn.execute(
            """
            UPDATE telegram_updates
            SET status = ?, attempt_count = attempt_count + 1,
                last_error = NULL, claim_token = ?, claimed_at_utc = ?,
                lease_expires_at_utc = ?, updated_at_utc = ?
            WHERE update_id = ?
            """,
            (
                TelegramUpdateStatus.CLAIMED.value,
                claim_token,
                now_utc.isoformat(),
                lease_expires_at_utc.isoformat(),
                now_utc.isoformat(),
                update_id,
            ),
        )
        return self.get(conn, update_id)

    def finish(
        self,
        conn: sqlite3.Connection,
        *,
        update_id: int,
        claim_token: str,
        status: TelegramUpdateStatus,
        now_utc: datetime,
        next_attempt_at_utc: datetime | None = None,
        error: str | None = None,
    ) -> bool:
        cursor = conn.execute(
            """
            UPDATE telegram_updates
            SET status = ?, last_error = ?, next_attempt_at_utc = ?,
                claim_token = NULL, claimed_at_utc = NULL,
                lease_expires_at_utc = NULL, updated_at_utc = ?
            WHERE update_id = ? AND status = ? AND claim_token = ?
            """,
            (
                status.value,
                error,
                (next_attempt_at_utc or now_utc).isoformat(),
                now_utc.isoformat(),
                update_id,
                TelegramUpdateStatus.CLAIMED.value,
                claim_token,
            ),
        )
        return cursor.rowcount == 1


def _row(row: sqlite3.Row) -> TelegramUpdateRecord:
    return TelegramUpdateRecord(
        update_id=int(row["update_id"]),
        telegram_user_id=int(row["telegram_user_id"]),
        telegram_chat_id=int(row["telegram_chat_id"]),
        application_user_id=int(row["application_user_id"]),
        update_kind=row["update_kind"],
        payload=json.loads(row["payload_json"]),
        status=TelegramUpdateStatus(row["status"]),
        attempt_count=int(row["attempt_count"]),
        last_error=row["last_error"],
        next_attempt_at_utc=datetime.fromisoformat(row["next_attempt_at_utc"]),
        claim_token=row["claim_token"],
        claimed_at_utc=_datetime(row["claimed_at_utc"]),
        lease_expires_at_utc=_datetime(row["lease_expires_at_utc"]),
    )


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
