from datetime import UTC, date, datetime, time

import pytest

from reminder_agent.db.database import Database
from reminder_agent.domain.enums import (
    DeliveryStatus,
    GoalStatus,
    ReminderPurpose,
    ReminderStatus,
    TaskStatus,
)
from reminder_agent.domain.errors import (
    InvalidOwnershipError,
    InvalidStateTransitionError,
    InvalidTimezoneError,
    NoFutureOccurrenceError,
)
from reminder_agent.domain.models import Delivery
from reminder_agent.domain.recurrence import ReminderSchedule
from reminder_agent.repositories.deliveries import DeliveryRepository
from reminder_agent.repositories.users import UserRepository
from reminder_agent.services.goals import GoalService
from reminder_agent.services.deliveries import DeliveryService
from reminder_agent.services.reminders import ReminderService
from reminder_agent.services.tasks import TaskService
from reminder_agent.services.users import UserService


NOW = datetime(2026, 8, 20, 0, 0, tzinfo=UTC)


@pytest.fixture
def services(database: Database):
    return (
        GoalService(database, clock=lambda: NOW),
        TaskService(database),
        ReminderService(database, clock=lambda: NOW),
    )


def test_user_timezone_is_validated_and_updated(database: Database, user_id: int):
    service = UserService(database)
    assert service.update_timezone(user_id, "Asia/Tokyo") == "Asia/Tokyo"
    assert service.get_timezone(user_id) == "Asia/Tokyo"
    with pytest.raises(InvalidTimezoneError):
        service.update_timezone(user_id, "Mars/Olympus")


def test_goal_task_progress_and_standalone_task_workflow(
    user_id: int, services
):
    goals, tasks, _ = services
    goal = goals.create(user_id, "Run a half marathon")
    child = tasks.create(user_id, "Run 5 km", goal_id=goal.id)
    standalone = tasks.create(user_id, "Buy printer ink")
    progress = goals.record_progress(user_id, goal.id, "Completed week one")

    assert tasks.list(user_id, goal_id=goal.id) == [child]
    assert standalone.goal_id is None
    assert progress.occurred_on == date(2026, 8, 20)
    assert goals.list_progress(user_id, goal.id) == [progress]


def test_reminder_lifecycle_and_task_completion(user_id: int, services):
    _, tasks, reminders = services
    task = tasks.create(user_id, "Submit claim")
    reminder = reminders.create(
        user_id,
        purpose=ReminderPurpose.TASK_REMINDER,
        task_id=task.id,
        schedule=ReminderSchedule(
            start_date=date(2026, 8, 21),
            local_time=time(9),
            timezone="Asia/Singapore",
        ),
    )
    assert reminder.next_fire_at_utc == datetime(2026, 8, 21, 1, tzinfo=UTC)

    paused = reminders.pause(user_id, reminder.id)
    assert paused.status == ReminderStatus.PAUSED
    assert paused.version == 2
    resumed = reminders.resume(user_id, reminder.id)
    assert resumed.status == ReminderStatus.ACTIVE
    assert resumed.version == 3

    rescheduled = reminders.reschedule(
        user_id,
        reminder.id,
        ReminderSchedule(
            start_date=date(2026, 8, 22),
            local_time=time(10),
            timezone="Asia/Singapore",
        ),
    )
    assert rescheduled.next_fire_at_utc == datetime(2026, 8, 22, 2, tzinfo=UTC)
    assert rescheduled.version == 4

    completed = tasks.complete(user_id, task.id)
    assert completed.status == TaskStatus.COMPLETED
    cancelled = reminders.get(user_id, reminder.id)
    assert cancelled.status == ReminderStatus.CANCELLED
    assert cancelled.next_fire_at_utc is None
    assert cancelled.version == 5


def test_goal_completion_keeps_child_tasks_and_cancels_goal_reminders(
    user_id: int, services
):
    goals, tasks, reminders = services
    goal = goals.create(user_id, "Train")
    task = tasks.create(user_id, "Run", goal_id=goal.id)
    reminder = reminders.create(
        user_id,
        purpose=ReminderPurpose.GOAL_REMINDER,
        goal_id=goal.id,
        schedule=ReminderSchedule(
            start_date=date(2026, 8, 21),
            local_time=time(9),
            timezone="UTC",
        ),
    )

    assert goals.complete(user_id, goal.id).status == GoalStatus.COMPLETED
    assert tasks.list(user_id, goal_id=goal.id) == [task]
    assert reminders.get(user_id, reminder.id).status == ReminderStatus.CANCELLED


def test_cross_user_relationships_and_invalid_transitions_are_rejected(
    database: Database, user_id: int, services
):
    goals, tasks, reminders = services
    with database.transaction() as conn:
        other_id = UserRepository().create(conn, timezone="UTC")
    other_goal = goals.create(other_id, "Other goal")

    with pytest.raises(InvalidOwnershipError):
        tasks.create(user_id, "Wrong owner", goal_id=other_goal.id)

    task = tasks.create(user_id, "Done soon")
    tasks.complete(user_id, task.id)
    with pytest.raises(InvalidStateTransitionError):
        tasks.resume(user_id, task.id)
    with pytest.raises(InvalidStateTransitionError):
        reminders.create(
            user_id,
            purpose=ReminderPurpose.TASK_REMINDER,
            task_id=task.id,
            schedule=ReminderSchedule(
                start_date=date(2026, 8, 21),
                local_time=time(9),
                timezone="UTC",
            ),
        )


def test_schedule_without_future_occurrence_is_rejected(user_id: int, services):
    _, tasks, reminders = services
    task = tasks.create(user_id, "Old task")
    with pytest.raises(NoFutureOccurrenceError):
        reminders.create(
            user_id,
            purpose=ReminderPurpose.TASK_REMINDER,
            task_id=task.id,
            schedule=ReminderSchedule(
                start_date=date(2026, 8, 19),
                local_time=time(9),
                timezone="UTC",
            ),
        )


def test_sent_delivery_can_be_listed_skipped_and_marked_acted_on(
    database: Database, user_id: int, services
):
    _, tasks, reminders = services
    task = tasks.create(user_id, "Respond")
    reminder = reminders.create(
        user_id,
        purpose=ReminderPurpose.TASK_REMINDER,
        task_id=task.id,
        schedule=ReminderSchedule(
            start_date=date(2026, 8, 21),
            local_time=time(9),
            timezone="UTC",
        ),
    )
    repo = DeliveryRepository()
    with database.transaction() as conn:
        first = repo.create(
            conn,
            Delivery(
                user_id=user_id,
                reminder_id=reminder.id,
                reminder_version=reminder.version,
                scheduled_for_utc=reminder.next_fire_at_utc,
                status=DeliveryStatus.SENT,
            ),
        )
        second = repo.create(
            conn,
            Delivery(
                user_id=user_id,
                reminder_id=reminder.id,
                reminder_version=reminder.version,
                scheduled_for_utc=datetime(2026, 8, 22, 9, tzinfo=UTC),
                status=DeliveryStatus.SENT,
            ),
        )

    service = DeliveryService(database)
    assert service.list(user_id, reminder.id) == [first, second]
    assert service.skip(user_id, first.id).status == DeliveryStatus.SKIPPED
    assert service.mark_acted_on(user_id, second.id).status == DeliveryStatus.ACTED_ON
    with pytest.raises(InvalidStateTransitionError):
        service.skip(user_id, first.id)
