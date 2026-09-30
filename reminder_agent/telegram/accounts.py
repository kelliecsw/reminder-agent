from __future__ import annotations

from reminder_agent.db.database import Database
from reminder_agent.repositories.telegram_accounts import (
    TelegramAccount,
    TelegramAccountRepository,
)
from reminder_agent.repositories.users import UserRepository


class TelegramAuthorizationError(Exception):
    """Raised when a Telegram sender or chat is not authorized for this bot."""


class TelegramAccountService:
    def __init__(
        self,
        database: Database,
        *,
        owner_telegram_user_id: int,
        application_user_id: int,
        account_repo: TelegramAccountRepository | None = None,
        user_repo: UserRepository | None = None,
    ) -> None:
        self.database = database
        self.owner_telegram_user_id = owner_telegram_user_id
        self.application_user_id = application_user_id
        self.account_repo = account_repo or TelegramAccountRepository()
        self.user_repo = user_repo or UserRepository()

    def authenticate(
        self,
        *,
        telegram_user_id: int,
        telegram_chat_id: int,
        chat_type: str,
    ) -> int:
        if telegram_user_id != self.owner_telegram_user_id:
            raise TelegramAuthorizationError("This Telegram user is not authorized.")
        if chat_type != "private":
            raise TelegramAuthorizationError("Use this bot in a private chat.")

        with self.database.transaction(immediate=True) as conn:
            if self.user_repo.get_timezone(conn, self.application_user_id) is None:
                raise TelegramAuthorizationError(
                    f"Application user {self.application_user_id} does not exist."
                )

            by_telegram = self.account_repo.get_by_telegram_user_id(
                conn,
                telegram_user_id,
            )
            if by_telegram is not None:
                if by_telegram.user_id != self.application_user_id:
                    raise TelegramAuthorizationError(
                        "Telegram owner is linked to a different application user."
                    )
                if by_telegram.telegram_chat_id != telegram_chat_id:
                    self.account_repo.update_chat_id(
                        conn,
                        by_telegram.id,
                        telegram_chat_id=telegram_chat_id,
                    )
                return by_telegram.user_id

            by_user = self.account_repo.get_by_user_id(
                conn,
                self.application_user_id,
            )
            if by_user is not None:
                raise TelegramAuthorizationError(
                    "Application user is linked to a different Telegram user."
                )

            self.account_repo.create(
                conn,
                user_id=self.application_user_id,
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
            )
            return self.application_user_id

    def get_for_user(self, user_id: int) -> TelegramAccount | None:
        with self.database.connect() as conn:
            return self.account_repo.get_by_user_id(conn, user_id)


__all__ = ["TelegramAccountService", "TelegramAuthorizationError"]
