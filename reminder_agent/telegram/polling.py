from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

from reminder_agent.ai.graph import create_reminder_agent
from reminder_agent.ai.model import create_model
from reminder_agent.ai.tools.registry import create_tool_registry
from reminder_agent.config import Settings
from reminder_agent.db.database import Database
from reminder_agent.services.delivery_actions import DeliveryActionService
from reminder_agent.services.deliveries import DeliveryService
from reminder_agent.services.goals import GoalService
from reminder_agent.services.reminders import ReminderService
from reminder_agent.services.tasks import TaskService
from reminder_agent.services.agent_threads import AgentThreadService
from reminder_agent.services.users import UserService
from reminder_agent.telegram.accounts import TelegramAccountService
from reminder_agent.telegram.gateway import require_telegram_settings
from reminder_agent.telegram.processor import TelegramProcessor
from reminder_agent.clock import utc_now
from reminder_agent.telegram.normalize import normalize_update
from reminder_agent.telegram.updates import TelegramUpdateService
from reminder_agent.telegram.accounts import TelegramAuthorizationError


logger = logging.getLogger(__name__)


def run_telegram_polling(database: Database, settings: Settings) -> None:
    token, owner_user_id = require_telegram_settings(settings)
    tasks = TaskService(database)
    goals = GoalService(database)
    reminders = ReminderService(database)
    deliveries = DeliveryService(database)
    threads = AgentThreadService(database)
    graph = create_reminder_agent(
        settings=settings,
        model=create_model(settings),
        registry=create_tool_registry(
            tasks=tasks,
            goals=goals,
            reminders=reminders,
        ),
        thread_service=threads,
    )
    processor = TelegramProcessor(
        accounts=TelegramAccountService(
            database,
            owner_telegram_user_id=owner_user_id,
            application_user_id=settings.telegram_application_user_id,
        ),
        users=UserService(database),
        tasks=tasks,
        goals=goals,
        reminders=reminders,
        deliveries=deliveries,
        delivery_actions=DeliveryActionService(database),
        graph=graph,
        tool_round_limit=settings.llm_tool_round_limit,
        thread_service=threads,
        log_private_content=settings.log_private_content,
    )

    application = build_telegram_application(settings, processor.handle)
    logger.info("Starting Telegram long polling.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


class TelegramUpdateIntake:
    def __init__(
        self,
        updates: TelegramUpdateService,
        accounts: TelegramAccountService,
    ) -> None:
        self.updates = updates
        self.accounts = accounts

    async def handle(self, update: Update, context) -> None:
        item = normalize_update(update)
        if item is None:
            return
        try:
            user_id = self.accounts.authenticate(
                telegram_user_id=item.telegram_user_id,
                telegram_chat_id=item.telegram_chat_id,
                chat_type=item.chat_type,
            )
        except TelegramAuthorizationError as exc:
            logger.warning(
                "[TELEGRAM META] event=intake_rejected update_id=%s kind=%s "
                "telegram_user_id=%s telegram_chat_id=%s error=%r",
                update.update_id,
                item.kind.value,
                item.telegram_user_id,
                item.telegram_chat_id,
                str(exc),
            )
            if update.callback_query is not None:
                await update.callback_query.answer(str(exc), show_alert=True)
            else:
                await context.bot.send_message(
                    chat_id=item.telegram_chat_id,
                    text=str(exc),
                )
            return

        created = self.updates.receive(
            update_id=update.update_id,
            item=item,
            application_user_id=user_id,
            payload=update.to_dict(),
            now_utc=utc_now(),
        )
        if update.callback_query is not None:
            await update.callback_query.answer()
        logger.info(
            "[TELEGRAM META] event=update_received update_id=%s kind=%s created=%s",
            update.update_id,
            item.kind.value,
            created,
        )


def build_telegram_application(settings: Settings, handler) -> Application:
    token, _ = require_telegram_settings(settings)
    application = Application.builder().token(token).build()
    application.add_handler(CallbackQueryHandler(handler))
    application.add_handler(MessageHandler(filters.TEXT, handler))
    application.add_error_handler(_log_error)
    return application


async def _log_error(update, context) -> None:
    error = context.error
    logger.error(
        "Unhandled Telegram polling error for update %s.",
        getattr(update, "update_id", None),
        exc_info=(type(error), error, error.__traceback__),
    )


__all__ = [
    "TelegramUpdateIntake",
    "build_telegram_application",
    "run_telegram_polling",
]
