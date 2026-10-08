from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler
import signal
import sys
from contextlib import suppress

from telegram import Update

from reminder_agent.ai.graph import create_reminder_agent
from reminder_agent.ai.model import create_model
from reminder_agent.ai.tools.registry import create_tool_registry
from reminder_agent.config import Settings
from reminder_agent.db.database import Database
from reminder_agent.scheduler.materializer import ReminderMaterializer
from reminder_agent.scheduler.worker import DeliveryDispatcher
from reminder_agent.services.agent_threads import AgentThreadService
from reminder_agent.services.deliveries import DeliveryService
from reminder_agent.services.delivery_actions import DeliveryActionService
from reminder_agent.services.goals import GoalService
from reminder_agent.services.reminders import ReminderService
from reminder_agent.services.tasks import TaskService
from reminder_agent.services.tool_executions import ToolExecutionService
from reminder_agent.services.users import UserService
from reminder_agent.telegram.accounts import TelegramAccountService
from reminder_agent.telegram.gateway import TelegramGateway, require_telegram_settings
from reminder_agent.telegram.outbox import OutboxService
from reminder_agent.telegram.outbox_worker import OutboxWorker
from reminder_agent.telegram.normalize import TelegramInputKind
from reminder_agent.telegram.polling import (
    TelegramUpdateIntake,
    build_telegram_application,
)
from reminder_agent.telegram.processor import TelegramProcessor
from reminder_agent.telegram.update_worker import TelegramUpdateWorker
from reminder_agent.telegram.updates import TelegramUpdateService


logger = logging.getLogger(__name__)


def main() -> None:
    settings = Settings()
    _configure_logging(settings)
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass


async def run(settings: Settings) -> None:
    _, owner_user_id = require_telegram_settings(settings)
    database = Database(settings.database_path)
    database.migrate()
    _ensure_application_user(database, settings)

    accounts = TelegramAccountService(
        database,
        owner_telegram_user_id=owner_user_id,
        application_user_id=settings.telegram_application_user_id,
    )
    updates = TelegramUpdateService(database)
    outbox = OutboxService(database)
    threads = AgentThreadService(database)
    tool_executions = ToolExecutionService(database)
    tasks = TaskService(database)
    goals = GoalService(database)
    reminders = ReminderService(database)
    deliveries = DeliveryService(database)

    registry = create_tool_registry(
        tasks=tasks,
        goals=goals,
        reminders=reminders,
        execution_service=tool_executions,
    )
    model = create_model(settings)
    graph = create_reminder_agent(
        settings=settings,
        model=model,
        registry=registry,
        thread_service=threads,
    )
    processor = TelegramProcessor(
        accounts=accounts,
        users=UserService(database),
        tasks=tasks,
        goals=goals,
        reminders=reminders,
        deliveries=deliveries,
        delivery_actions=DeliveryActionService(database),
        graph=graph,
        tool_round_limit=settings.llm_tool_round_limit,
        thread_service=threads,
        raise_unexpected=True,
        tool_execution_service=tool_executions,
        log_private_content=settings.log_private_content,
    )
    intake = TelegramUpdateIntake(updates, accounts)
    application = build_telegram_application(settings, intake.handle)
    gateway = TelegramGateway(application.bot, accounts)
    conversation_worker = TelegramUpdateWorker(
        updates,
        outbox,
        processor,
        application.bot,
        lease_seconds=settings.worker_lease_seconds,
        max_attempts=settings.update_max_attempts,
        retry_base_seconds=settings.update_retry_base_seconds,
        kinds={TelegramInputKind.TEXT},
    )
    interaction_worker = TelegramUpdateWorker(
        updates,
        outbox,
        processor,
        application.bot,
        lease_seconds=settings.worker_lease_seconds,
        max_attempts=settings.update_max_attempts,
        retry_base_seconds=settings.update_retry_base_seconds,
        kinds={TelegramInputKind.CALLBACK, TelegramInputKind.COMMAND},
    )
    materializer = ReminderMaterializer(
        deliveries,
        batch_size=settings.scheduler_batch_size,
    )
    dispatcher = DeliveryDispatcher(
        deliveries,
        outbox,
        lease_seconds=settings.worker_lease_seconds,
    )
    outbox_worker = OutboxWorker(
        outbox,
        gateway,
        lease_seconds=settings.worker_lease_seconds,
        max_attempts=settings.delivery_max_attempts,
        retry_base_seconds=settings.delivery_retry_base_seconds,
        log_private_content=settings.log_private_content,
    )

    await application.initialize()
    if application.updater is None:
        raise RuntimeError("Telegram Application has no updater.")
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    await application.start()
    logger.info(
        "Reminder Agent started. log_private_content=%s.",
        settings.log_private_content,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals = []
    for name in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(name, stop.set)
            installed_signals.append(name)

    workers = [
        asyncio.create_task(
            _drain_loop(conversation_worker.run_once, settings.update_poll_seconds),
            name="telegram-conversation-worker",
        ),
        asyncio.create_task(
            _drain_loop(interaction_worker.run_once, settings.update_poll_seconds),
            name="telegram-interaction-worker",
        ),
        asyncio.create_task(
            _scheduler_loop(
                materializer,
                dispatcher,
                settings.scheduler_poll_seconds,
            ),
            name="scheduler",
        ),
        asyncio.create_task(
            _drain_loop(outbox_worker.run_once, settings.outbox_poll_seconds),
            name="telegram-outbox-worker",
        ),
    ]
    stop_waiter = asyncio.create_task(stop.wait(), name="shutdown-signal")

    failure: BaseException | None = None
    try:
        failure = await _wait_for_stop_or_failure(workers, stop_waiter)
    finally:
        stop_waiter.cancel()
        for task in workers:
            task.cancel()
        with suppress(asyncio.CancelledError):
            await stop_waiter
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                asyncio.gather(*workers, return_exceptions=True),
                timeout=settings.shutdown_timeout_seconds,
            )
        for name in installed_signals:
            loop.remove_signal_handler(name)
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        logger.info("Reminder Agent stopped.")

    if failure is not None:
        raise failure


async def _wait_for_stop_or_failure(
    workers: list[asyncio.Task],
    stop_waiter: asyncio.Task,
) -> BaseException | None:
    done, _ = await asyncio.wait(
        [*workers, stop_waiter],
        return_when=asyncio.FIRST_COMPLETED,
    )
    if stop_waiter in done:
        return None
    task = next(task for task in done if task is not stop_waiter)
    try:
        task.result()
    except BaseException as exc:
        return exc
    return RuntimeError(f"Critical task {task.get_name()} exited unexpectedly.")


async def _drain_loop(run_once, poll_seconds: float) -> None:
    while True:
        worked = await run_once()
        if not worked:
            await asyncio.sleep(poll_seconds)


async def _scheduler_loop(
    materializer: ReminderMaterializer,
    dispatcher: DeliveryDispatcher,
    poll_seconds: float,
) -> None:
    while True:
        materializer.run_once()
        while await dispatcher.run_once():
            pass
        await asyncio.sleep(poll_seconds)


def _ensure_application_user(database: Database, settings: Settings) -> None:
    with database.transaction() as conn:
        conn.execute(
            """
            INSERT INTO users (id, timezone)
            VALUES (?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                settings.telegram_application_user_id,
                settings.default_timezone,
            ),
        )


def _configure_logging(settings: Settings) -> None:
    handlers = _build_logging_handlers(settings)
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _build_logging_handlers(settings: Settings) -> list[logging.Handler]:
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    return [
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            settings.log_file,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
    ]


if __name__ == "__main__":
    main()


__all__ = ["main", "run"]
