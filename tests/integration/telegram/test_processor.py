import asyncio
from datetime import UTC, date, datetime, time
from types import SimpleNamespace

from langchain_core.messages import AIMessage
from reminder_agent.domain.enums import (
    DeliveryStatus,
    Frequency,
    ReminderPurpose,
    ReminderStatus,
    TaskStatus,
)
from reminder_agent.domain.recurrence import RecurrenceRule, ReminderSchedule
from reminder_agent.repositories.users import UserRepository
from reminder_agent.scheduler.materializer import ReminderMaterializer
from reminder_agent.services.delivery_actions import DeliveryActionService
from reminder_agent.services.deliveries import DeliveryService
from reminder_agent.services.goals import GoalService
from reminder_agent.services.reminders import ReminderService
from reminder_agent.services.tasks import TaskService
from reminder_agent.services.users import UserService
from reminder_agent.telegram.accounts import TelegramAccountService
from reminder_agent.telegram.processor import TelegramProcessor
from reminder_agent.services.tool_executions import ToolExecutionService


NOW = datetime(2026, 8, 22, 1, 0, tzinfo=UTC)


class FakeGraph:
    def __init__(self, response="Handled by graph"):
        self.states = []
        self.response = response

    async def ainvoke(self, state):
        self.states.append(state)
        return {
            "messages": [
                *state["messages"],
                AIMessage(content=self.response),
            ]
        }


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)

    async def do_api_request(self, endpoint, api_kwargs=None, **kwargs):
        self.messages.append(
            {
                "endpoint": endpoint,
                **(api_kwargs or {}),
                **kwargs,
            }
        )
        return {"message_id": len(self.messages)}


class FakeQuery:
    def __init__(self, data, message_id=10):
        self.data = data
        self.message = SimpleNamespace(message_id=message_id)
        self.inline_message_id = None
        self.answered = False
        self.cleared = False

    async def answer(self, *args, **kwargs):
        self.answered = True

    async def edit_message_reply_markup(self, *, reply_markup):
        self.cleared = reply_markup is None


def _processor(database, user_id, graph, *, log_private_content=False):
    return TelegramProcessor(
        accounts=TelegramAccountService(
            database,
            owner_telegram_user_id=12345,
            application_user_id=user_id,
        ),
        users=UserService(database),
        tasks=TaskService(database),
        goals=GoalService(database),
        reminders=ReminderService(database),
        deliveries=DeliveryService(database),
        delivery_actions=DeliveryActionService(database),
        graph=graph,
        tool_round_limit=6,
        clock=lambda: NOW,
        log_private_content=log_private_content,
    )


def _message_update(text, *, update_id=None):
    return SimpleNamespace(
        update_id=update_id,
        effective_user=SimpleNamespace(id=12345),
        effective_chat=SimpleNamespace(id=12345, type="private"),
        effective_message=SimpleNamespace(text=text),
        callback_query=None,
    )


def _callback_update(data, *, update_id=None, message_id=10):
    query = FakeQuery(data, message_id)
    return SimpleNamespace(
        update_id=update_id,
        effective_user=SimpleNamespace(id=12345),
        effective_chat=SimpleNamespace(id=12345, type="private"),
        effective_message=None,
        callback_query=query,
    )


def test_command_routes_without_invoking_graph(database, user_id):
    graph = FakeGraph()
    bot = FakeBot()

    asyncio.run(
        _processor(database, user_id, graph).handle(
            _message_update("/help"),
            SimpleNamespace(bot=bot),
        )
    )

    assert graph.states == []
    assert bot.messages[0]["chat_id"] == 12345
    assert "Commands:" in bot.messages[0]["text"]


def test_text_invokes_graph_with_mapped_application_user(database, user_id):
    graph = FakeGraph()
    bot = FakeBot()

    asyncio.run(
        _processor(database, user_id, graph).handle(
            _message_update("Remind me tomorrow at 9"),
            SimpleNamespace(bot=bot),
        )
    )

    assert len(graph.states) == 1
    assert graph.states[0]["user_id"] == user_id
    assert graph.states[0]["timezone"] == "Asia/Singapore"
    assert bot.messages[-1]["rich_message"] == {
        "html": "<p>Handled by graph</p>"
    }


def test_agent_markdown_is_rendered_as_telegram_rich_html(database, user_id):
    graph = FakeGraph("Please **drink water**.")
    bot = FakeBot()

    asyncio.run(
        _processor(database, user_id, graph).handle(
            _message_update("What reminders are complete?"),
            SimpleNamespace(bot=bot),
        )
    )

    sent = bot.messages[-1]
    assert sent["endpoint"] == "sendRichMessage"
    assert sent["rich_message"] == {
        "html": "<p>Please <strong>drink water</strong>.</p>"
    }


def test_private_command_logging_shows_input_route_and_reply(
    database,
    user_id,
    caplog,
):
    processor = _processor(
        database,
        user_id,
        FakeGraph(),
        log_private_content=True,
    )

    with caplog.at_level(
        "INFO",
        logger="reminder_agent.telegram.interaction",
    ):
        asyncio.run(
            processor.handle(
                _message_update("/goals", update_id=700),
                SimpleNamespace(bot=FakeBot()),
            )
        )

    messages = [record.getMessage() for record in caplog.records]
    assert "[TELEGRAM] USER COMMAND" in messages
    command_index = messages.index("/goals")
    assert messages[command_index + 1].startswith(
        "[METADATA] update_id=700 application_user_id="
    )
    assert messages[command_index + 2] == "[RESULT] outcome=listed item_count=0"
    assert messages[command_index + 1] != "=" * 60
    assert "[TELEGRAM] APP REPLY" in messages
    assert "No active goals." in messages


