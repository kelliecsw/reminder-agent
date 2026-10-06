from datetime import UTC, date, datetime, time, timedelta

import pytest

from reminder_agent.domain.enums import (
    DeliveryStatus,
    Frequency,
    GoalStatus,
    ReminderPurpose,
    ReminderStatus,
    TaskStatus,
)
from reminder_agent.domain.recurrence import RecurrenceRule, ReminderSchedule
from reminder_agent.repositories.deliveries import DeliveryRepository
from reminder_agent.scheduler.materializer import ReminderMaterializer
from reminder_agent.services.delivery_actions import DeliveryActionService
from reminder_agent.services.deliveries import DeliveryService
from reminder_agent.services.goals import GoalService
from reminder_agent.services.reminders import ReminderService
from reminder_agent.services.tasks import TaskService


NOW = datetime(2026, 8, 22, 1, 0, tzinfo=UTC)
ONE_OFF_SCHEDULE = ReminderSchedule(
    start_date=date(2026, 8, 22),
    local_time=time(9),
    timezone="Asia/Singapore",
)
RECURRING_SCHEDULE = ReminderSchedule(
    start_date=date(2026, 8, 22),
    local_time=time(9),
    timezone="Asia/Singapore",
    recurrence=RecurrenceRule(frequency=Frequency.DAILY),
)
FINAL_RECURRING_SCHEDULE = ReminderSchedule(
    start_date=date(2026, 8, 22),
    local_time=time(9),
    timezone="Asia/Singapore",
    recurrence=RecurrenceRule(frequency=Frequency.DAILY, count=1),
)


def _mark_next_delivery_sent(database, reminder_id):
    deliveries = DeliveryService(database)
    delivery = ReminderMaterializer(deliveries).run_once(NOW)[0]
    assert delivery.reminder_id == reminder_id
    claimed = deliveries.claim_ready(NOW)
    return deliveries.mark_sent(claimed.id, claim_token=claimed.claim_token)


def _sent_task_delivery(database, user_id, schedule):
    task = TaskService(database).create(user_id, "Submit claim")
    reminder = ReminderService(
        database,
        clock=lambda: NOW.replace(day=21),
    ).create(
        user_id,
        purpose=ReminderPurpose.TASK_REMINDER,
        task_id=task.id,
        schedule=schedule,
    )
    return task, reminder, _mark_next_delivery_sent(database, reminder.id)


def _sent_goal_delivery(database, user_id, schedule):
    goal = GoalService(database).create(user_id, "Exercise consistently")
    reminder = ReminderService(
        database,
        clock=lambda: NOW.replace(day=21),
    ).create(
        user_id,
        purpose=ReminderPurpose.GOAL_REMINDER,
        goal_id=goal.id,
        schedule=schedule,
    )
    return goal, reminder, _mark_next_delivery_sent(database, reminder.id)


@pytest.mark.parametrize(
    ("schedule", "expected_reminder_status"),
    [
        pytest.param(ONE_OFF_SCHEDULE, ReminderStatus.COMPLETED, id="one-off"),
        pytest.param(RECURRING_SCHEDULE, ReminderStatus.ACTIVE, id="recurring"),
    ],
)
def test_done_marks_only_task_reminder_occurrence(
    database,
    user_id,
    schedule,
    expected_reminder_status,
):
    task, reminder, delivery = _sent_task_delivery(database, user_id, schedule)

    result = DeliveryActionService(database).done(user_id, delivery.id)

    assert result.reminder_purpose == ReminderPurpose.TASK_REMINDER
    assert result.delivery.status == DeliveryStatus.ACTED_ON
    assert TaskService(database).list(user_id)[0].status == TaskStatus.ACTIVE
    saved_reminder = ReminderService(database).get(user_id, reminder.id)
    assert saved_reminder.status == expected_reminder_status

    if schedule.recurrence is not None:
        assert saved_reminder.next_fire_at_utc == NOW + timedelta(days=1)
        created = ReminderMaterializer(DeliveryService(database)).run_once(
            NOW + timedelta(days=1)
        )
        assert len(created) == 1
        assert created[0].reminder_id == reminder.id


@pytest.mark.parametrize(
    ("schedule", "expected_reminder_status"),
    [
        pytest.param(ONE_OFF_SCHEDULE, ReminderStatus.COMPLETED, id="one-off"),
        pytest.param(RECURRING_SCHEDULE, ReminderStatus.ACTIVE, id="recurring"),
    ],
)
def test_done_records_progress_for_goal_reminder_occurrence(
    database,
    user_id,
    schedule,
    expected_reminder_status,
):
    goal, reminder, delivery = _sent_goal_delivery(database, user_id, schedule)

    result = DeliveryActionService(
        database,
        clock=lambda: NOW,
    ).done(user_id, delivery.id)

    assert result.reminder_purpose == ReminderPurpose.GOAL_REMINDER
    assert result.delivery.status == DeliveryStatus.ACTED_ON
    progress = GoalService(database).list_progress(user_id, goal.id)
    assert len(progress) == 1
    assert progress[0].note == "Completed reminder occurrence"
    assert progress[0].occurred_on == date(2026, 8, 22)
    assert GoalService(database).list(user_id)[0].status == GoalStatus.ACTIVE
    saved_reminder = ReminderService(database).get(user_id, reminder.id)
    assert saved_reminder.status == expected_reminder_status


def test_done_on_final_recurring_occurrence_does_not_complete_task(
    database,
    user_id,
):
    task, reminder, delivery = _sent_task_delivery(
        database,
        user_id,
        FINAL_RECURRING_SCHEDULE,
    )

    result = DeliveryActionService(database).done(user_id, delivery.id)

    assert result.delivery.status == DeliveryStatus.ACTED_ON
    assert TaskService(database).list(user_id)[0].status == TaskStatus.ACTIVE
    assert (
        ReminderService(database).get(user_id, reminder.id).status
        == ReminderStatus.COMPLETED
    )


def test_goal_progress_rolls_back_if_delivery_update_fails(database, user_id):
    goal, reminder, delivery = _sent_goal_delivery(
        database,
        user_id,
        RECURRING_SCHEDULE,
    )

    class FailingDeliveryRepository(DeliveryRepository):
        def update(self, conn, delivery):
            raise RuntimeError("simulated write failure")

    service = DeliveryActionService(
        database,
        delivery_repo=FailingDeliveryRepository(),
        clock=lambda: NOW,
    )

    with pytest.raises(RuntimeError, match="simulated write failure"):
        service.done(user_id, delivery.id)

    assert GoalService(database).list_progress(user_id, goal.id) == []
    assert GoalService(database).list(user_id)[0].status == GoalStatus.ACTIVE
    assert (
        ReminderService(database).get(user_id, reminder.id).status
        == ReminderStatus.ACTIVE
    )
    assert (
        DeliveryService(database).get(user_id, delivery.id).status
        == DeliveryStatus.SENT
    )
