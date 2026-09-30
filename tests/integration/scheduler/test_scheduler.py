import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta

from reminder_agent.db.database import Database
from reminder_agent.domain.enums import (
    DeliveryStatus,
    Frequency,
    ReminderPurpose,
    ReminderStatus,
    Weekday,
)
from reminder_agent.domain.recurrence import (
    RecurrenceRule,
    ReminderSchedule,
    WeekdayRule,
)
from reminder_agent.repositories.reminders import ReminderRepository
from reminder_agent.scheduler.gateway import FakeGateway, SentMessage
from reminder_agent.scheduler.materializer import ReminderMaterializer
from reminder_agent.scheduler.worker import DeliveryWorker
from reminder_agent.services.deliveries import DeliveryService
from reminder_agent.services.reminders import ReminderService
from reminder_agent.services.tasks import TaskService


START_UTC = datetime(2026, 8, 22, 1, 0, tzinfo=UTC)
BEFORE_START_UTC = START_UTC - timedelta(days=1)


def _create_task_reminder(
    database: Database,
    user_id: int,
    schedule: ReminderSchedule,
):
    task = TaskService(database).create(user_id, "Submit claim")
    reminder = ReminderService(
        database,
        clock=lambda: BEFORE_START_UTC,
    ).create(
        user_id,
        purpose=ReminderPurpose.TASK_REMINDER,
        task_id=task.id,
        schedule=schedule,
    )
    return task, reminder


def _every_three_days() -> ReminderSchedule:
    return ReminderSchedule(
        start_date=date(2026, 8, 22),
        local_time=time(9),
        timezone="Asia/Singapore",
        recurrence=RecurrenceRule(
            frequency=Frequency.DAILY,
            interval=3,
        ),
    )


def test_one_off_reminder_is_materialized_and_sent(database, user_id):
    _, reminder = _create_task_reminder(
        database,
        user_id,
        ReminderSchedule(
            start_date=date(2026, 8, 22),
            local_time=time(9),
            timezone="Asia/Singapore",
        ),
    )
    deliveries = DeliveryService(database)
    gateway = FakeGateway()

    created = ReminderMaterializer(deliveries).run_once(START_UTC)
    assert len(created) == 1
    assert created[0].scheduled_for_utc == START_UTC
    assert created[0].deliver_at_utc == START_UTC

    saved_reminder = ReminderService(database).get(user_id, reminder.id)
    assert saved_reminder.status == ReminderStatus.COMPLETED
    assert saved_reminder.next_fire_at_utc is None

    assert asyncio.run(DeliveryWorker(deliveries, gateway).run_once(START_UTC)) is True
    assert gateway.messages == [
        SentMessage(
            delivery_id=created[0].id,
            user_id=user_id,
            text="Submit claim",
        )
    ]
    assert deliveries.get(user_id, created[0].id).status == DeliveryStatus.SENT


def test_every_three_days_stays_anchored(database, user_id):
    _, reminder = _create_task_reminder(
        database,
        user_id,
        _every_three_days(),
    )
    deliveries = DeliveryService(database)
    materializer = ReminderMaterializer(deliveries)

    first = materializer.run_once(START_UTC)
    after_first = ReminderService(database).get(user_id, reminder.id)
    assert len(first) == 1
    assert after_first.next_fire_at_utc == START_UTC + timedelta(days=3)

    second = materializer.run_once(START_UTC + timedelta(days=3))
    after_second = ReminderService(database).get(user_id, reminder.id)
    assert len(second) == 1
    assert second[0].scheduled_for_utc == START_UTC + timedelta(days=3)
    assert after_second.next_fire_at_utc == START_UTC + timedelta(days=6)


def test_monthly_ordinal_recurrence_advances(database, user_id):
    schedule = ReminderSchedule(
        start_date=date(2027, 1, 1),
        local_time=time(9),
        timezone="Asia/Singapore",
        recurrence=RecurrenceRule(
            frequency=Frequency.MONTHLY,
            by_day=(WeekdayRule(Weekday.SATURDAY, ordinal=2),),
        ),
    )
    task = TaskService(database).create(user_id, "Monthly review")
    reminder = ReminderService(
        database,
        clock=lambda: datetime(2026, 12, 1, tzinfo=UTC),
    ).create(
        user_id,
        purpose=ReminderPurpose.TASK_REMINDER,
        task_id=task.id,
        schedule=schedule,
    )
    first = datetime(2027, 1, 9, 1, 0, tzinfo=UTC)

    ReminderMaterializer(DeliveryService(database)).run_once(first)

    saved = ReminderService(database).get(user_id, reminder.id)
    assert saved.next_fire_at_utc == datetime(2027, 2, 13, 1, 0, tzinfo=UTC)


def test_same_occurrence_is_not_duplicated(database, user_id):
    _, reminder = _create_task_reminder(database, user_id, _every_three_days())
    deliveries = DeliveryService(database)
    materializer = ReminderMaterializer(deliveries)

    assert len(materializer.run_once(START_UTC)) == 1

    reminder_repo = ReminderRepository()
    with database.transaction() as conn:
        saved = reminder_repo.get(conn, reminder.id)
        reminder_repo.update(
            conn,
            replace(
                saved,
                status=ReminderStatus.ACTIVE,
                next_fire_at_utc=START_UTC,
            ),
        )

    assert materializer.run_once(START_UTC) == []
    assert len(deliveries.list(user_id, reminder.id)) == 1


