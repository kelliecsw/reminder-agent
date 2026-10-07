from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage
from telegram import Update

from reminder_agent.ai.graph import initial_state
from reminder_agent.clock import utc_now
from reminder_agent.domain.enums import (
    GoalStatus,
    ReminderPurpose,
    ReminderStatus,
    TaskStatus,
)
from reminder_agent.domain.errors import ApplicationError, DomainError
from reminder_agent.domain.models import Reminder
from reminder_agent.log_values import log_interaction, private_text
from reminder_agent.services.delivery_actions import DeliveryActionService
from reminder_agent.services.deliveries import DeliveryService
from reminder_agent.services.goals import GoalService
from reminder_agent.services.reminders import ReminderService
from reminder_agent.services.tasks import TaskService
from reminder_agent.services.users import UserService
from reminder_agent.services.agent_threads import AgentThreadService
from reminder_agent.services.tool_executions import ToolExecutionService
from reminder_agent.telegram.accounts import (
    TelegramAccountService,
    TelegramAuthorizationError,
)
from reminder_agent.telegram.formatting import render_markdown
from reminder_agent.telegram.gateway import send_rich_message
from reminder_agent.telegram.normalize import (
    TelegramInput,
    TelegramInputKind,
    callback_identity,
    normalize_update,
)
from reminder_agent.telegram.views import (
    format_goals,
    format_reminders,
    format_tasks,
    reminder_list_actions,
    snooze_actions,
)


logger = logging.getLogger(__name__)
interaction_logger = logging.getLogger("reminder_agent.telegram.interaction")


