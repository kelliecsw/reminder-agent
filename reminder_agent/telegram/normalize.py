from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from telegram import Update


class TelegramInputKind(StrEnum):
    COMMAND = "command"
    CALLBACK = "callback"
    TEXT = "text"


@dataclass(frozen=True)
class TelegramInput:
    kind: TelegramInputKind
    telegram_user_id: int
    telegram_chat_id: int
    chat_type: str
    text: str | None = None
    callback_data: str | None = None


def normalize_update(update: Update) -> TelegramInput | None:
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return None

    if update.callback_query is not None:
        data = update.callback_query.data
        if not isinstance(data, str):
            return None
        return TelegramInput(
            kind=TelegramInputKind.CALLBACK,
            telegram_user_id=user.id,
            telegram_chat_id=chat.id,
            chat_type=chat.type,
            callback_data=data,
        )

    message = update.effective_message
    if message is None or not message.text:
        return None
    text = message.text.strip()
    if not text:
        return None
    return TelegramInput(
        kind=(
            TelegramInputKind.COMMAND
            if text.startswith("/")
            else TelegramInputKind.TEXT
        ),
        telegram_user_id=user.id,
        telegram_chat_id=chat.id,
        chat_type=chat.type,
        text=text,
    )


def callback_identity(
    update: Update,
    item: TelegramInput,
    *,
    fallback_update_id: int | None = None,
) -> str | None:
    """Identify one button on one Telegram message across callback retries."""
    if item.kind != TelegramInputKind.CALLBACK or item.callback_data is None:
        return None
    query = update.callback_query
    message = getattr(query, "message", None)
    message_id = getattr(message, "message_id", None)
    if message_id is not None:
        source = f"message:{message_id}"
    else:
        inline_message_id = getattr(query, "inline_message_id", None)
        if inline_message_id is not None:
            source = f"inline:{inline_message_id}"
        elif fallback_update_id is not None:
            source = f"update:{fallback_update_id}"
        else:
            return None
    return (
        f"telegram-callback:{item.telegram_chat_id}:{source}:"
        f"{item.callback_data}"
    )


__all__ = [
    "TelegramInput",
    "TelegramInputKind",
    "callback_identity",
    "normalize_update",
]
