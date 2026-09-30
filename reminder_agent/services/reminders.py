from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from reminder_agent.clock import utc_now
from reminder_agent.db.database import Database
from reminder_agent.domain.enums import (
    GoalStatus,
    ReminderPurpose,
    ReminderStatus,
    TaskStatus,
)
from reminder_agent.domain.errors import (
    InvalidReminderTargetError,
    InvalidStateTransitionError,
    NoFutureOccurrenceError,
)
from reminder_agent.domain.models import Reminder
from reminder_agent.domain.recurrence import ReminderSchedule, next_occurrence
from reminder_agent.repositories.goals import GoalRepository
from reminder_agent.repositories.reminders import ReminderRepository
from reminder_agent.repositories.tasks import TaskRepository
from reminder_agent.repositories.users import UserRepository

from ._common import (
    cancel_live_reminders,
    require_goal,
    require_reminder,
    require_task,
    require_user,
)


class ReminderService:
    def __init__(
        self,
        database: Database,
        *,
        reminder_repo: ReminderRepository | None = None,
        task_repo: TaskRepository | None = None,
        goal_repo: GoalRepository | None = None,
        user_repo: UserRepository | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.database = database
        self.reminder_repo = reminder_repo or ReminderRepository()
        self.task_repo = task_repo or TaskRepository()
        self.goal_repo = goal_repo or GoalRepository()
        self.user_repo = user_repo or UserRepository()
        self.clock = clock

    def create(
        self,
        user_id: int,
        *,
        purpose: ReminderPurpose,
        schedule: ReminderSchedule,
        task_id: int | None = None,
        goal_id: int | None = None,
    ) -> Reminder:
        with self.database.transaction() as conn:
            require_user(conn, self.user_repo, user_id)
            self._validate_target(
                conn,
                user_id=user_id,
                purpose=purpose,
                task_id=task_id,
                goal_id=goal_id,
            )
            next_fire = self._next_or_raise(schedule)
            reminder = Reminder(
                user_id=user_id,
                task_id=task_id,
                goal_id=goal_id,
                purpose=purpose,
                schedule=schedule,
                next_fire_at_utc=next_fire,
            )
            return self.reminder_repo.create(conn, reminder)

    def get(self, user_id: int, reminder_id: int) -> Reminder:
        with self.database.connect() as conn:
            return require_reminder(
                conn, self.reminder_repo, user_id, reminder_id
            )

    def list(self, user_id: int) -> list[Reminder]:
        with self.database.connect() as conn:
            require_user(conn, self.user_repo, user_id)
            return self.reminder_repo.list_by_user(conn, user_id)

    def reschedule(
        self,
        user_id: int,
        reminder_id: int,
        schedule: ReminderSchedule,
    ) -> Reminder:
        with self.database.transaction() as conn:
            reminder = require_reminder(
                conn, self.reminder_repo, user_id, reminder_id
            )
            self._require_live(reminder, action="reschedule")
            updated = replace(
                reminder,
                schedule=schedule,
                next_fire_at_utc=self._next_or_raise(schedule),
                version=reminder.version + 1,
            )
            self.reminder_repo.update(conn, updated)
            return updated

    def pause(self, user_id: int, reminder_id: int) -> Reminder:
        with self.database.transaction() as conn:
            reminder = require_reminder(
                conn, self.reminder_repo, user_id, reminder_id
            )
            if reminder.status != ReminderStatus.ACTIVE:
                raise InvalidStateTransitionError(
                    f"Cannot pause {reminder.status.value} reminder {reminder_id}."
                )
            updated = replace(
                reminder,
                status=ReminderStatus.PAUSED,
                version=reminder.version + 1,
            )
            self.reminder_repo.update(conn, updated)
            return updated

    def resume(self, user_id: int, reminder_id: int) -> Reminder:
        with self.database.transaction() as conn:
            reminder = require_reminder(
                conn, self.reminder_repo, user_id, reminder_id
            )
            if reminder.status != ReminderStatus.PAUSED:
                raise InvalidStateTransitionError(
                    f"Cannot resume {reminder.status.value} reminder {reminder_id}."
                )
            self._validate_target(
                conn,
                user_id=user_id,
                purpose=reminder.purpose,
                task_id=reminder.task_id,
                goal_id=reminder.goal_id,
            )
            updated = replace(
                reminder,
                status=ReminderStatus.ACTIVE,
                next_fire_at_utc=self._next_or_raise(reminder.schedule),
                version=reminder.version + 1,
            )
            self.reminder_repo.update(conn, updated)
            return updated

    def cancel_for_task(self, user_id: int, task_id: int) -> list[Reminder]:
        with self.database.transaction() as conn:
            require_task(conn, self.task_repo, user_id, task_id)
            return cancel_live_reminders(
                conn,
                self.reminder_repo,
                self.reminder_repo.list_by_task(conn, task_id),
            )

    def cancel_for_goal(self, user_id: int, goal_id: int) -> list[Reminder]:
        with self.database.transaction() as conn:
            require_goal(conn, self.goal_repo, user_id, goal_id)
            return cancel_live_reminders(
                conn,
                self.reminder_repo,
                self.reminder_repo.list_by_goal(conn, goal_id),
            )

    def complete(self, user_id: int, reminder_id: int) -> Reminder:
        with self.database.transaction() as conn:
            reminder = require_reminder(
                conn, self.reminder_repo, user_id, reminder_id
            )
            self._require_live(reminder, action="complete")
            updated = replace(
                reminder,
                status=ReminderStatus.COMPLETED,
                next_fire_at_utc=None,
                version=reminder.version + 1,
            )
            self.reminder_repo.update(conn, updated)
            return updated

    def _next_or_raise(self, schedule: ReminderSchedule) -> datetime:
        occurrence = next_occurrence(schedule, after_utc=self.clock())
        if occurrence is None:
            raise NoFutureOccurrenceError(
                "Reminder schedule has no occurrence after the current time."
            )
        return occurrence

    @staticmethod
    def _require_live(reminder: Reminder, *, action: str) -> None:
        if reminder.status in {ReminderStatus.COMPLETED, ReminderStatus.CANCELLED}:
            raise InvalidStateTransitionError(
                f"Cannot {action} {reminder.status.value} reminder {reminder.id}."
            )

    def _validate_target(
        self,
        conn,
        *,
        user_id: int,
        purpose: ReminderPurpose,
        task_id: int | None,
        goal_id: int | None,
    ) -> None:
        if purpose == ReminderPurpose.TASK_REMINDER and task_id is not None and goal_id is None:
            task = require_task(conn, self.task_repo, user_id, task_id)
            if task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
                raise InvalidStateTransitionError(
                    f"Cannot remind {task.status.value} task {task_id}."
                )
            return
        if purpose == ReminderPurpose.GOAL_CHECK_IN and goal_id is not None and task_id is None:
            goal = require_goal(conn, self.goal_repo, user_id, goal_id)
            if goal.status in {GoalStatus.COMPLETED, GoalStatus.CANCELLED}:
                raise InvalidStateTransitionError(
                    f"Cannot remind {goal.status.value} goal {goal_id}."
                )
            return
        raise InvalidReminderTargetError(
            "Reminder purpose and target must identify exactly one matching task or goal."
        )