class TelegramProcessor:
    def __init__(
        self,
        *,
        accounts: TelegramAccountService,
        users: UserService,
        tasks: TaskService,
        goals: GoalService,
        reminders: ReminderService,
        deliveries: DeliveryService,
        delivery_actions: DeliveryActionService,
        graph: Any,
        tool_round_limit: int,
        clock: Callable[[], datetime] = utc_now,
        thread_service: AgentThreadService | None = None,
        raise_unexpected: bool = False,
        tool_execution_service: ToolExecutionService | None = None,
        log_private_content: bool = False,
    ) -> None:
        self.accounts = accounts
        self.users = users
        self.tasks = tasks
        self.goals = goals
        self.reminders = reminders
        self.deliveries = deliveries
        self.delivery_actions = delivery_actions
        self.graph = graph
        self.tool_round_limit = tool_round_limit
        self.clock = clock
        self.thread_service = thread_service
        self.raise_unexpected = raise_unexpected
        self.tool_execution_service = tool_execution_service
        self.log_private_content = log_private_content
        self.histories: dict[int, list[Any]] = {}

    async def handle(self, update: Update, context: Any) -> None:
        normalized = normalize_update(update)
        if normalized is None:
            return
        update_id = getattr(update, "update_id", None)

        try:
            user_id = self.accounts.authenticate(
                telegram_user_id=normalized.telegram_user_id,
                telegram_chat_id=normalized.telegram_chat_id,
                chat_type=normalized.chat_type,
            )
        except TelegramAuthorizationError as exc:
            logger.warning(
                "[TELEGRAM META] event=authorization_failed update_id=%s kind=%s "
                "telegram_user_id=%s telegram_chat_id=%s error=%r",
                update_id,
                normalized.kind.value,
                normalized.telegram_user_id,
                normalized.telegram_chat_id,
                str(exc),
            )
            if update.callback_query is not None:
                await update.callback_query.answer(str(exc), show_alert=True)
            else:
                await context.bot.send_message(
                    chat_id=normalized.telegram_chat_id,
                    text=str(exc),
                )
            return

        if normalized.kind == TelegramInputKind.TEXT:
            self._log_text_input(normalized, user_id, update_id=update_id)
        try:
            if normalized.kind == TelegramInputKind.CALLBACK:
                await self._handle_callback(
                    update,
                    context,
                    normalized,
                    user_id,
                    update_id=update_id,
                )
            elif normalized.kind == TelegramInputKind.COMMAND:
                await self._handle_command(
                    context,
                    normalized,
                    user_id,
                    update_id=update_id,
                )
            else:
                await self._handle_text(
                    context,
                    normalized,
                    user_id,
                    update_id=update_id,
                )
        except (ApplicationError, DomainError, ValueError) as exc:
            logger.info(
                "[TELEGRAM META] event=action_rejected update_id=%s kind=%s "
                "error=%r",
                update_id,
                normalized.kind.value,
                str(exc),
            )
            await self._reply(
                context,
                normalized,
                f"I couldn't do that: {exc}",
                update_id=update_id,
            )
        except Exception:
            logger.exception(
                "[TELEGRAM META] event=processing_exception update_id=%s kind=%s",
                update_id,
                normalized.kind.value,
            )
            if self.raise_unexpected:
                raise
            await self._reply(
                context,
                normalized,
                "Something went wrong while processing that request.",
                update_id=update_id,
            )

    async def _handle_command(
        self,
        context: Any,
        item: TelegramInput,
        user_id: int,
        *,
        update_id: int | None,
    ) -> None:
        assert item.text is not None
        parts = item.text.split()
        command = parts[0].split("@", 1)[0].lower()

        if command in {"/start", "/help"}:
            self._log_command_result(
                item,
                user_id,
                update_id,
                command,
                outcome="help",
            )
            await self._reply(
                context,
                item,
                _HELP_TEXT,
                update_id=update_id,
            )
            return
        if command == "/tasks":
            tasks = [
                task
                for task in self.tasks.list(user_id)
                if task.status not in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
            ]
            self._log_command_result(
                item,
                user_id,
                update_id,
                command,
                outcome="listed",
                result_count=len(tasks),
            )
            await self._reply(
                context,
                item,
                format_tasks(tasks),
                update_id=update_id,
            )
            return
        if command == "/goals":
            goals = [
                goal
                for goal in self.goals.list(user_id)
                if goal.status not in {GoalStatus.COMPLETED, GoalStatus.CANCELLED}
            ]
            self._log_command_result(
                item,
                user_id,
                update_id,
                command,
                outcome="listed",
                result_count=len(goals),
            )
            await self._reply(
                context,
                item,
                format_goals(goals),
                update_id=update_id,
            )
            return
        if command == "/reminders":
            reminders = [
                reminder
                for reminder in self.reminders.list(user_id)
                if reminder.status
                in {ReminderStatus.ACTIVE, ReminderStatus.PAUSED}
            ]
            self._log_command_result(
                item,
                user_id,
                update_id,
                command,
                outcome="listed",
                result_count=len(reminders),
            )
            await self._reply(
                context,
                item,
                format_reminders(
                    reminders,
                    target_titles=self._reminder_titles(user_id, reminders),
                ),
                reply_markup=reminder_list_actions(reminders),
                update_id=update_id,
            )
            return
        if command == "/today":
            timezone = ZoneInfo(self.users.get_timezone(user_id))
            today = self.clock().astimezone(timezone).date()
            reminders = [
                reminder
                for reminder in self.reminders.list(user_id)
                if reminder.status == ReminderStatus.ACTIVE
                and reminder.next_fire_at_utc is not None
                and reminder.next_fire_at_utc.astimezone(timezone).date() == today
            ]
            self._log_command_result(
                item,
                user_id,
                update_id,
                command,
                outcome="listed",
                result_count=len(reminders),
            )
            await self._reply(
                context,
                item,
                format_reminders(
                    reminders,
                    target_titles=self._reminder_titles(user_id, reminders),
                ),
                update_id=update_id,
            )
            return
        if command == "/timezone":
            if len(parts) == 1:
                self._log_command_result(
                    item,
                    user_id,
                    update_id,
                    command,
                    outcome="shown",
                )
                await self._reply(
                    context,
                    item,
                    f"Timezone: {self.users.get_timezone(user_id)}",
                    update_id=update_id,
                )
            else:
                timezone = self.users.update_timezone(user_id, parts[1])
                self._log_command_result(
                    item,
                    user_id,
                    update_id,
                    command,
                    outcome="updated",
                )
                await self._reply(
                    context,
                    item,
                    f"Timezone updated to {timezone}.",
                    update_id=update_id,
                )
            return

        self._log_command_result(
            item,
            user_id,
            update_id,
            command,
            outcome="unknown",
        )
        await self._reply(
            context,
            item,
            "Unknown command. Use /help for options.",
            update_id=update_id,
        )

    async def _handle_callback(
        self,
        update: Update,
        context: Any,
        item: TelegramInput,
        user_id: int,
        *,
        update_id: int | None,
    ) -> None:
        query = update.callback_query
        assert query is not None
        assert item.callback_data is not None
        if not getattr(context, "callback_acknowledged", False):
            await query.answer()

        parts = item.callback_data.split(":")
        if len(parts) != 3 or not parts[1].isdigit():
            raise ValueError("Invalid button action.")
        resource, raw_id, action = parts
        resource_id = int(raw_id)

        if resource == "d" and action == "s":
            self._log_callback_result(
                item,
                user_id,
                update_id,
                resource,
                resource_id,
                action,
                identity=callback_identity(
                    update,
                    item,
                    fallback_update_id=update_id,
                ),
                outcome="prompted_for_duration",
            )
            await self._reply(
                context,
                item,
                "Snooze for how long?",
                reply_markup=snooze_actions(resource_id),
                update_id=update_id,
            )
            await self._clear_buttons(query)
            return

        def mutate() -> dict[str, str]:
            if resource == "r" and action == "resume":
                self.reminders.resume(user_id, resource_id)
                return {"response": f"Resumed reminder #{resource_id}."}
            if resource != "d":
                raise ValueError("Invalid button resource.")
            if action == "done":
                result = self.delivery_actions.done(user_id, resource_id)
                response = (
                    "Marked this reminder occurrence done and recorded goal progress."
                    if result.reminder_purpose == ReminderPurpose.GOAL_REMINDER
                    else "Marked this reminder occurrence done."
                )
                return {"response": response}
            if action == "skip":
                self.deliveries.skip(user_id, resource_id)
                return {"response": "Skipped this reminder occurrence."}
            if action == "pause":
                self.delivery_actions.pause_reminder_from_delivery(
                    user_id,
                    resource_id,
                )
                return {"response": "Paused the reminder."}
            if action in {"s10", "s60", "sday"}:
                delay = {
                    "s10": timedelta(minutes=10),
                    "s60": timedelta(hours=1),
                    "sday": timedelta(days=1),
                }[action]
                snoozed_until = self.clock() + delay
                self.deliveries.snooze(user_id, resource_id, snoozed_until)
                return {"response": f"Snoozed until {snoozed_until.isoformat()}."}
            raise ValueError("Unknown button action.")

        identity = callback_identity(
            update,
            item,
            fallback_update_id=update_id,
        )
        if self.tool_execution_service is not None and identity is not None:
            result = self.tool_execution_service.execute(
                request_id=identity,
                tool_call_id="execute",
                tool_name="telegram_callback",
                arguments={"callback_data": item.callback_data},
                now_utc=self.clock(),
                operation=mutate,
            )
        else:
            result = mutate()
        response = result["response"]

        self._log_callback_result(
            item,
            user_id,
            update_id,
            resource,
            resource_id,
            action,
            identity=identity,
            outcome="success",
        )
        await self._clear_buttons(query)
        await self._reply(
            context,
            item,
            response,
            update_id=update_id,
        )

    async def _handle_text(
        self,
        context: Any,
        item: TelegramInput,
        user_id: int,
        *,
        update_id: int | None,
    ) -> None:
        assert item.text is not None
        now = self.clock()
        thread_version = None
        if self.thread_service is None:
            history = self.histories.get(user_id, [])
        else:
            loaded = self.thread_service.load(user_id)
            history = loaded.messages
            thread_version = loaded.version
            source = str(update_id) if update_id is not None else None
            existing_index = next(
                (
                    index
                    for index, message in enumerate(history)
                    if isinstance(message, HumanMessage)
                    and message.additional_kwargs.get("telegram_update_id") == source
                ),
                None,
            )
            if existing_index is not None:
                response = next(
                    (
                        message
                        for message in reversed(history[existing_index + 1 :])
                        if isinstance(message, AIMessage) and not message.tool_calls
                    ),
                    None,
                )
                if response is not None:
                    await self._reply(
                        context,
                        item,
                        _content_text(response.content),
                        update_id=update_id,
                    )
                    return
            else:
                human = HumanMessage(
                    content=item.text,
                    additional_kwargs={"telegram_update_id": source},
                )
                saved = self.thread_service.save(
                    user_id,
                    [*history, human],
                    expected_version=thread_version,
                    now_utc=now,
                )
                history = saved.messages
                thread_version = saved.version
        result = await self.graph.ainvoke(
            initial_state(
                messages=(
                    [*history, HumanMessage(content=item.text)]
                    if self.thread_service is None
                    else history
                ),
                user_id=user_id,
                timezone=self.users.get_timezone(user_id),
                now_utc=now,
                tool_round_limit=self.tool_round_limit,
                request_id=(
                    f"telegram:{update_id}" if update_id is not None else None
                ),
                thread_version=thread_version,
            )
        )
        messages = list(result["messages"])
        if self.thread_service is None:
            self.histories[user_id] = messages
        response = next(
            (
                message
                for message in reversed(messages)
                if isinstance(message, AIMessage) and not message.tool_calls
            ),
            None,
        )
        markdown = (
            _content_text(response.content)
            if response is not None
            else "I could not produce a response."
        )
        await self._reply(
            context,
            item,
            markdown,
            update_id=update_id,
            rich_message=render_markdown(markdown),
        )

    @staticmethod
    async def _clear_buttons(query: Any) -> None:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("Could not clear Telegram callback buttons.", exc_info=True)

    async def _reply(
        self,
        context: Any,
        item: TelegramInput,
        text: str,
        *,
        update_id: int | None,
        reply_markup: Any | None = None,
        rich_message: dict[str, Any] | None = None,
    ) -> None:
        enqueue_rich = getattr(context.bot, "enqueue_rich_message", None)
        if rich_message is not None and enqueue_rich is not None:
            sent = await enqueue_rich(
                chat_id=item.telegram_chat_id,
                text=text,
                rich_message=rich_message,
                reply_markup=reply_markup,
            )
        elif rich_message is not None:
            sent = await send_rich_message(
                context.bot,
                chat_id=item.telegram_chat_id,
                rich_message=rich_message,
                reply_markup=reply_markup,
            )
        else:
            sent = await context.bot.send_message(
                chat_id=item.telegram_chat_id,
                text=text,
                reply_markup=reply_markup,
            )
        if not getattr(sent, "queued", False):
            telegram_message_id = (
                sent.get("message_id")
                if isinstance(sent, dict)
                else getattr(sent, "message_id", None)
            )
            log_interaction(
                interaction_logger,
                heading="APP REPLY",
                content=private_text(
                    text,
                    enabled=self.log_private_content,
                ),
                metadata=(
                    f"update_id={update_id} chat_id={item.telegram_chat_id} "
                    f"telegram_message_id={telegram_message_id}"
                ),
                result=(
                    f"status=sent mode=direct "
                    f"has_keyboard={reply_markup is not None}"
                ),
            )

    def _log_text_input(
        self,
        item: TelegramInput,
        user_id: int,
        *,
        update_id: int | None,
    ) -> None:
        assert item.text is not None
        log_interaction(
            interaction_logger,
            heading="USER MESSAGE",
            content=private_text(
                item.text,
                enabled=self.log_private_content,
            ),
            metadata=(
                f"update_id={update_id} application_user_id={user_id} kind=text"
            ),
        )

    def _log_command_result(
        self,
        item: TelegramInput,
        user_id: int,
        update_id: int | None,
        command: str,
        *,
        outcome: str,
        result_count: int | None = None,
    ) -> None:
        assert item.text is not None
        content = item.text if self.log_private_content else command
        count = f" item_count={result_count}" if result_count is not None else ""
        log_interaction(
            interaction_logger,
            heading="USER COMMAND",
            content=content,
            metadata=(
                f"update_id={update_id} application_user_id={user_id} "
                f"kind=command command={command}"
            ),
            result=f"outcome={outcome}{count}",
        )

    def _log_callback_result(
        self,
        item: TelegramInput,
        user_id: int,
        update_id: int | None,
        resource: str,
        resource_id: int,
        action: str,
        *,
        identity: str | None,
        outcome: str,
    ) -> None:
        assert item.callback_data is not None
        log_interaction(
            interaction_logger,
            heading="USER PRESSED",
            content=(
                f"{_callback_label(action)} — "
                f"{_callback_resource(resource)} #{resource_id}"
            ),
            metadata=(
                f"update_id={update_id} application_user_id={user_id} "
                f"kind=callback identity={identity} callback_data="
                f"{private_text(item.callback_data, enabled=self.log_private_content)}"
            ),
            result=f"outcome={outcome}",
        )

    def _reminder_titles(
        self,
        user_id: int,
        reminders: list[Reminder],
    ) -> dict[int, str]:
        task_titles = {task.id: task.title for task in self.tasks.list(user_id)}
        goal_titles = {goal.id: goal.title for goal in self.goals.list(user_id)}
        titles = {}
        for reminder in reminders:
            if reminder.id is None:
                continue
            if reminder.task_id is not None:
                title = task_titles.get(reminder.task_id)
            else:
                title = goal_titles.get(reminder.goal_id)
            if title is not None:
                titles[reminder.id] = title
        return titles


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block["text"]
            if isinstance(block, dict) and isinstance(block.get("text"), str)
            else str(block)
            for block in content
        )
    return str(content)


def _callback_resource(resource: str) -> str:
    return {
        "d": "delivery",
        "r": "reminder",
    }.get(resource, "unknown")


def _callback_label(action: str) -> str:
    return {
        "done": "Done",
        "s": "Snooze",
        "skip": "Skip",
        "pause": "Pause",
        "s10": "Snooze 10 minutes",
        "s60": "Snooze 1 hour",
        "sday": "Snooze until tomorrow",
        "resume": "Resume",
    }.get(action, "Unknown")


_HELP_TEXT = """Reminder Agent

Send a natural-language request, for example:
Remind me to submit my claim tomorrow at 9 AM.

Commands:
/today - reminders scheduled today
/tasks - active tasks
/goals - active goals
/reminders - active and paused reminders
/timezone - show your timezone
/timezone Asia/Singapore - change your timezone
/help - show this help"""


__all__ = ["TelegramProcessor"]
