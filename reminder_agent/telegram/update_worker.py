from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import datetime, timedelta
import logging
from time import perf_counter
from types import SimpleNamespace
from typing import Any

from telegram import Bot, InlineKeyboardMarkup, Update

from reminder_agent.clock import utc_now
from reminder_agent.telegram.outbox import OutboxService
from reminder_agent.telegram.normalize import (
    TelegramInputKind,
    callback_identity,
    normalize_update,
)
from reminder_agent.telegram.processor import TelegramProcessor
from reminder_agent.telegram.updates import TelegramUpdateService


logger = logging.getLogger(__name__)


class QueuedReplyBot:
    def __init__(
        self,
        *,
        outbox: OutboxService,
        reply_scope: str,
        application_user_id: int,
        clock: Callable[[], datetime],
    ) -> None:
        self.outbox = outbox
        self.reply_scope = reply_scope
        self.application_user_id = application_user_id
        self.clock = clock
        self.sequence = 0

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        **_: Any,
    ) -> Any:
        self.sequence += 1
        item, created = self.outbox.enqueue(
            dedupe_key=f"{self.reply_scope}:reply:{self.sequence}",
            application_user_id=self.application_user_id,
            telegram_chat_id=chat_id,
            text=text,
            keyboard=reply_markup,
            now_utc=self.clock(),
        )
        logger.info(
            "[TELEGRAM META] event=reply_queued source=%s outbox_id=%s "
            "sequence=%s created=%s has_keyboard=%s",
            self.reply_scope,
            item.id,
            self.sequence,
            created,
            reply_markup is not None,
        )
        return SimpleNamespace(
            message_id=item.id,
            outbox_id=item.id,
            queued=True,
        )

    async def enqueue_rich_message(
        self,
        *,
        chat_id: int,
        text: str,
        rich_message: dict[str, Any],
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Any:
        self.sequence += 1
        item, created = self.outbox.enqueue(
            dedupe_key=f"{self.reply_scope}:reply:{self.sequence}",
            application_user_id=self.application_user_id,
            telegram_chat_id=chat_id,
            text=text,
            keyboard=reply_markup,
            rich_message=rich_message,
            now_utc=self.clock(),
        )
        logger.info(
            "[TELEGRAM META] event=reply_queued source=%s outbox_id=%s "
            "sequence=%s created=%s has_keyboard=%s is_rich=true",
            self.reply_scope,
            item.id,
            self.sequence,
            created,
            reply_markup is not None,
        )
        return SimpleNamespace(
            message_id=item.id,
            outbox_id=item.id,
            queued=True,
        )


class TelegramUpdateWorker:
    def __init__(
        self,
        updates: TelegramUpdateService,
        outbox: OutboxService,
        processor: TelegramProcessor,
        bot: Bot,
        *,
        clock: Callable[[], datetime] = utc_now,
        lease_seconds: float = 60.0,
        max_attempts: int = 3,
        retry_base_seconds: float = 5.0,
        kinds: Collection[TelegramInputKind] | None = None,
    ) -> None:
        if lease_seconds <= 0 or retry_base_seconds <= 0:
            raise ValueError("Worker timing settings must be positive.")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        self.updates = updates
        self.outbox = outbox
        self.processor = processor
        self.bot = bot
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.kinds = frozenset(kinds) if kinds is not None else None
        if self.kinds is not None and not self.kinds:
            raise ValueError("kinds must contain at least one update kind.")

    async def run_once(self, now_utc: datetime | None = None) -> bool:
        now_utc = now_utc or self.clock()
        item = self.updates.claim_ready(
            now_utc,
            lease_seconds=self.lease_seconds,
            kinds=self.kinds,
        )
        if item is None:
            return False

        started = perf_counter()
        queued_bot = None
        logger.info(
            "[TELEGRAM META] event=processing_started update_id=%s kind=%s "
            "attempt=%s",
            item.update_id,
            item.update_kind,
            item.attempt_count,
        )
        try:
            update = Update.de_json(item.payload, self.bot)
            reply_scope = f"update:{item.update_id}"
            normalized = normalize_update(update)
            if normalized is not None:
                identity = callback_identity(
                    update,
                    normalized,
                    fallback_update_id=item.update_id,
                )
                if identity is not None:
                    reply_scope = identity
            queued_bot = QueuedReplyBot(
                outbox=self.outbox,
                reply_scope=reply_scope,
                application_user_id=item.application_user_id,
                clock=self.clock,
            )
            context = SimpleNamespace(bot=queued_bot, callback_acknowledged=True)
            await self.processor.handle(update, context)
        except Exception as exc:
            retry_at = now_utc + timedelta(
                seconds=self.retry_base_seconds * (2 ** (item.attempt_count - 1))
            )
            status = self.updates.retry(
                item,
                now_utc=now_utc,
                retry_at_utc=retry_at,
                error=str(exc),
                max_attempts=self.max_attempts,
            )
            logger.warning(
                "[TELEGRAM META] event=processing_failed update_id=%s kind=%s "
                "attempt=%s status=%s retry_at=%s duration_ms=%s error=%r",
                item.update_id,
                item.update_kind,
                item.attempt_count,
                status.value,
                retry_at.isoformat(),
                round((perf_counter() - started) * 1000),
                str(exc),
            )
            return True

        self.updates.complete(item, now_utc)
        logger.info(
            "[TELEGRAM META] event=processing_completed update_id=%s kind=%s "
            "attempt=%s replies=%s duration_ms=%s",
            item.update_id,
            item.update_kind,
            item.attempt_count,
            queued_bot.sequence if queued_bot is not None else 0,
            round((perf_counter() - started) * 1000),
        )
        return True


__all__ = ["QueuedReplyBot", "TelegramUpdateWorker"]
