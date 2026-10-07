from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from zoneinfo import ZoneInfo

from reminder_agent.clock import utc_now
from reminder_agent.db.database import Database
from reminder_agent.domain.enums import (
    DeliveryStatus,
    GoalStatus,
    ReminderPurpose,
    ReminderStatus,
)
from reminder_agent.domain.errors import (
    InvalidOwnershipError,
    InvalidStateTransitionError,
    NotFoundError,
)
from reminder_agent.domain.models import Delivery, GoalProgress
from reminder_agent.repositories.deliveries import DeliveryRepository
from reminder_agent.repositories.goal_progress import GoalProgressRepository
from reminder_agent.repositories.goals import GoalRepository
from reminder_agent.repositories.reminders import ReminderRepository
from reminder_agent.repositories.tasks import TaskRepository
from reminder_agent.repositories.users import UserRepository


@dataclass(frozen=True, slots=True)
class DoneResult:
    delivery: Delivery
    reminder_purpose: ReminderPurpose


class DeliveryActionService:
    def __init__(
        self,
        database: Database,
        *,
        delivery_repo: DeliveryRepository | None = None,
        reminder_repo: ReminderRepository | None = None,
        task_repo: TaskRepository | None = None,
        goal_repo: GoalRepository | None = None,
        progress_repo: GoalProgressRepository | None = None,
        user_repo: UserRepository | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.database = database
        self.delivery_repo = delivery_repo or DeliveryRepository()
        self.reminder_repo = reminder_repo or ReminderRepository()
        self.task_repo = task_repo or TaskRepository()
        self.goal_repo = goal_repo or GoalRepository()
        self.progress_repo = progress_repo or GoalProgressRepository()
        self.user_repo = user_repo or UserRepository()
        self.clock = clock

    def done(
        self,
        user_id: int,
        delivery_id: int,
    ) -> DoneResult:
        with self.database.transaction(immediate=True) as conn:
            delivery, reminder = self._require_current_sent_delivery(
                conn,
                user_id,
                delivery_id,
            )
            if reminder.task_id is not None:
                task = self.task_repo.get(conn, reminder.task_id)
                if task is None:
                    raise NotFoundError(f"Task {reminder.task_id} was not found.")
                if task.user_id != user_id:
                    raise InvalidOwnershipError(
                        "Task and Delivery ownership do not match."
                    )
                updated = replace(delivery, status=DeliveryStatus.ACTED_ON)
                self.delivery_repo.update(conn, updated)
                return DoneResult(
                    delivery=updated,
                    reminder_purpose=ReminderPurpose.TASK_REMINDER,
                )

            if reminder.goal_id is None:
                raise InvalidStateTransitionError("Reminder has no supported target.")
            goal = self.goal_repo.get(conn, reminder.goal_id)
            if goal is None:
                raise NotFoundError(f"Goal {reminder.goal_id} was not found.")
            if goal.user_id != user_id:
                raise InvalidOwnershipError("Goal and Delivery ownership do not match.")
            if goal.status in {GoalStatus.COMPLETED, GoalStatus.CANCELLED}:
                raise InvalidStateTransitionError(
                    f"Goal {goal.id} is already {goal.status.value}."
                )
            timezone = self.user_repo.get_timezone(conn, user_id)
            if timezone is None:
                raise NotFoundError(f"User {user_id} was not found.")

            self.progress_repo.create(
                conn,
                GoalProgress(
                    goal_id=goal.id,
                    user_id=user_id,
                    occurred_on=self.clock().astimezone(ZoneInfo(timezone)).date(),
                    note="Completed reminder occurrence",
                ),
            )
            updated = replace(delivery, status=DeliveryStatus.ACTED_ON)
            self.delivery_repo.update(conn, updated)
            return DoneResult(
                delivery=updated,
                reminder_purpose=ReminderPurpose.GOAL_REMINDER,
            )

    def pause_reminder_from_delivery(
        self,
        user_id: int,
        delivery_id: int,
    ) -> Delivery:
        with self.database.transaction(immediate=True) as conn:
            delivery, reminder = self._require_current_sent_delivery(
                conn,
                user_id,
                delivery_id,
            )
            if reminder.status != ReminderStatus.ACTIVE:
                raise InvalidStateTransitionError(
                    f"Cannot pause {reminder.status.value} reminder {reminder.id}."
                )
            self.reminder_repo.update(
                conn,
                replace(
                    reminder,
                    status=ReminderStatus.PAUSED,
                    version=reminder.version + 1,
                ),
            )
            return delivery

    def _require_current_sent_delivery(self, conn, user_id: int, delivery_id: int):
        delivery = self.delivery_repo.get(conn, delivery_id)
        if delivery is None:
            raise NotFoundError(f"Delivery {delivery_id} was not found.")
        if delivery.user_id != user_id:
            raise InvalidOwnershipError(
                f"Delivery {delivery_id} does not belong to user {user_id}."
            )
        if delivery.status != DeliveryStatus.SENT:
            raise InvalidStateTransitionError(
                f"Cannot act on {delivery.status.value} delivery {delivery_id}."
            )

        reminder = self.reminder_repo.get(conn, delivery.reminder_id)
        if reminder is None:
            raise NotFoundError(f"Reminder {delivery.reminder_id} was not found.")
        if reminder.user_id != user_id:
            raise InvalidOwnershipError(
                "Reminder and Delivery ownership do not match."
            )
        if reminder.version != delivery.reminder_version:
            raise InvalidStateTransitionError(
                "Delivery belongs to an older Reminder version."
            )
        if reminder.status not in {ReminderStatus.ACTIVE, ReminderStatus.COMPLETED}:
            raise InvalidStateTransitionError(
                f"Cannot act on a {reminder.status.value} Reminder."
            )
        return delivery, reminder


__all__ = ["DeliveryActionService", "DoneResult"]
