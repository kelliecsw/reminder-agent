from datetime import UTC, date, datetime, time

from reminder_agent.db.database import Database
from reminder_agent.domain.enums import (
    Frequency,
    ReminderPurpose,
    Weekday,
)
from reminder_agent.domain.models import Delivery, Goal, Reminder, Task
from reminder_agent.domain.recurrence import (
    RecurrenceRule,
    ReminderSchedule,
    WeekdayRule,
)
from reminder_agent.repositories.deliveries import DeliveryRepository
from reminder_agent.repositories.goals import GoalRepository
from reminder_agent.repositories.reminders import ReminderRepository
from reminder_agent.repositories.tasks import TaskRepository
from reminder_agent.repositories.users import UserRepository


def test_complete_graph_survives_database_restart(
    database: Database,
    user_id: int,
):
    goal_repo = GoalRepository()
    task_repo = TaskRepository()
    reminder_repo = ReminderRepository()
    delivery_repo = DeliveryRepository()

    with database.transaction() as conn:
        goal = goal_repo.create(
            conn,
            Goal(
                user_id=user_id,
                title="Run a half marathon",
                notes="Train consistently",
            ),
        )

        task_1 = task_repo.create(
            conn,
            Task(
                user_id=user_id,
                goal_id=goal.id,
                title="Run 5 km",
            ),
        )
        task_2 = task_repo.create(
            conn,
            Task(
                user_id=user_id,
                goal_id=goal.id,
                title="Buy running shoes",
            ),
        )

        reminder = reminder_repo.create(
            conn,
            Reminder(
                user_id=user_id,
                task_id=task_1.id,
                purpose=ReminderPurpose.TASK_REMINDER,
                schedule=ReminderSchedule(
                    start_date=date(2026, 8, 23),
                    local_time=time(9, 0),
                    timezone="Asia/Singapore",
                    recurrence=RecurrenceRule(
                        frequency=Frequency.WEEKLY,
                        by_day=(WeekdayRule(Weekday.SUNDAY),),
                    ),
                ),
                next_fire_at_utc=datetime(
                    2026,
                    8,
                    23,
                    1,
                    0,
                    tzinfo=UTC,
                ),
            ),
        )

        delivery_1 = delivery_repo.create(
            conn,
            Delivery(
                user_id=user_id,
                reminder_id=reminder.id,
                reminder_version=reminder.version,
                scheduled_for_utc=datetime(
                    2026,
                    8,
                    23,
                    1,
                    0,
                    tzinfo=UTC,
                ),
            ),
        )
        delivery_2 = delivery_repo.create(
            conn,
            Delivery(
                user_id=user_id,
                reminder_id=reminder.id,
                reminder_version=reminder.version,
                scheduled_for_utc=datetime(
                    2026,
                    8,
                    30,
                    1,
                    0,
                    tzinfo=UTC,
                ),
            ),
        )

    restarted_database = Database(
        database.path,
        migrations_dir=database.migrations_dir,
    )
    restarted_database.migrate()

    with restarted_database.connect() as conn:
        loaded_timezone = UserRepository().get_timezone(conn, user_id)
        loaded_goal = goal_repo.get(conn, goal.id)
        loaded_tasks = task_repo.list_by_goal(conn, goal.id)
        loaded_reminders = reminder_repo.list_by_task(conn, task_1.id)
        loaded_deliveries = delivery_repo.list_by_reminder(
            conn,
            reminder.id,
        )

    assert loaded_timezone == "Asia/Singapore"
    assert loaded_goal == goal
    assert loaded_tasks == [task_1, task_2]
    assert loaded_reminders == [reminder]
    assert loaded_deliveries == [delivery_1, delivery_2]
