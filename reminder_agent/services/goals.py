from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, date
from zoneinfo import ZoneInfo

from reminder_agent.clock import utc_now
from reminder_agent.db.database import Database
from reminder_agent.domain.enums import GoalStatus
from reminder_agent.domain.errors import InvalidStateTransitionError
from reminder_agent.domain.models import Goal, GoalProgress
from reminder_agent.repositories.goal_progress import GoalProgressRepository
from reminder_agent.repositories.goals import GoalRepository
from reminder_agent.repositories.reminders import ReminderRepository
from reminder_agent.repositories.users import UserRepository

from ._common import (
    cancel_live_reminders,
    require_goal,
    require_nonempty,
    require_user,
)


_UNSET = object()


class GoalService:
    def __init__(
        self,
        database: Database,
        *,
        goal_repo: GoalRepository | None = None,
        progress_repo: GoalProgressRepository | None = None,
        reminder_repo: ReminderRepository | None = None,
        user_repo: UserRepository | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.database = database
        self.goal_repo = goal_repo or GoalRepository()
        self.progress_repo = progress_repo or GoalProgressRepository()
        self.reminder_repo = reminder_repo or ReminderRepository()
        self.user_repo = user_repo or UserRepository()
        self.clock = clock

    def create(
        self,
        user_id: int,
        title: str,
        *,
        notes: str | None = None,
    ) -> Goal:
        title = require_nonempty(title, field_name="Goal title")
        with self.database.transaction() as conn:
            require_user(conn, self.user_repo, user_id)
            return self.goal_repo.create(
                conn, Goal(user_id=user_id, title=title, notes=notes)
            )

    def list(self, user_id: int) -> list[Goal]:
        with self.database.connect() as conn:
            require_user(conn, self.user_repo, user_id)
            return self.goal_repo.list_by_user(conn, user_id)

    def update(
        self,
        user_id: int,
        goal_id: int,
        *,
        title: str | None = None,
        notes: str | None | object = _UNSET,
    ) -> Goal:
        with self.database.transaction() as conn:
            goal = require_goal(conn, self.goal_repo, user_id, goal_id)
            updated = replace(
                goal,
                title=(
                    require_nonempty(title, field_name="Goal title")
                    if title is not None
                    else goal.title
                ),
                notes=goal.notes if notes is _UNSET else notes,
            )
            self.goal_repo.update(conn, updated)
            return updated

    def complete(self, user_id: int, goal_id: int) -> Goal:
        return self._finish(user_id, goal_id, GoalStatus.COMPLETED)

    def cancel(self, user_id: int, goal_id: int) -> Goal:
        return self._finish(user_id, goal_id, GoalStatus.CANCELLED)

    def pause(self, user_id: int, goal_id: int) -> Goal:
        return self._transition(
            user_id, goal_id, from_status=GoalStatus.ACTIVE, to_status=GoalStatus.PAUSED
        )

    def resume(self, user_id: int, goal_id: int) -> Goal:
        return self._transition(
            user_id, goal_id, from_status=GoalStatus.PAUSED, to_status=GoalStatus.ACTIVE
        )

    def record_progress(
        self,
        user_id: int,
        goal_id: int,
        note: str,
        *,
        occurred_on: date | None = None,
    ) -> GoalProgress:
        note = require_nonempty(note, field_name="Progress note")
        with self.database.transaction() as conn:
            timezone = require_user(conn, self.user_repo, user_id)
            goal = require_goal(conn, self.goal_repo, user_id, goal_id)
            if goal.status in {GoalStatus.COMPLETED, GoalStatus.CANCELLED}:
                raise InvalidStateTransitionError(
                    f"Cannot record progress for {goal.status.value} goal {goal_id}."
                )
            if occurred_on is None:
                occurred_on = self.clock().astimezone(ZoneInfo(timezone)).date()
            return self.progress_repo.create(
                conn,
                GoalProgress(
                    goal_id=goal_id,
                    user_id=user_id,
                    occurred_on=occurred_on,
                    note=note,
                ),
            )

    def list_progress(self, user_id: int, goal_id: int) -> list[GoalProgress]:
        with self.database.connect() as conn:
            require_goal(conn, self.goal_repo, user_id, goal_id)
            return self.progress_repo.list_by_goal(conn, goal_id)

    def _finish(self, user_id: int, goal_id: int, status: GoalStatus) -> Goal:
        with self.database.transaction() as conn:
            goal = require_goal(conn, self.goal_repo, user_id, goal_id)
            if goal.status in {GoalStatus.COMPLETED, GoalStatus.CANCELLED}:
                raise InvalidStateTransitionError(
                    f"Goal {goal_id} is already terminal ({goal.status.value})."
                )
            updated = replace(goal, status=status)
            self.goal_repo.update(conn, updated)
            cancel_live_reminders(
                conn,
                self.reminder_repo,
                self.reminder_repo.list_by_goal(conn, goal_id),
            )
            return updated

    def _transition(
        self,
        user_id: int,
        goal_id: int,
        *,
        from_status: GoalStatus,
        to_status: GoalStatus,
    ) -> Goal:
        with self.database.transaction() as conn:
            goal = require_goal(conn, self.goal_repo, user_id, goal_id)
            if goal.status != from_status:
                raise InvalidStateTransitionError(
                    f"Cannot move goal {goal_id} from {goal.status.value} to {to_status.value}."
                )
            updated = replace(goal, status=to_status)
            self.goal_repo.update(conn, updated)
            return updated
