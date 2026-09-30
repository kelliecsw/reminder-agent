from datetime import UTC, date, datetime, time

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
SCHEDULE = ReminderSchedule(
    start_date=date(2026, 8, 22),
    local_time=time(9),
    timezone="Asia/Singapore",
    recurrence=RecurrenceRule(frequency=Frequency.DAILY),
)


def _sent_task_delivery(database, user_id):
    task = TaskService(database).create(user_id, "Submit claim")
    reminder = ReminderService(
        database,
        clock=lambda: NOW.replace(day=21),
    ).create(
        user_id,
        purpose=ReminderPurpose.TASK_REMINDER,
        task_id=task.id,
        schedule=SCHEDULE,
    )
    delivery_service = DeliveryService(database)
    delivery = ReminderMaterializer(delivery_service).run_once(NOW)[0]
    claimed = delivery_service.claim_ready(NOW)
    delivery_service.mark_sent(claimed.id, claim_token=claimed.claim_token)
    return task, reminder, delivery


def test_done_completes_task_and_marks_delivery_atomically(database, user_id):
    task, reminder, delivery = _sent_task_delivery(database, user_id)

    acted_on = DeliveryActionService(database).complete_task_from_delivery(
        user_id,
        delivery.id,
    )

    assert acted_on.status == DeliveryStatus.ACTED_ON
    assert TaskService(database).list(user_id)[0].status == TaskStatus.COMPLETED
    assert ReminderService(database).get(user_id, reminder.id).status == ReminderStatus.CANCELLED


def test_done_on_goal_records_progress_and_marks_delivery(database, user_id):
    goal = GoalService(database).create(user_id, "Exercise consistently")
    reminder = ReminderService(
        database,
        clock=lambda: NOW.replace(day=21),
    ).create(
        user_id,
        purpose=ReminderPurpose.GOAL_CHECK_IN,
        goal_id=goal.id,
        schedule=SCHEDULE,
    )
    deliveries = DeliveryService(database)
    delivery = ReminderMaterializer(deliveries).run_once(NOW)[0]
    claimed = deliveries.claim_ready(NOW)
    deliveries.mark_sent(claimed.id, claim_token=claimed.claim_token)

    acted_on = DeliveryActionService(
        database,
        clock=lambda: NOW,
    ).record_goal_check_in_from_delivery(user_id, delivery.id)

    assert acted_on.status == DeliveryStatus.ACTED_ON
    progress = GoalService(database).list_progress(user_id, goal.id)
    assert len(progress) == 1
    assert progress[0].note == "Checked in via reminder"
    assert progress[0].occurred_on == date(2026, 8, 22)
    assert GoalService(database).list(user_id)[0].status == GoalStatus.ACTIVE


def test_task_completion_rolls_back_if_delivery_update_fails(database, user_id):
    task, reminder, delivery = _sent_task_delivery(database, user_id)

    class FailingDeliveryRepository(DeliveryRepository):
        def update(self, conn, delivery):
            raise RuntimeError("simulated write failure")

    service = DeliveryActionService(
        database,
        delivery_repo=FailingDeliveryRepository(),
    )

    with pytest.raises(RuntimeError, match="simulated write failure"):
        service.complete_task_from_delivery(user_id, delivery.id)

    assert TaskService(database).list(user_id)[0].status == TaskStatus.ACTIVE
    assert ReminderService(database).get(user_id, reminder.id).status == ReminderStatus.ACTIVE
    assert DeliveryService(database).get(user_id, delivery.id).status == DeliveryStatus.SENT
