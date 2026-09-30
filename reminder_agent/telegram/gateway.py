from __future__ import annotations

from typing import Any

from telegram import Bot, InlineKeyboardMarkup

from reminder_agent.config import Settings
from reminder_agent.db.database import Database
from reminder_agent.domain.models import OutboxMessage
from reminder_agent.telegram.accounts import TelegramAccountService
from reminder_agent.telegram.views import reminder_actions


class TelegramGateway:
    def __init__(self, bot: Bot, accounts: TelegramAccountService) -> None:
        self.bot = bot
        self.accounts = accounts

    async def initialize(self) -> None:
        await self.bot.initialize()

    async def shutdown(self) -> None:
        await self.bot.shutdown()

    async def send(
        self,
        *,
        delivery_id: int,
        user_id: int,
        text: str,
    ) -> None:
        account = self.accounts.get_for_user(user_id)
        if account is None:
            raise RuntimeError(
                f"Application user {user_id} has no linked Telegram account."
            )
        await self.bot.send_message(
            chat_id=account.telegram_chat_id,
            text=f"🔔 {text}",
            reply_markup=reminder_actions(delivery_id),
        )

    async def send_outbox(self, item: OutboxMessage) -> int | None:
        account = self.accounts.get_for_user(item.application_user_id)
        if account is None:
            raise RuntimeError(
                f"Application user {item.application_user_id} has no linked Telegram account."
            )
        chat_id = item.telegram_chat_id
        if chat_id is None:
            chat_id = account.telegram_chat_id
        elif chat_id != account.telegram_chat_id:
            raise RuntimeError(
                f"Telegram chat {chat_id} is not linked to application user "
                f"{item.application_user_id}."
            )
        reply_markup = (
            InlineKeyboardMarkup.de_json(item.keyboard, self.bot)
            if item.keyboard is not None
            else None
        )
        if item.rich_message is not None:
            sent = await send_rich_message(
                self.bot,
                chat_id=chat_id,
                rich_message=item.rich_message,
                reply_markup=reply_markup,
            )
            return _message_id(sent)

        sent = await self.bot.send_message(
            chat_id=chat_id,
            text=item.text,
            reply_markup=reply_markup,
        )
        return getattr(sent, "message_id", None)


async def send_rich_message(
    bot: Bot,
    *,
    chat_id: int,
    rich_message: dict[str, Any],
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Any:
    """Call the Bot API method before python-telegram-bot wraps it."""
    api_kwargs: dict[str, Any] = {
        "chat_id": chat_id,
        "rich_message": rich_message,
    }
    if reply_markup is not None:
        api_kwargs["reply_markup"] = reply_markup
    return await bot.do_api_request(
        "sendRichMessage",
        api_kwargs=api_kwargs,
    )


def _message_id(sent: Any) -> int | None:
    if isinstance(sent, dict):
        value = sent.get("message_id")
    else:
        value = getattr(sent, "message_id", None)
    return int(value) if value is not None else None


def create_telegram_gateway(
    database: Database,
    settings: Settings,
) -> TelegramGateway:
    token, owner_user_id = require_telegram_settings(settings)
    accounts = TelegramAccountService(
        database,
        owner_telegram_user_id=owner_user_id,
        application_user_id=settings.telegram_application_user_id,
    )
    return TelegramGateway(Bot(token=token), accounts)


def require_telegram_settings(settings: Settings) -> tuple[str, int]:
    if settings.telegram_bot_token is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required.")
    if settings.telegram_owner_user_id is None:
        raise RuntimeError("TELEGRAM_OWNER_USER_ID is required.")
    return (
        settings.telegram_bot_token.get_secret_value(),
        settings.telegram_owner_user_id,
    )


__all__ = [
    "TelegramGateway",
    "create_telegram_gateway",
    "require_telegram_settings",
    "send_rich_message",
]