def test_redacted_text_logging_hides_input_and_reply(database, user_id, caplog):
    processor = _processor(database, user_id, FakeGraph())

    with caplog.at_level(
        "INFO",
        logger="reminder_agent.telegram.interaction",
    ):
        asyncio.run(
            processor.handle(
                _message_update("A private request", update_id=701),
                SimpleNamespace(bot=FakeBot()),
            )
        )

    messages = [record.getMessage() for record in caplog.records]
    assert messages.count("<redacted>") == 2
    assert "[TELEGRAM] USER MESSAGE" in messages
    assert "[TELEGRAM] APP REPLY" in messages
    assert "A private request" not in caplog.text
    assert "Handled by graph" not in caplog.text


def test_callback_logging_uses_friendly_button_name(database, user_id, caplog):
    processor = _processor(
        database,
        user_id,
        FakeGraph(),
        log_private_content=True,
    )

    with caplog.at_level(
        "INFO",
        logger="reminder_agent.telegram.interaction",
    ):
        asyncio.run(
            processor.handle(
                _callback_update("d:42:s", update_id=702),
                SimpleNamespace(bot=FakeBot()),
            )
        )

    messages = [record.getMessage() for record in caplog.records]
    assert "[TELEGRAM] USER PRESSED" in messages
    assert "Snooze — delivery #42" in messages
    assert any("callback_data=d:42:s" in message for message in messages)
    assert "[RESULT] outcome=prompted_for_duration" in messages
    assert "Snooze for how long?" in messages


def test_callback_cannot_operate_on_another_users_delivery(database, user_id):
    with database.transaction() as conn:
        other_user_id = UserRepository().create(conn, timezone="UTC")
    task = TaskService(database).create(other_user_id, "Other user's task")
    reminder = ReminderService(
        database,
        clock=lambda: NOW.replace(day=21),
    ).create(
        other_user_id,
        purpose=ReminderPurpose.TASK_REMINDER,
        task_id=task.id,
        schedule=ReminderSchedule(
            start_date=date(2026, 8, 22),
            local_time=time(1),
            timezone="UTC",
        ),
    )
    deliveries = DeliveryService(database)
    delivery = ReminderMaterializer(deliveries).run_once(NOW)[0]
    claimed = deliveries.claim_ready(NOW)
    deliveries.mark_sent(claimed.id, claim_token=claimed.claim_token)
    graph = FakeGraph()
    bot = FakeBot()
    update = _callback_update(f"d:{delivery.id}:skip")

    asyncio.run(
        _processor(database, user_id, graph).handle(
            update,
            SimpleNamespace(bot=bot),
        )
    )

    assert update.callback_query.answered is True
    assert deliveries.get(other_user_id, delivery.id).status == DeliveryStatus.SENT
    assert "does not belong" in bot.messages[-1]["text"]


def test_done_callback_marks_only_the_task_reminder_occurrence(database, user_id):
    task = TaskService(database).create(user_id, "Submit report")
    reminder = ReminderService(database, clock=lambda: NOW.replace(day=21)).create(
        user_id,
        purpose=ReminderPurpose.TASK_REMINDER,
        task_id=task.id,
        schedule=ReminderSchedule(
            start_date=date(2026, 8, 22),
            local_time=time(9),
            timezone="Asia/Singapore",
            recurrence=RecurrenceRule(frequency=Frequency.DAILY),
        ),
    )
    deliveries = DeliveryService(database)
    delivery = ReminderMaterializer(deliveries).run_once(NOW)[0]
    claimed = deliveries.claim_ready(NOW)
    deliveries.mark_sent(claimed.id, claim_token=claimed.claim_token)
    bot = FakeBot()
    update = _callback_update(f"d:{delivery.id}:done")

    asyncio.run(
        _processor(database, user_id, FakeGraph()).handle(
            update,
            SimpleNamespace(bot=bot),
        )
    )

    assert update.callback_query.cleared is True
    assert deliveries.get(user_id, delivery.id).status == DeliveryStatus.ACTED_ON
    assert TaskService(database).list(user_id)[0].status == TaskStatus.ACTIVE
    assert (
        ReminderService(database).get(user_id, reminder.id).status
        == ReminderStatus.ACTIVE
    )
    assert bot.messages[-1]["text"] == "Marked this reminder occurrence done."


def test_replayed_callback_does_not_repeat_mutation(database, user_id):
    task = TaskService(database).create(user_id, "Submit report")
    ReminderService(database, clock=lambda: NOW.replace(day=21)).create(
        user_id,
        purpose=ReminderPurpose.TASK_REMINDER,
        task_id=task.id,
        schedule=ReminderSchedule(
            start_date=date(2026, 8, 22),
            local_time=time(9),
            timezone="Asia/Singapore",
        ),
    )
    deliveries = DeliveryService(database)
    delivery = ReminderMaterializer(deliveries).run_once(NOW)[0]
    claimed = deliveries.claim_ready(NOW)
    deliveries.mark_sent(claimed.id, claim_token=claimed.claim_token)
    processor = _processor(database, user_id, FakeGraph())
    processor.tool_execution_service = ToolExecutionService(database)
    bot = FakeBot()
    first_update = _callback_update(
        f"d:{delivery.id}:skip",
        update_id=500,
        message_id=75,
    )
    second_update = _callback_update(
        f"d:{delivery.id}:skip",
        update_id=501,
        message_id=75,
    )

    asyncio.run(processor.handle(first_update, SimpleNamespace(bot=bot)))
    asyncio.run(processor.handle(second_update, SimpleNamespace(bot=bot)))

    assert deliveries.get(user_id, delivery.id).status == DeliveryStatus.SKIPPED
    assert [message["text"] for message in bot.messages] == [
        "Skipped this reminder occurrence.",
        "Skipped this reminder occurrence.",
    ]
