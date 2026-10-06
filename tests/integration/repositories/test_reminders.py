from dataclasses import replace
from datetime import UTC, date, datetime, time

from reminder_agent.db.database import Database
from reminder_agent.domain.enums import (
    Frequency,
    ReminderPurpose,
    ReminderStatus,
    Weekday,
)
from reminder_agent.domain.models import Goal, Reminder, Task
from reminder_agent.domain.recurrence import (
    RecurrenceRule,
    ReminderSchedule,
    WeekdayRule,
)
from reminder_agent.repositories.goals import GoalRepository
from reminder_agent.repositories.reminders import ReminderRepository
from reminder_agent.repositories.tasks import TaskRepository


def test_task_reminder_round_trips_with_complex_recurrence(
    database: Database,
    user_id: int,
):
    task_repo = TaskRepository()
    reminder_repo = ReminderRepository()

    with database.transaction() as conn:
        task = task_repo.create(
            conn,
            Task(
                user_id=user_id,
                title="Review training plan",
            ),
        )

        reminder = reminder_repo.create(
            conn,
            Reminder(
                user_id=user_id,
                task_id=task.id,
                purpose=ReminderPurpose.TASK_REMINDER,
                schedule=ReminderSchedule(
                    start_date=date(2026, 8, 22),
                    local_time=time(9, 30),
                    timezone="Asia/Singapore",
                    recurrence=RecurrenceRule(
                        frequency=Frequency.MONTHLY,
                        interval=1,
                        by_day=(
                            WeekdayRule(
                                Weekday.SATURDAY,
                                ordinal=2,
                            ),
                        ),
                    ),
                ),
                next_fire_at_utc=datetime(
                    2026,
                    9,
                    12,
                    1,
                    30,
                    tzinfo=UTC,
                ),
            ),
        )

    with database.connect() as conn:
        loaded = reminder_repo.get(conn, reminder.id)

    assert loaded == reminder


def test_goal_reminder_can_be_persisted_and_listed_by_goal(
    database: Database,
    user_id: int,
):
    goal_repo = GoalRepository()
    reminder_repo = ReminderRepository()

    with database.transaction() as conn:
        goal = goal_repo.create(
            conn,
            Goal(
                user_id=user_id,
                title="Run a half marathon",
            ),
        )

        reminder = reminder_repo.create(
            conn,
            Reminder(
                user_id=user_id,
                goal_id=goal.id,
                purpose=ReminderPurpose.GOAL_REMINDER,
                schedule=ReminderSchedule(
                    start_date=date(2026, 8, 23),
                    local_time=time(20, 0),
                    timezone="Asia/Singapore",
                    recurrence=RecurrenceRule(
                        frequency=Frequency.WEEKLY,
                        by_day=(
                            WeekdayRule(Weekday.SUNDAY),
                        ),
                    ),
                ),
            ),
        )

    with database.connect() as conn:
        reminders = reminder_repo.list_by_goal(conn, goal.id)

    assert reminders == [reminder]


def test_one_off_reminder_round_trips_with_null_recurrence(
    database: Database,
    user_id: int,
):
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

        reminder = reminder_repo.create(
            conn,
            Reminder(
                user_id=user_id,
                task_id=task.id,
                purpose=ReminderPurpose.TASK_REMINDER,
                schedule=ReminderSchedule(
                    start_date=date(2026, 8, 25),
                    local_time=time(9, 0),
                    timezone="Asia/Singapore",
                ),
            ),
        )

    with database.connect() as conn:
        loaded = reminder_repo.get(conn, reminder.id)

    assert loaded == reminder
    assert loaded.schedule.recurrence is None


def test_reminder_update_persists_schedule_status_and_version(
    database: Database,
    user_id: int,
):
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

        saved = reminder_repo.create(
            conn,
            Reminder(
                user_id=user_id,
                task_id=task.id,
                purpose=ReminderPurpose.TASK_REMINDER,
                schedule=ReminderSchedule(
                    start_date=date(2026, 8, 25),
                    local_time=time(9, 0),
                    timezone="Asia/Singapore",
                ),
            ),
        )

    updated = replace(
        saved,
        schedule=ReminderSchedule(
            start_date=date(2026, 8, 26),
            local_time=time(10, 0),
            timezone="Asia/Singapore",
            recurrence=RecurrenceRule(
                frequency=Frequency.DAILY,
                interval=3,
            ),
        ),
        next_fire_at_utc=datetime(
            2026,
            8,
            26,
            2,
            0,
            tzinfo=UTC,
        ),
        status=ReminderStatus.PAUSED,
        version=2,
    )

    with database.transaction() as conn:
        assert reminder_repo.update(conn, updated) is True

    with database.connect() as conn:
        assert reminder_repo.get(conn, saved.id) == updated
