from __future__ import annotations

import sqlite3


class UserRepository:
    """Persistence operations for the minimal V1 user record."""

    def create(
        self,
        conn: sqlite3.Connection,
        *,
        timezone: str,
    ) -> int:
        cursor = conn.execute(
            """
            INSERT INTO users (timezone)
            VALUES (?)
            """,
            (timezone,),
        )
        return int(cursor.lastrowid)

    def get_timezone(
        self,
        conn: sqlite3.Connection,
        user_id: int,
    ) -> str | None:
        row = conn.execute(
            """
            SELECT timezone
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

        if row is None:
            return None

        return str(row["timezone"])

    def update_timezone(
        self,
        conn: sqlite3.Connection,
        user_id: int,
        *,
        timezone: str,
    ) -> bool:
        cursor = conn.execute(
            """
            UPDATE users
            SET timezone = ?,
                updated_at_utc = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (timezone, user_id),
        )
        return cursor.rowcount == 1
