import asyncio
from datetime import UTC, date, datetime, time

from reminder_agent.domain.enums import DeliveryStatus, ReminderPurpose
from reminder_agent.domain.recurrence import ReminderSchedule
from reminder_agent.scheduler.materializer import ReminderMaterializer
from reminder_agent.scheduler.worker import DeliveryWorker
from reminder_agent.services.deliveries import DeliveryService
from reminder_agent.services.reminders import ReminderService
from reminder_agent.services.tasks import TaskService
from reminder_agent.telegram.accounts import TelegramAccountService
from reminder_agent.telegram.formatting import render_markdown
from reminder_agent.telegram.gateway import TelegramGateway
from reminder_agent.telegram.outbox import OutboxService


class FakeBot:
    def __init__(self):
        self.messages = []
        self.api_requests = []

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)

    async def do_api_request(self, endpoint, api_kwargs=None, **kwargs):
        self.api_requests.append(
            {
                "endpoint": endpoint,
                "api_kwargs": api_kwargs,
                **kwargs,
            }
        )
        return {"message_id": 987}


class FailingBot(FakeBot):
    async def send_message(self, **kwargs):
        raise RuntimeError("Telegram unavailable")


def test_telegram_gateway_resolves_chat_and_adds_buttons(database, user_id):
    accounts = TelegramAccountService(
        database,
        owner_telegram_user_id=12345,
        application_user_id=user_id,
    )
    accounts.authenticate(
        telegram_user_id=12345,
        telegram_chat_id=54321,
        chat_type="private",
    )
    bot = FakeBot()
    gateway = TelegramGateway(bot, accounts)

    asyncio.run(
        gateway.send(
            delivery_id=42,
            user_id=user_id,
            text="Submit claim",
        )
    )

    assert len(bot.messages) == 1
    sent = bot.messages[0]
    assert sent["chat_id"] == 54321
    assert sent["text"] == "🔔 Submit claim"
    assert sent["reply_markup"].inline_keyboard[0][0].callback_data == "d:42:done"


def test_telegram_gateway_sends_persisted_rich_message(database, user_id):
    accounts = TelegramAccountService(
        database,
        owner_telegram_user_id=12345,
        application_user_id=user_id,
    )
    accounts.authenticate(
        telegram_user_id=12345,
        telegram_chat_id=54321,
        chat_type="private",
    )
    now = datetime(2026, 8, 22, 1, 0, tzinfo=UTC)
    outbox = OutboxService(database)
    rich_message = render_markdown("Please **drink water**.")
    outbox.enqueue(
        dedupe_key="formatted-reply",
        application_user_id=user_id,
        telegram_chat_id=54321,
        text="Please **drink water**.",
        rich_message=rich_message,
        now_utc=now,
    )
    item = outbox.claim_ready(now, lease_seconds=60)
    bot = FakeBot()

    asyncio.run(TelegramGateway(bot, accounts).send_outbox(item))

    assert bot.messages == []
    assert bot.api_requests == [
        {
            "endpoint": "sendRichMessage",
            "api_kwargs": {
                "chat_id": 54321,
                "rich_message": {
                    "html": "<p>Please <strong>drink water</strong>.</p>"
                },
            },
        }
    ]


def test_telegram_send_failure_uses_delivery_retry(database, user_id):
    accounts = TelegramAccountService(
        database,
        owner_telegram_user_id=12345,
        application_user_id=user_id,
    )
    accounts.authenticate(
        telegram_user_id=12345,
        telegram_chat_id=54321,
        chat_type="private",
    )
    now = datetime(2026, 8, 22, 1, 0, tzinfo=UTC)
    task = TaskService(database).create(user_id, "Submit claim")
    ReminderService(
        database,
        clock=lambda: now.replace(day=21),
    ).create(
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
    delivery = ReminderMaterializer(deliveries).run_once(now)[0]
    worker = DeliveryWorker(
        deliveries,
        TelegramGateway(FailingBot(), accounts),
        retry_base_seconds=10,
    )

    assert asyncio.run(worker.run_once(now)) is True

    saved = deliveries.get(user_id, delivery.id)
    assert saved.status == DeliveryStatus.RETRY_WAIT
    assert saved.last_error == "Telegram unavailable"
