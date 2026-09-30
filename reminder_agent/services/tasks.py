from __future__ import annotations

from dataclasses import replace

from reminder_agent.db.database import Database
from reminder_agent.domain.enums import GoalStatus, TaskStatus
from reminder_agent.domain.errors import InvalidStateTransitionError
from reminder_agent.domain.models import Task
from reminder_agent.repositories.goals import GoalRepository
from reminder_agent.repositories.reminders import ReminderRepository
from reminder_agent.repositories.tasks import TaskRepository
from reminder_agent.repositories.users import UserRepository

from ._common import (
    cancel_live_reminders,
    require_goal,
    require_nonempty,
    require_task,
    require_user,
)


_UNSET = object()


class TaskService:
    def __init__(
        self,
        database: Database,
        *,
        task_repo: TaskRepository | None = None,
        goal_repo: GoalRepository | None = None,
        reminder_repo: ReminderRepository | None = None,
        user_repo: UserRepository | None = None,
    ) -> None:
        self.database = database
        self.task_repo = task_repo or TaskRepository()
        self.goal_repo = goal_repo or GoalRepository()
        self.reminder_repo = reminder_repo or ReminderRepository()
        self.user_repo = user_repo or UserRepository()

    def create(
        self,
        user_id: int,
        title: str,
        *,
        goal_id: int | None = None,
        notes: str | None = None,
    ) -> Task:
        title = require_nonempty(title, field_name="Task title")
        with self.database.transaction() as conn:
            require_user(conn, self.user_repo, user_id)
            if goal_id is not None:
                goal = require_goal(conn, self.goal_repo, user_id, goal_id)
                if goal.status in {GoalStatus.COMPLETED, GoalStatus.CANCELLED}:
                    raise InvalidStateTransitionError(
                        f"Cannot add a task to {goal.status.value} goal {goal_id}."
                    )
            return self.task_repo.create(
                conn,
                Task(
                    user_id=user_id,
                    goal_id=goal_id,
                    title=title,
                    notes=notes,
                ),
            )

    def list(self, user_id: int, *, goal_id: int | None = None) -> list[Task]:
        with self.database.connect() as conn:
            require_user(conn, self.user_repo, user_id)
            if goal_id is not None:
                require_goal(conn, self.goal_repo, user_id, goal_id)
                return self.task_repo.list_by_goal(conn, goal_id)
            return self.task_repo.list_by_user(conn, user_id)

    def update(
        self,
        user_id: int,
        task_id: int,
        *,
        title: str | None = None,
        notes: str | None | object = _UNSET,
        goal_id: int | None | object = _UNSET,
    ) -> Task:
        with self.database.transaction() as conn:
            task = require_task(conn, self.task_repo, user_id, task_id)
            new_goal_id = task.goal_id if goal_id is _UNSET else goal_id
            if new_goal_id is not None:
                goal = require_goal(conn, self.goal_repo, user_id, new_goal_id)
                if goal.status in {GoalStatus.COMPLETED, GoalStatus.CANCELLED}:
                    raise InvalidStateTransitionError(
                        f"Cannot attach a task to {goal.status.value} goal {new_goal_id}."
                    )
            updated = replace(
                task,
                title=(
                    require_nonempty(title, field_name="Task title")
                    if title is not None
                    else task.title
                ),
                notes=task.notes if notes is _UNSET else notes,
                goal_id=new_goal_id,
            )
            self.task_repo.update(conn, updated)
            return updated

    def complete(self, user_id: int, task_id: int) -> Task:
        return self._finish(user_id, task_id, TaskStatus.COMPLETED)

    def cancel(self, user_id: int, task_id: int) -> Task:
        return self._finish(user_id, task_id, TaskStatus.CANCELLED)

    def pause(self, user_id: int, task_id: int) -> Task:
        return self._transition(
            user_id, task_id, from_status=TaskStatus.ACTIVE, to_status=TaskStatus.PAUSED
        )

    def resume(self, user_id: int, task_id: int) -> Task:
        return self._transition(
            user_id, task_id, from_status=TaskStatus.PAUSED, to_status=TaskStatus.ACTIVE
        )

    def _finish(self, user_id: int, task_id: int, status: TaskStatus) -> Task:
        with self.database.transaction() as conn:
            task = require_task(conn, self.task_repo, user_id, task_id)
            if task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
                raise InvalidStateTransitionError(
                    f"Task {task_id} is already terminal ({task.status.value})."
                )
            updated = replace(task, status=status)
            self.task_repo.update(conn, updated)
            cancel_live_reminders(
                conn,
                self.reminder_repo,
                self.reminder_repo.list_by_task(conn, task_id),
            )
            return updated

    def _transition(
        self,
        user_id: int,
        task_id: int,
        *,
        from_status: TaskStatus,
        to_status: TaskStatus,
    ) -> Task:
        with self.database.transaction() as conn:
            task = require_task(conn, self.task_repo, user_id, task_id)
            if task.status != from_status:
                raise InvalidStateTransitionError(
                    f"Cannot move task {task_id} from {task.status.value} to {to_status.value}."
                )
            updated = replace(task, status=to_status)
            self.task_repo.update(conn, updated)
            return updated

