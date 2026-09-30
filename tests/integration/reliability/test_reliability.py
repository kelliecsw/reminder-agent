import asyncio
from datetime import UTC, date, datetime, time, timedelta

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from reminder_agent.ai.tools.registry import ToolContext, create_tool_registry
from reminder_agent.domain.enums import (
    DeliveryStatus,
    OutboxStatus,
    ReminderPurpose,
    TelegramUpdateStatus,
)
from reminder_agent.domain.errors import InvalidStateTransitionError
from reminder_agent.domain.recurrence import ReminderSchedule
from reminder_agent.scheduler.materializer import ReminderMaterializer
from reminder_agent.scheduler.worker import DeliveryDispatcher
from reminder_agent.services.agent_threads import AgentThreadService
from reminder_agent.services.deliveries import DeliveryService
from reminder_agent.services.goals import GoalService
from reminder_agent.services.reminders import ReminderService
from reminder_agent.services.tasks import TaskService
from reminder_agent.services.tool_executions import ToolExecutionService
from reminder_agent.telegram.normalize import TelegramInput, TelegramInputKind
from reminder_agent.telegram.outbox import OutboxService
from reminder_agent.telegram.outbox_worker import OutboxWorker
from reminder_agent.telegram.formatting import render_markdown
from reminder_agent.telegram.updates import TelegramUpdateService
from reminder_agent.telegram.update_worker import QueuedReplyBot, TelegramUpdateWorker
from telegram import Bot


NOW = datetime(2026, 9, 9, 1, 0, tzinfo=UTC)


def _input() -> TelegramInput:
    return TelegramInput(
        kind=TelegramInputKind.TEXT,
        telegram_user_id=123,
        telegram_chat_id=123,
        chat_type="private",
        text="hello",
    )


def _callback_input(data: str = "d:1:done") -> TelegramInput:
    return TelegramInput(
        kind=TelegramInputKind.CALLBACK,
        telegram_user_id=123,
        telegram_chat_id=123,
        chat_type="private",
        callback_data=data,
    )


def _callback_payload(update_id: int, *, message_id: int = 20) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"callback-{update_id}",
            "from": {
                "id": 123,
                "is_bot": False,
                "first_name": "Owner",
            },
            "message": {
                "message_id": message_id,
                "date": int(NOW.timestamp()),
                "chat": {"id": 123, "type": "private"},
                "text": "Reminder",
            },
            "chat_instance": "test-chat",
            "data": "d:1:done",
        },
    }


def test_telegram_update_is_deduplicated_and_expired_lease_is_reclaimed(
    database,
    user_id,
):
    updates = TelegramUpdateService(database)
    kwargs = {
        "update_id": 50,
        "item": _input(),
        "application_user_id": user_id,
        "payload": {"update_id": 50},
        "now_utc": NOW,
    }

    assert updates.receive(**kwargs) is True
    assert updates.receive(**kwargs) is False

    first = updates.claim_ready(NOW, lease_seconds=60)
    assert first is not None
    assert first.status == TelegramUpdateStatus.CLAIMED
    assert updates.claim_ready(NOW + timedelta(seconds=59), lease_seconds=60) is None

    reclaimed = updates.claim_ready(NOW + timedelta(seconds=60), lease_seconds=60)
    assert reclaimed is not None
    assert reclaimed.claim_token != first.claim_token
    assert reclaimed.attempt_count == 2

    with pytest.raises(InvalidStateTransitionError, match="stale worker claim"):
        updates.complete(first, NOW + timedelta(seconds=61))
    updates.complete(reclaimed, NOW + timedelta(seconds=61))
    assert updates.get(50).status == TelegramUpdateStatus.COMPLETED


def test_outbox_deduplicates_and_rejects_stale_claim(database, user_id):
    outbox = OutboxService(database)
    first, created = outbox.enqueue(
        dedupe_key="reply:1",
        application_user_id=user_id,
        telegram_chat_id=123,
        text="Hello",
        now_utc=NOW,
    )
    duplicate, duplicate_created = outbox.enqueue(
        dedupe_key="reply:1",
        application_user_id=user_id,
        telegram_chat_id=123,
        text="Hello",
        now_utc=NOW,
    )
    assert created is True
    assert duplicate_created is False
    assert duplicate.id == first.id

    old_claim = outbox.claim_ready(NOW, lease_seconds=30)
    new_claim = outbox.claim_ready(NOW + timedelta(seconds=30), lease_seconds=30)
    assert old_claim is not None and new_claim is not None
    with pytest.raises(InvalidStateTransitionError, match="stale worker claim"):
        outbox.mark_sent(old_claim, telegram_message_id=10, now_utc=NOW)

    outbox.mark_sent(new_claim, telegram_message_id=11, now_utc=NOW)
    saved = outbox.get(first.id)
    assert saved.status == OutboxStatus.SENT
    assert saved.telegram_message_id == 11


