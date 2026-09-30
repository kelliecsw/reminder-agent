from datetime import UTC, datetime

import pytest

from reminder_agent.ai.tools.registry import ToolContext, create_tool_registry
from reminder_agent.db.database import Database
from reminder_agent.domain.enums import Frequency, ReminderPurpose, Weekday
from reminder_agent.services.goals import GoalService
from reminder_agent.services.reminders import ReminderService
from reminder_agent.services.tasks import TaskService


NOW = datetime(2026, 8, 24, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("recurrence", "frequency", "interval", "days", "month_days", "months"),
    [
        ({"frequency": "daily", "interval": 3}, Frequency.DAILY, 3, (), (), ()),
        (
            {
                "frequency": "weekly",
                "by_day": [{"day": "monday"}, {"day": "thursday"}],
            },
            Frequency.WEEKLY,
            1,
            (Weekday.MONDAY, Weekday.THURSDAY),
            (),
            (),
        ),
        (
            {
                "frequency": "monthly",
                "by_day": [{"day": "saturday", "ordinal": 2}],
            },
            Frequency.MONTHLY,
            1,
            (Weekday.SATURDAY,),
            (),
            (),
        ),
        (
            {
                "frequency": "monthly",
                "by_day": [{"day": "friday", "ordinal": -1}],
            },
            Frequency.MONTHLY,
            1,
            (Weekday.FRIDAY,),
            (),
            (),
        ),
        (
            {
                "frequency": "yearly",
                "by_month": [12],
                "by_month_day": [25],
            },
            Frequency.YEARLY,
            1,
            (),
            (25,),
            (12,),
        ),
    ],
)
def test_structured_recurrence_reaches_domain_schedule(
    database: Database,
    user_id: int,
    recurrence,
    frequency,
    interval,
    days,
    month_days,
    months,
):
    tasks = TaskService(database)
    reminders = ReminderService(database, clock=lambda: NOW)
    registry = create_tool_registry(
        tasks=tasks,
        goals=GoalService(database, clock=lambda: NOW),
        reminders=reminders,
    )
    task = tasks.create(user_id, "Test schedule")
    result = registry.execute(
        name="create_task_reminder",
        arguments={
            "task_id": task.id,
            "schedule": {
                "start_date": "2026-08-25",
                "local_time": "09:00",
                "recurrence": recurrence,
            },
        },
        context=ToolContext(
            user_id=user_id,
            timezone="Asia/Singapore",
            now_utc=NOW,
        ),
    )

    assert result["status"] == "success", result
    reminder = reminders.get(user_id, result["reminder"]["id"])
    rule = reminder.schedule.recurrence
    assert reminder.purpose == ReminderPurpose.TASK_REMINDER
    assert reminder.schedule.timezone == "Asia/Singapore"
    assert rule.frequency == frequency
    assert rule.interval == interval
    assert tuple(item.day for item in rule.by_day) == days
    assert rule.by_month_day == month_days
    assert rule.by_month == months
