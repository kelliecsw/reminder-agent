from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import sqlite3

from reminder_agent.domain.enums import DeliveryStatus
from reminder_agent.domain.models import Delivery


_DELIVERY_COLUMNS = """
    id,
    user_id,
    reminder_id,
    reminder_version,
    scheduled_for_utc,
    deliver_at_utc,
    status,
    attempt_count,
    last_error,
    claim_token,
    claimed_at_utc,
    lease_expires_at_utc
"""


class DeliveryRepository:
    def create(
        self,
        conn: sqlite3.Connection,
        delivery: Delivery,
    ) -> Delivery:
        cursor = conn.execute(
            """
            INSERT INTO deliveries (
                user_id,
                reminder_id,
                reminder_version,
                scheduled_for_utc,
                deliver_at_utc,
                status,
                attempt_count,
                last_error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _delivery_values(delivery),
        )
        return replace(delivery, id=int(cursor.lastrowid))

    def create_occurrence(
        self,
        conn: sqlite3.Connection,
        delivery: Delivery,
    ) -> tuple[Delivery, bool]:
        cursor = conn.execute(
            """
            INSERT INTO deliveries (
                user_id,
                reminder_id,
                reminder_version,
                scheduled_for_utc,
                deliver_at_utc,
                status,
                attempt_count,
                last_error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                reminder_id,
                reminder_version,
                scheduled_for_utc
            ) DO NOTHING
            """,
            _delivery_values(delivery),
        )
        if cursor.rowcount == 1:
            return replace(delivery, id=int(cursor.lastrowid)), True

        existing = self.get_occurrence(
            conn,
            reminder_id=delivery.reminder_id,
            reminder_version=delivery.reminder_version,
            scheduled_for_utc=delivery.scheduled_for_utc,
        )
        if existing is None:
            raise RuntimeError("Delivery occurrence conflict could not be resolved.")
        return existing, False

    def get(
        self,
        conn: sqlite3.Connection,
        delivery_id: int,
    ) -> Delivery | None:
        row = conn.execute(
            f"SELECT {_DELIVERY_COLUMNS} FROM deliveries WHERE id = ?",
            (delivery_id,),
        ).fetchone()
        return _row_to_delivery(row) if row is not None else None

    def get_occurrence(
        self,
        conn: sqlite3.Connection,
        *,
        reminder_id: int,
        reminder_version: int,
        scheduled_for_utc: datetime,
    ) -> Delivery | None:
        row = conn.execute(
            f"""
            SELECT {_DELIVERY_COLUMNS}
            FROM deliveries
            WHERE reminder_id = ?
              AND reminder_version = ?
              AND scheduled_for_utc = ?
            """,
            (
                reminder_id,
                reminder_version,
                scheduled_for_utc.isoformat(),
            ),
        ).fetchone()
        return _row_to_delivery(row) if row is not None else None

    def list_by_reminder(
        self,
        conn: sqlite3.Connection,
        reminder_id: int,
    ) -> list[Delivery]:
        rows = conn.execute(
            f"""
            SELECT {_DELIVERY_COLUMNS}
            FROM deliveries
            WHERE reminder_id = ?
            ORDER BY scheduled_for_utc, id
            """,
            (reminder_id,),
        ).fetchall()
        return [_row_to_delivery(row) for row in rows]

    def find_ready(
        self,
        conn: sqlite3.Connection,
        now_utc: datetime,
        *,
        limit: int = 100,
    ) -> list[Delivery]:
        rows = conn.execute(
            f"""
            SELECT {_DELIVERY_COLUMNS}
            FROM deliveries
            WHERE (
                    status IN (?, ?)
                    AND deliver_at_utc <= ?
                  )
               OR (
                    status = ?
                    AND lease_expires_at_utc <= ?
                  )
            ORDER BY deliver_at_utc, id
            LIMIT ?
            """,
            (
                DeliveryStatus.PENDING.value,
                DeliveryStatus.RETRY_WAIT.value,
                now_utc.isoformat(),
                DeliveryStatus.CLAIMED.value,
                now_utc.isoformat(),
                limit,
            ),
        ).fetchall()
        return [_row_to_delivery(row) for row in rows]

    def claim(
        self,
        conn: sqlite3.Connection,
        delivery_id: int,
        now_utc: datetime,
        *,
        claim_token: str,
        lease_expires_at_utc: datetime,
    ) -> bool:
        cursor = conn.execute(
            """
            UPDATE deliveries
            SET status = ?,
                attempt_count = attempt_count + 1,
                last_error = NULL,
                claim_token = ?,
                claimed_at_utc = ?,
                lease_expires_at_utc = ?,
                updated_at_utc = CURRENT_TIMESTAMP
            WHERE id = ?
              AND (
                    (status IN (?, ?) AND deliver_at_utc <= ?)
                    OR (status = ? AND lease_expires_at_utc <= ?)
                  )
            """,
            (
                DeliveryStatus.CLAIMED.value,
                claim_token,
                now_utc.isoformat(),
                lease_expires_at_utc.isoformat(),
                delivery_id,
                DeliveryStatus.PENDING.value,
                DeliveryStatus.RETRY_WAIT.value,
                now_utc.isoformat(),
                DeliveryStatus.CLAIMED.value,
                now_utc.isoformat(),
            ),
        )
        return cursor.rowcount == 1

    def update(
        self,
        conn: sqlite3.Connection,
        delivery: Delivery,
    ) -> bool:
        if delivery.id is None:
            raise ValueError("Cannot update a Delivery without an id.")
        if delivery.deliver_at_utc is None:
            raise ValueError("Delivery deliver_at_utc must be set.")

        cursor = conn.execute(
            """
            UPDATE deliveries
            SET reminder_version = ?,
                scheduled_for_utc = ?,
                deliver_at_utc = ?,
                status = ?,
                attempt_count = ?,
                last_error = ?,
                claim_token = ?,
                claimed_at_utc = ?,
                lease_expires_at_utc = ?,
                updated_at_utc = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                delivery.reminder_version,
                delivery.scheduled_for_utc.isoformat(),
                delivery.deliver_at_utc.isoformat(),
                delivery.status.value,
                delivery.attempt_count,
                delivery.last_error,
                delivery.claim_token,
                _iso(delivery.claimed_at_utc),
                _iso(delivery.lease_expires_at_utc),
                delivery.id,
            ),
        )
        return cursor.rowcount == 1


def _delivery_values(delivery: Delivery) -> tuple:
    if delivery.deliver_at_utc is None:
        raise ValueError("Delivery deliver_at_utc must be set.")
    return (
        delivery.user_id,
        delivery.reminder_id,
        delivery.reminder_version,
        delivery.scheduled_for_utc.isoformat(),
        delivery.deliver_at_utc.isoformat(),
        delivery.status.value,
        delivery.attempt_count,
        delivery.last_error,
    )


def _row_to_delivery(row: sqlite3.Row) -> Delivery:
    return Delivery(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        reminder_id=int(row["reminder_id"]),
        reminder_version=int(row["reminder_version"]),
        scheduled_for_utc=datetime.fromisoformat(row["scheduled_for_utc"]),
        deliver_at_utc=datetime.fromisoformat(row["deliver_at_utc"]),
        status=DeliveryStatus(row["status"]),
        attempt_count=int(row["attempt_count"]),
        last_error=row["last_error"],
        claim_token=row["claim_token"],
        claimed_at_utc=_datetime(row["claimed_at_utc"]),
        lease_expires_at_utc=_datetime(row["lease_expires_at_utc"]),
    )


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