def test_agent_thread_survives_service_restart(database, user_id):
    service = AgentThreadService(database, history_limit=10)
    saved = service.save(
        user_id,
        [HumanMessage(content="Remember this"), AIMessage(content="I will")],
        expected_version=None,
        now_utc=NOW,
    )
    assert saved.version == 1

    restarted = AgentThreadService(database, history_limit=10).load(user_id)
    assert [message.content for message in restarted.messages] == [
        "Remember this",
        "I will",
    ]


def test_tool_execution_replay_does_not_create_duplicate_task(database, user_id):
    tasks = TaskService(database)
    registry = create_tool_registry(
        tasks=tasks,
        goals=GoalService(database),
        reminders=ReminderService(database),
        execution_service=ToolExecutionService(database),
    )
    context = ToolContext(user_id=user_id, timezone="Asia/Singapore", now_utc=NOW)
    kwargs = {
        "name": "create_task",
        "arguments": {"title": "Only once"},
        "context": context,
        "request_id": "telegram:99",
        "tool_call_id": "call-1",
    }

    first = registry.execute(**kwargs)
    replay = registry.execute(**kwargs)

    assert replay == first
    assert [task.title for task in tasks.list(user_id)] == ["Only once"]


class _Gateway:
    def __init__(self):
        self.items = []

    async def send_outbox(self, item):
        self.items.append(item)
        return 777


class _FlakyGateway(_Gateway):
    async def send_outbox(self, item):
        self.items.append(item)
        if len(self.items) == 1:
            raise RuntimeError("Telegram unavailable")
        return 778


def test_outbox_send_failure_retries(database, user_id, caplog):
    outbox = OutboxService(database)
    rich_message = render_markdown("**Try again**")
    message, _ = outbox.enqueue(
        dedupe_key="retry-me",
        application_user_id=user_id,
        telegram_chat_id=123,
        text="**Try again**",
        rich_message=rich_message,
        now_utc=NOW,
    )
    gateway = _FlakyGateway()
    worker = OutboxWorker(
        outbox,
        gateway,
        retry_base_seconds=10,
        max_attempts=3,
    )

    with caplog.at_level(
        "WARNING",
        logger="reminder_agent.telegram.interaction",
    ):
        assert asyncio.run(worker.run_once(NOW)) is True
    assert "[TELEGRAM] APP REPLY · DELIVERY FAILED" in caplog.text
    assert "error='Telegram unavailable'" in caplog.text
    assert "retry_at=2026-09-09T01:00:10+00:00" in caplog.text
    failed_once = outbox.get(message.id)
    assert failed_once.status == OutboxStatus.RETRY_WAIT
    assert failed_once.next_attempt_at_utc == NOW + timedelta(seconds=10)
    assert asyncio.run(worker.run_once(NOW + timedelta(seconds=9))) is False
    assert asyncio.run(worker.run_once(NOW + timedelta(seconds=10))) is True
    assert outbox.get(message.id).status == OutboxStatus.SENT
    assert [item.text for item in gateway.items] == [
        "**Try again**",
        "**Try again**",
    ]
    assert [item.rich_message for item in gateway.items] == [
        {"html": "<p><strong>Try again</strong></p>"},
        {"html": "<p><strong>Try again</strong></p>"},
    ]


def test_scheduled_delivery_is_queued_then_sent_from_outbox(database, user_id):
    task = TaskService(database).create(user_id, "Submit report")
    ReminderService(database, clock=lambda: NOW - timedelta(days=1)).create(
        user_id,
        purpose=ReminderPurpose.TASK_REMINDER,
        task_id=task.id,
        schedule=ReminderSchedule(
            start_date=date(2026, 9, 9),
            local_time=time(9),
            timezone="Asia/Singapore",
        ),
    )
    deliveries = DeliveryService(database)
    delivery = ReminderMaterializer(deliveries).run_once(NOW)[0]
    outbox = OutboxService(database)

    assert asyncio.run(
        DeliveryDispatcher(deliveries, outbox).run_once(NOW)
    ) is True
    assert deliveries.get(user_id, delivery.id).status == DeliveryStatus.QUEUED

    gateway = _Gateway()
    assert asyncio.run(OutboxWorker(outbox, gateway).run_once(NOW)) is True
    assert len(gateway.items) == 1
    assert gateway.items[0].delivery_id == delivery.id
    assert deliveries.get(user_id, delivery.id).status == DeliveryStatus.SENT
    assert outbox.get(gateway.items[0].id).telegram_message_id == 777


