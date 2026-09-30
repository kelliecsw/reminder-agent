from datetime import UTC, date, datetime, time

import pytest

from reminder_agent.domain.enums import ReminderPurpose
from reminder_agent.domain.errors import InvalidReminderTargetError
from reminder_agent.domain.models import Delivery, Goal, Reminder, Task
from reminder_agent.domain.recurrence import ReminderSchedule


def _schedule() -> ReminderSchedule:
    return ReminderSchedule(
        start_date=date(2026, 8, 22),
        local_time=time(9, 0),
        timezone="Asia/Singapore",
    )


def test_task_can_exist_without_goal():
    task = Task(
        user_id=1,
        title="Buy printer ink",
    )

    assert task.goal_id is None


def test_goal_can_have_multiple_tasks():
    goal = Goal(
        id=10,
        user_id=1,
        title="Run a half marathon",
    )

    task_1 = Task(
        user_id=1,
        title="Run 5 km",
        goal_id=goal.id,
    )

    task_2 = Task(
        user_id=1,
        title="Buy running shoes",
        goal_id=goal.id,
    )

    assert task_1.goal_id == goal.id
    assert task_2.goal_id == goal.id


def test_task_reminder_can_target_task():
    reminder = Reminder(
        user_id=1,
        task_id=5,
        purpose=ReminderPurpose.TASK_REMINDER,
        schedule=_schedule(),
    )

    assert reminder.task_id == 5
    assert reminder.goal_id is None


def test_goal_check_in_can_target_goal():
    reminder = Reminder(
        user_id=1,
        goal_id=10,
        purpose=ReminderPurpose.GOAL_CHECK_IN,
        schedule=_schedule(),
    )

    assert reminder.goal_id == 10
    assert reminder.task_id is None


def test_reminder_cannot_target_both_goal_and_task():
    with pytest.raises(InvalidReminderTargetError):
        Reminder(
            user_id=1,
            task_id=5,
            goal_id=10,
            purpose=ReminderPurpose.TASK_REMINDER,
            schedule=_schedule(),
        )


def test_reminder_must_target_either_goal_or_task():
    with pytest.raises(InvalidReminderTargetError):
        Reminder(
            user_id=1,
            purpose=ReminderPurpose.TASK_REMINDER,
            schedule=_schedule(),
        )


def test_task_reminder_must_target_task():
    with pytest.raises(InvalidReminderTargetError):
        Reminder(
            user_id=1,
            goal_id=10,
            purpose=ReminderPurpose.TASK_REMINDER,
            schedule=_schedule(),
        )


def test_goal_check_in_must_target_goal():
    with pytest.raises(InvalidReminderTargetError):
        Reminder(
            user_id=1,
            task_id=5,
            purpose=ReminderPurpose.GOAL_CHECK_IN,
            schedule=_schedule(),
        )


def test_multiple_deliveries_can_reference_same_reminder():
    delivery_1 = Delivery(
        user_id=1,
        reminder_id=5,
        reminder_version=1,
        scheduled_for_utc=datetime(2026, 8, 22, 1, 0, tzinfo=UTC),
    )

    delivery_2 = Delivery(
        user_id=1,
        reminder_id=5,
        reminder_version=1,
        scheduled_for_utc=datetime(2026, 8, 29, 1, 0, tzinfo=UTC),
    )

    assert delivery_1.reminder_id == 5
    assert delivery_2.reminder_id == 5
