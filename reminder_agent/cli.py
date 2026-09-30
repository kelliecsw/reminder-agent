from __future__ import annotations

import argparse
import asyncio
from datetime import date, time
import logging
from pathlib import Path
import shlex

from reminder_agent.db.database import Database
from reminder_agent.domain.enums import ReminderPurpose
from reminder_agent.domain.recurrence import ReminderSchedule
from reminder_agent.repositories.users import UserRepository
from reminder_agent.services.goals import GoalService
from reminder_agent.services.reminders import ReminderService
from reminder_agent.services.tasks import TaskService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reminder Agent development CLI")
    parser.add_argument("--db", default="reminder-agent.db")
    parser.add_argument("--user-timezone", default="UTC")
    subparsers = parser.add_subparsers(dest="command")

    create_goal = subparsers.add_parser("create-goal")
    create_goal.add_argument("title")

    create_task = subparsers.add_parser("create-task")
    create_task.add_argument("title")
    create_task.add_argument("--goal", type=int)

    remind_task = subparsers.add_parser("remind-task")
    remind_task.add_argument("task_id", type=int)
    remind_task.add_argument("date", type=date.fromisoformat)
    remind_task.add_argument("time", type=time.fromisoformat)
    remind_task.add_argument("--timezone")

    subparsers.add_parser("goals")
    subparsers.add_parser("tasks")
    subparsers.add_parser("reminders")

    chat = subparsers.add_parser("chat")
    chat.add_argument("--provider")
    chat.add_argument("--model")

    subparsers.add_parser("telegram")

    scheduler = subparsers.add_parser("scheduler")
    scheduler.add_argument(
        "--gateway",
        choices=("console", "telegram"),
        default="console",
    )
    scheduler.add_argument(
        "--once",
        action="store_true",
        help="Materialize and deliver one scheduler cycle, then exit.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    database = Database(Path(args.db))
    database.migrate()
    user_id = _ensure_dev_user(database, args.user_timezone)

    if args.command is None:
        _interactive(parser, database, user_id, args.db, args.user_timezone)
        return
    _run(args, database, user_id)


def _ensure_dev_user(database: Database, timezone: str) -> int:
    users = UserRepository()
    with database.transaction() as conn:
        if users.get_timezone(conn, 1) is not None:
            return 1
        return users.create(conn, timezone=timezone)


def _interactive(
    parser: argparse.ArgumentParser,
    database: Database,
    user_id: int,
    db_path: str,
    timezone: str,
) -> None:
    print("Development CLI. Type 'help' or 'quit'.")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in {"quit", "exit"}:
            return
        if line == "help":
            parser.print_help()
            continue
        try:
            args = parser.parse_args(
                ["--db", db_path, "--user-timezone", timezone, *shlex.split(line)]
            )
            _run(args, database, user_id)
        except SystemExit:
            continue
        except Exception as exc:
            print(f"error: {exc}")


def _run(args: argparse.Namespace, database: Database, user_id: int) -> None:
    if args.command == "chat":
        asyncio.run(_chat(database, user_id, provider=args.provider, model=args.model))
        return
    if args.command == "scheduler":
        try:
            asyncio.run(
                _run_scheduler(
                    database,
                    once=args.once,
                    gateway_name=args.gateway,
                )
            )
        except KeyboardInterrupt:
            print("\nScheduler stopped.")
        return
    if args.command == "telegram":
        _run_telegram(database)
        return

    goals = GoalService(database)
    tasks = TaskService(database)
    reminders = ReminderService(database)

    if args.command == "create-goal":
        print(goals.create(user_id, args.title))
    elif args.command == "create-task":
        print(tasks.create(user_id, args.title, goal_id=args.goal))
    elif args.command == "remind-task":
        timezone = args.timezone
        if timezone is None:
            with database.connect() as conn:
                timezone = UserRepository().get_timezone(conn, user_id)
        assert timezone is not None
        print(
            reminders.create(
                user_id,
                purpose=ReminderPurpose.TASK_REMINDER,
                task_id=args.task_id,
                schedule=ReminderSchedule(
                    start_date=args.date,
                    local_time=args.time,
                    timezone=timezone,
                ),
            )
        )
    elif args.command == "goals":
        for goal in goals.list(user_id):
            print(goal)
    elif args.command == "tasks":
        for task in tasks.list(user_id):
            print(task)
    elif args.command == "reminders":
        for reminder in reminders.list(user_id):
            print(reminder)


async def _run_scheduler(
    database: Database,
    *,
    once: bool,
    gateway_name: str,
) -> None:
    from reminder_agent.config import Settings
    from reminder_agent.scheduler.gateway import ConsoleGateway, MessageGateway
    from reminder_agent.scheduler.materializer import ReminderMaterializer
    from reminder_agent.scheduler.worker import DeliveryWorker
    from reminder_agent.services.deliveries import DeliveryService

    settings = Settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    gateway: MessageGateway
    telegram_gateway = None
    if gateway_name == "telegram":
        from reminder_agent.telegram.gateway import create_telegram_gateway

        telegram_gateway = create_telegram_gateway(database, settings)
        await telegram_gateway.initialize()
        gateway = telegram_gateway
    else:
        gateway = ConsoleGateway()

    deliveries = DeliveryService(database)
    materializer = ReminderMaterializer(
        deliveries,
        batch_size=settings.scheduler_batch_size,
    )
    worker = DeliveryWorker(
        deliveries,
        gateway,
        max_attempts=settings.delivery_max_attempts,
        retry_base_seconds=settings.delivery_retry_base_seconds,
    )

    print(
        f"Scheduler started with {gateway_name} delivery. Press Ctrl-C to stop.",
        flush=True,
    )
    try:
        while True:
            materializer.run_once()
            while await worker.run_once():
                pass
            if once:
                return
            await asyncio.sleep(settings.scheduler_poll_seconds)
    finally:
        if telegram_gateway is not None:
            await telegram_gateway.shutdown()


def _run_telegram(database: Database) -> None:
    from reminder_agent.config import Settings
    from reminder_agent.telegram.polling import run_telegram_polling

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    run_telegram_polling(database, Settings())


async def _chat(
    database: Database,
    user_id: int,
    *,
    provider: str | None,
    model: str | None,
) -> None:
    from langchain_core.messages import AIMessage, HumanMessage

    from reminder_agent.ai.graph import create_reminder_agent, initial_state
    from reminder_agent.ai.model import create_model
    from reminder_agent.ai.tools.registry import create_tool_registry
    from reminder_agent.clock import utc_now
    from reminder_agent.config import Settings
    from reminder_agent.services.users import UserService

    settings = Settings()
    updates = {}
    if provider is not None:
        updates["llm_provider"] = provider
    if model is not None:
        updates["llm_model"] = model
    if updates:
        settings = Settings.model_validate(
            {**settings.model_dump(), **updates}
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    tasks = TaskService(database)
    goals = GoalService(database)
    reminders = ReminderService(database)
    registry = create_tool_registry(
        tasks=tasks,
        goals=goals,
        reminders=reminders,
    )
    graph = create_reminder_agent(
        model=create_model(settings),
        registry=registry,
        clock=utc_now,
        retry_attempts=settings.llm_retry_attempts,
        log_private_content=settings.log_private_content,
    )
    timezone = UserService(database).get_timezone(user_id)
    history = []

    print(
        f"Chat using {settings.llm_provider}/{settings.llm_model or 'default'}. "
        "Type 'quit' to exit."
    )
    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if text.lower() in {"quit", "exit"}:
            return
        if not text:
            continue

        now = utc_now()
        result = await graph.ainvoke(
            initial_state(
                messages=[*history, HumanMessage(content=text)],
                user_id=user_id,
                timezone=timezone,
                now_utc=now,
                tool_round_limit=settings.llm_tool_round_limit,
            )
        )
        history = list(result["messages"])
        response = next(
            (
                message
                for message in reversed(history)
                if isinstance(message, AIMessage) and not message.tool_calls
            ),
            None,
        )
        if response is None:
            print("Bot: I could not produce a final response.")
        else:
            print(f"Bot: {_content_text(response.content)}")


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)