def test_outbox_logging_shows_confirmed_private_content(database, user_id, caplog):
    outbox = OutboxService(database)
    outbox.enqueue(
        dedupe_key="update:55:reply:1",
        application_user_id=user_id,
        telegram_chat_id=123,
        text="A private response",
        keyboard={
            "inline_keyboard": [
                [{"text": "Done", "callback_data": "d:1:done"}],
            ]
        },
        now_utc=NOW,
    )

    with caplog.at_level(
        "INFO",
        logger="reminder_agent.telegram.interaction",
    ):
        assert asyncio.run(
            OutboxWorker(
                outbox,
                _Gateway(),
                log_private_content=True,
            ).run_once(NOW)
        ) is True

    assert "[TELEGRAM] APP REPLY" in caplog.text
    assert "source=update:55:reply:1" in caplog.text
    assert 'buttons=["Done"]' in caplog.text
    assert "A private response" in caplog.text


def test_outbox_logging_redacts_content_by_default(database, user_id, caplog):
    outbox = OutboxService(database)
    outbox.enqueue(
        dedupe_key="update:56:reply:1",
        application_user_id=user_id,
        telegram_chat_id=123,
        text="Do not log this response",
        now_utc=NOW,
    )

    with caplog.at_level(
        "INFO",
        logger="reminder_agent.telegram.interaction",
    ):
        assert asyncio.run(OutboxWorker(outbox, _Gateway()).run_once(NOW)) is True

    assert "<redacted>" in caplog.text
    assert "Do not log this response" not in caplog.text


def test_expired_delivery_claim_is_reclaimed(database, user_id):
    task = TaskService(database).create(user_id, "Lease test")
    ReminderService(database, clock=lambda: NOW - timedelta(days=1)).create(
        user_id,
        purpose=ReminderPurpose.TASK_REMINDER,
        task_id=task.id,
        schedule=ReminderSchedule(
            start_date=date(2026, 9, 9),
            local_time=time(9),
            timezone="Asia/Singapore",
        ),
    )
    deliveries = DeliveryService(database)
    ReminderMaterializer(deliveries).run_once(NOW)

    old_claim = deliveries.claim_ready(NOW, lease_seconds=30)
    assert deliveries.claim_ready(
        NOW + timedelta(seconds=29), lease_seconds=30
    ) is None
    new_claim = deliveries.claim_ready(
        NOW + timedelta(seconds=30), lease_seconds=30
    )

    assert new_claim.claim_token != old_claim.claim_token
    with pytest.raises(InvalidStateTransitionError, match="different worker claim"):
        deliveries.prepare_for_send(
            old_claim.id,
            claim_token=old_claim.claim_token,
        )


def test_snoozed_delivery_creates_a_new_outbox_dispatch(database, user_id):
    task = TaskService(database).create(user_id, "Snooze test")
    ReminderService(database, clock=lambda: NOW - timedelta(days=1)).create(
        user_id,
        purpose=ReminderPurpose.TASK_REMINDER,
        task_id=task.id,
        schedule=ReminderSchedule(
            start_date=date(2026, 9, 9),
            local_time=time(9),
            timezone="Asia/Singapore",
        ),
    )
    deliveries = DeliveryService(database)
    delivery = ReminderMaterializer(deliveries).run_once(NOW)[0]
    outbox = OutboxService(database)
    gateway = _Gateway()
    dispatcher = DeliveryDispatcher(deliveries, outbox)
    sender = OutboxWorker(outbox, gateway)

    asyncio.run(dispatcher.run_once(NOW))
    asyncio.run(sender.run_once(NOW))
    snoozed_at = NOW + timedelta(minutes=10)
    deliveries.snooze(user_id, delivery.id, snoozed_at)
    asyncio.run(dispatcher.run_once(snoozed_at))
    asyncio.run(sender.run_once(snoozed_at))

    with database.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM telegram_outbox WHERE delivery_id = ?",
            (delivery.id,),
        ).fetchone()[0]
    assert count == 2
    assert deliveries.get(user_id, delivery.id).status == DeliveryStatus.SENT


class _Processor:
    def __init__(self):
        self.update_ids = []

    async def handle(self, update, context):
        self.update_ids.append(update.update_id)
        await context.bot.send_message(
            chat_id=123,
            text="Durable response",
        )


