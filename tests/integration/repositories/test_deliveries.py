from dataclasses import replace
from datetime import UTC, date, datetime, time

from reminder_agent.db.database import Database
from reminder_agent.domain.enums import DeliveryStatus, ReminderPurpose
from reminder_agent.domain.models import Delivery, Reminder, Task
from reminder_agent.domain.recurrence import ReminderSchedule
from reminder_agent.repositories.deliveries import DeliveryRepository
from reminder_agent.repositories.reminders import ReminderRepository
from reminder_agent.repositories.tasks import TaskRepository


def _create_reminder(
    database: Database,
    user_id: int,
) -> Reminder:
    task_repo = TaskRepository()
    reminder_repo = ReminderRepository()

    with database.transaction() as conn:
        task = task_repo.create(
            conn,
            Task(
                user_id=user_id,
                title="Submit claim",
            ),
        )

        return reminder_repo.create(
            conn,
            Reminder(
                user_id=user_id,
                task_id=task.id,
                purpose=ReminderPurpose.TASK_REMINDER,
                schedule=ReminderSchedule(
                    start_date=date(2026, 8, 22),
                    local_time=time(9, 0),
                    timezone="Asia/Singapore",
                ),
            ),
        )


def test_multiple_deliveries_can_reference_same_reminder(
    database: Database,
    user_id: int,
):
    reminder = _create_reminder(database, user_id)
    repo = DeliveryRepository()

    with database.transaction() as conn:
        delivery_1 = repo.create(
            conn,
            Delivery(
                user_id=user_id,
                reminder_id=reminder.id,
                reminder_version=1,
                scheduled_for_utc=datetime(
                    2026,
                    8,
                    22,
                    1,
                    0,
                    tzinfo=UTC,
                ),
            ),
        )

        delivery_2 = repo.create(
            conn,
            Delivery(
                user_id=user_id,
                reminder_id=reminder.id,
                reminder_version=1,
                scheduled_for_utc=datetime(
                    2026,
                    8,
                    29,
                    1,
                    0,
                    tzinfo=UTC,
                ),
            ),
        )

    with database.connect() as conn:
        deliveries = repo.list_by_reminder(
            conn,
            reminder.id,
        )

    assert deliveries == [delivery_1, delivery_2]


def test_delivery_update_is_persisted(
    database: Database,
    user_id: int,
):
    reminder = _create_reminder(database, user_id)
    repo = DeliveryRepository()

    with database.transaction() as conn:
        saved = repo.create(
            conn,
            Delivery(
                user_id=user_id,
                reminder_id=reminder.id,
                reminder_version=1,
                scheduled_for_utc=datetime(
                    2026,
                    8,
                    22,
                    1,
                    0,
                    tzinfo=UTC,
                ),
            ),
        )

    updated = replace(
        saved,
        status=DeliveryStatus.SENT,
    )

    with database.transaction() as conn:
        assert repo.update(conn, updated) is True

    with database.connect() as conn:
        assert repo.get(conn, saved.id) == updated