def test_paused_reminder_is_not_delivered(database, user_id):
    _, reminder = _create_task_reminder(database, user_id, _every_three_days())
    deliveries = DeliveryService(database)
    created = ReminderMaterializer(deliveries).run_once(START_UTC)[0]
    ReminderService(database).pause(user_id, reminder.id)
    gateway = FakeGateway()

    assert asyncio.run(DeliveryWorker(deliveries, gateway).run_once(START_UTC)) is True
    assert gateway.messages == []
    assert deliveries.get(user_id, created.id).status == DeliveryStatus.CANCELLED


def test_delivery_is_cancelled_after_task_completes(database, user_id):
    task, reminder = _create_task_reminder(database, user_id, _every_three_days())
    deliveries = DeliveryService(database)
    created = ReminderMaterializer(deliveries).run_once(START_UTC)[0]
    TaskService(database).complete(user_id, task.id)
    gateway = FakeGateway()

    asyncio.run(DeliveryWorker(deliveries, gateway).run_once(START_UTC))

    assert gateway.messages == []
    assert deliveries.get(user_id, created.id).status == DeliveryStatus.CANCELLED
    assert ReminderService(database).get(user_id, reminder.id).status == ReminderStatus.CANCELLED


def test_stale_reminder_version_is_not_sent(database, user_id):
    _, reminder = _create_task_reminder(database, user_id, _every_three_days())
    deliveries = DeliveryService(database)
    created = ReminderMaterializer(deliveries).run_once(START_UTC)[0]
    ReminderService(
        database,
        clock=lambda: START_UTC,
    ).reschedule(
        user_id,
        reminder.id,
        ReminderSchedule(
            start_date=date(2026, 9, 1),
            local_time=time(9),
            timezone="Asia/Singapore",
        ),
    )
    gateway = FakeGateway()

    asyncio.run(DeliveryWorker(deliveries, gateway).run_once(START_UTC))

    assert gateway.messages == []
    assert deliveries.get(user_id, created.id).status == DeliveryStatus.CANCELLED


def test_failed_delivery_retries_then_sends(database, user_id):
    _create_task_reminder(database, user_id, _every_three_days())
    deliveries = DeliveryService(database)
    created = ReminderMaterializer(deliveries).run_once(START_UTC)[0]
    gateway = FakeGateway(failures_remaining=1)
    worker = DeliveryWorker(
        deliveries,
        gateway,
        retry_base_seconds=10,
        max_attempts=3,
    )

    assert asyncio.run(worker.run_once(START_UTC)) is True
    failed_once = deliveries.get(user_id, created.id)
    assert failed_once.status == DeliveryStatus.RETRY_WAIT
    assert failed_once.attempt_count == 1
    assert failed_once.deliver_at_utc == START_UTC + timedelta(seconds=10)
    assert failed_once.last_error == "Fake gateway send failed."

    assert asyncio.run(worker.run_once(START_UTC + timedelta(seconds=9))) is False
    assert asyncio.run(worker.run_once(START_UTC + timedelta(seconds=10))) is True
    sent = deliveries.get(user_id, created.id)
    assert sent.status == DeliveryStatus.SENT
    assert sent.attempt_count == 2
    assert sent.last_error is None


def test_delivery_stops_retrying_at_max_attempts(database, user_id):
    _create_task_reminder(database, user_id, _every_three_days())
    deliveries = DeliveryService(database)
    created = ReminderMaterializer(deliveries).run_once(START_UTC)[0]
    worker = DeliveryWorker(
        deliveries,
        FakeGateway(failures_remaining=1),
        max_attempts=1,
    )

    assert asyncio.run(worker.run_once(START_UTC)) is True
    failed = deliveries.get(user_id, created.id)
    assert failed.status == DeliveryStatus.FAILED
    assert failed.attempt_count == 1


def test_snooze_changes_delivery_time_without_changing_occurrence(database, user_id):
    _create_task_reminder(database, user_id, _every_three_days())
    deliveries = DeliveryService(database)
    created = ReminderMaterializer(deliveries).run_once(START_UTC)[0]
    gateway = FakeGateway()
    worker = DeliveryWorker(deliveries, gateway)
    asyncio.run(worker.run_once(START_UTC))
    snoozed_until = START_UTC + timedelta(minutes=15)

    snoozed = deliveries.snooze(user_id, created.id, snoozed_until)

    assert snoozed.scheduled_for_utc == START_UTC
    assert snoozed.deliver_at_utc == snoozed_until
    assert snoozed.status == DeliveryStatus.RETRY_WAIT
    assert asyncio.run(
        worker.run_once(snoozed_until - timedelta(seconds=1))
    ) is False
    assert asyncio.run(worker.run_once(snoozed_until)) is True
    assert gateway.messages == [
        SentMessage(
            delivery_id=created.id,
            user_id=user_id,
            text="Submit claim",
        ),
        SentMessage(
            delivery_id=created.id,
            user_id=user_id,
            text="Submit claim",
        ),
    ]


def test_pending_delivery_survives_database_restart(database, user_id):
    _create_task_reminder(database, user_id, _every_three_days())
    ReminderMaterializer(DeliveryService(database)).run_once(START_UTC)

    restarted = Database(database.path, migrations_dir=database.migrations_dir)
    restarted.migrate()
    gateway = FakeGateway()

    assert asyncio.run(
        DeliveryWorker(DeliveryService(restarted), gateway).run_once(START_UTC)
    )
    assert gateway.messages == [
        SentMessage(delivery_id=1, user_id=user_id, text="Submit claim")
    ]