def test_update_worker_processes_saved_payload_and_queues_reply(
    database,
    user_id,
    caplog,
):
    updates = TelegramUpdateService(database)
    payload = {
        "update_id": 101,
        "message": {
            "message_id": 10,
            "date": int(NOW.timestamp()),
            "chat": {"id": 123, "type": "private"},
            "from": {
                "id": 123,
                "is_bot": False,
                "first_name": "Owner",
            },
            "text": "/help",
        },
    }
    updates.receive(
        update_id=101,
        item=_input(),
        application_user_id=user_id,
        payload=payload,
        now_utc=NOW,
    )
    outbox = OutboxService(database)
    processor = _Processor()
    worker = TelegramUpdateWorker(
        updates,
        outbox,
        processor,
        Bot("123:abc"),
        clock=lambda: NOW,
    )

    with caplog.at_level(
        "INFO",
        logger="reminder_agent.telegram.update_worker",
    ):
        assert asyncio.run(worker.run_once(NOW)) is True
    assert processor.update_ids == [101]
    assert updates.get(101).status == TelegramUpdateStatus.COMPLETED
    reply = outbox.claim_ready(NOW, lease_seconds=60)
    assert reply.text == "Durable response"
    assert reply.dedupe_key == "update:101:reply:1"
    assert "event=processing_started update_id=101" in caplog.text
    assert "event=reply_queued source=update:101" in caplog.text
    assert "replies=1" in caplog.text


def test_queued_reply_bot_persists_rich_message(database, user_id):
    outbox = OutboxService(database)
    bot = QueuedReplyBot(
        outbox=outbox,
        reply_scope="update:102",
        application_user_id=user_id,
        clock=lambda: NOW,
    )

    sent = asyncio.run(
        bot.enqueue_rich_message(
            chat_id=123,
            text="| Item | Price |\n|---|---|\n| Water | $2–$4 |",
            rich_message={
                "html": (
                    "<table compact><tr><th>Item</th><th>Price</th></tr>"
                    "<tr><td>Water</td><td>$2–$4</td></tr></table>"
                )
            },
        )
    )

    assert sent.queued is True
    reply = outbox.claim_ready(NOW, lease_seconds=60)
    assert reply.dedupe_key == "update:102:reply:1"
    assert reply.text.startswith("| Item | Price |")
    assert reply.rich_message == {
        "html": (
            "<table compact><tr><th>Item</th><th>Price</th></tr>"
            "<tr><td>Water</td><td>$2–$4</td></tr></table>"
        )
    }


def test_update_workers_claim_only_their_assigned_kinds(database, user_id):
    updates = TelegramUpdateService(database)
    updates.receive(
        update_id=110,
        item=_input(),
        application_user_id=user_id,
        payload={
            "update_id": 110,
            "message": {
                "message_id": 10,
                "date": int(NOW.timestamp()),
                "chat": {"id": 123, "type": "private"},
                "from": {
                    "id": 123,
                    "is_bot": False,
                    "first_name": "Owner",
                },
                "text": "hello",
            },
        },
        now_utc=NOW,
    )
    updates.receive(
        update_id=111,
        item=_callback_input(),
        application_user_id=user_id,
        payload=_callback_payload(111),
        now_utc=NOW,
    )
    processor = _Processor()
    outbox = OutboxService(database)
    bot = Bot("123:abc")
    interaction_worker = TelegramUpdateWorker(
        updates,
        outbox,
        processor,
        bot,
        clock=lambda: NOW,
        kinds={TelegramInputKind.CALLBACK, TelegramInputKind.COMMAND},
    )
    conversation_worker = TelegramUpdateWorker(
        updates,
        outbox,
        processor,
        bot,
        clock=lambda: NOW,
        kinds={TelegramInputKind.TEXT},
    )

    assert asyncio.run(interaction_worker.run_once(NOW)) is True
    assert processor.update_ids == [111]
    assert updates.get(110).status == TelegramUpdateStatus.RECEIVED
    assert asyncio.run(conversation_worker.run_once(NOW)) is True
    assert processor.update_ids == [111, 110]


def test_distinct_updates_for_same_callback_queue_only_one_reply(database, user_id):
    updates = TelegramUpdateService(database)
    for update_id in (120, 121):
        updates.receive(
            update_id=update_id,
            item=_callback_input(),
            application_user_id=user_id,
            payload=_callback_payload(update_id, message_id=99),
            now_utc=NOW,
        )
    outbox = OutboxService(database)
    worker = TelegramUpdateWorker(
        updates,
        outbox,
        _Processor(),
        Bot("123:abc"),
        clock=lambda: NOW,
        kinds={TelegramInputKind.CALLBACK},
    )

    assert asyncio.run(worker.run_once(NOW)) is True
    assert asyncio.run(worker.run_once(NOW)) is True

    with database.connect() as conn:
        rows = conn.execute(
            "SELECT dedupe_key, text FROM telegram_outbox"
        ).fetchall()
    assert [(row["dedupe_key"], row["text"]) for row in rows] == [
        (
            "telegram-callback:123:message:99:d:1:done:reply:1",
            "Durable response",
        )
    ]
