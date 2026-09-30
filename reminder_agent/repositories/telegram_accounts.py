from __future__ import annotations

from dataclasses import dataclass
import sqlite3


@dataclass(frozen=True)
class TelegramAccount:
    id: int
    user_id: int
    telegram_user_id: int
    telegram_chat_id: int


class TelegramAccountRepository:
    def create(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: int,
        telegram_user_id: int,
        telegram_chat_id: int,
    ) -> TelegramAccount:
        cursor = conn.execute(
            """
            INSERT INTO telegram_accounts (
                user_id,
                telegram_user_id,
                telegram_chat_id
            )
            VALUES (?, ?, ?)
            """,
            (user_id, telegram_user_id, telegram_chat_id),
        )
        return TelegramAccount(
            id=int(cursor.lastrowid),
            user_id=user_id,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
        )

    def get_by_user_id(
        self,
        conn: sqlite3.Connection,
        user_id: int,
    ) -> TelegramAccount | None:
        row = conn.execute(
            """
            SELECT id, user_id, telegram_user_id, telegram_chat_id
            FROM telegram_accounts
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        return _row_to_account(row) if row is not None else None

    def get_by_telegram_user_id(
        self,
        conn: sqlite3.Connection,
        telegram_user_id: int,
    ) -> TelegramAccount | None:
        row = conn.execute(
            """
            SELECT id, user_id, telegram_user_id, telegram_chat_id
            FROM telegram_accounts
            WHERE telegram_user_id = ?
            """,
            (telegram_user_id,),
        ).fetchone()
        return _row_to_account(row) if row is not None else None

    def update_chat_id(
        self,
        conn: sqlite3.Connection,
        account_id: int,
        *,
        telegram_chat_id: int,
    ) -> bool:
        cursor = conn.execute(
            """
            UPDATE telegram_accounts
            SET telegram_chat_id = ?,
                updated_at_utc = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (telegram_chat_id, account_id),
        )
        return cursor.rowcount == 1


def _row_to_account(row: sqlite3.Row) -> TelegramAccount:
    return TelegramAccount(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        telegram_user_id=int(row["telegram_user_id"]),
        telegram_chat_id=int(row["telegram_chat_id"]),
    )
