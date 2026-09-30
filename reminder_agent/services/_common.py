from __future__ import annotations

from dataclasses import replace
import sqlite3

from reminder_agent.domain.enums import ReminderStatus
from reminder_agent.domain.errors import (
    InvalidOwnershipError,
    NotFoundError,
)
from reminder_agent.domain.models import Goal, Reminder, Task
from reminder_agent.repositories.goals import GoalRepository
from reminder_agent.repositories.reminders import ReminderRepository
from reminder_agent.repositories.tasks import TaskRepository
from reminder_agent.repositories.users import UserRepository


def require_user(
    conn: sqlite3.Connection,
    repo: UserRepository,
    user_id: int,
) -> str:
    timezone = repo.get_timezone(conn, user_id)
    if timezone is None:
        raise NotFoundError(f"User {user_id} was not found.")
    return timezone


def require_goal(
    conn: sqlite3.Connection,
    repo: GoalRepository,
    user_id: int,
    goal_id: int,
) -> Goal:
    goal = repo.get(conn, goal_id)
    if goal is None:
        raise NotFoundError(f"Goal {goal_id} was not found.")
    if goal.user_id != user_id:
        raise InvalidOwnershipError(
            f"Goal {goal_id} does not belong to user {user_id}."
        )
    return goal


def require_task(
    conn: sqlite3.Connection,
    repo: TaskRepository,
    user_id: int,
    task_id: int,
) -> Task:
    task = repo.get(conn, task_id)
    if task is None:
        raise NotFoundError(f"Task {task_id} was not found.")
    if task.user_id != user_id:
        raise InvalidOwnershipError(
            f"Task {task_id} does not belong to user {user_id}."
        )
    return task


def require_reminder(
    conn: sqlite3.Connection,
    repo: ReminderRepository,
    user_id: int,
    reminder_id: int,
) -> Reminder:
    reminder = repo.get(conn, reminder_id)
    if reminder is None:
        raise NotFoundError(f"Reminder {reminder_id} was not found.")
    if reminder.user_id != user_id:
        raise InvalidOwnershipError(
            f"Reminder {reminder_id} does not belong to user {user_id}."
        )
    return reminder


def cancel_live_reminders(
    conn: sqlite3.Connection,
    repo: ReminderRepository,
    reminders: list[Reminder],
) -> list[Reminder]:
    cancelled = []
    for reminder in reminders:
        if reminder.status in {ReminderStatus.ACTIVE, ReminderStatus.PAUSED}:
            reminder = replace(
                reminder,
                status=ReminderStatus.CANCELLED,
                next_fire_at_utc=None,
                version=reminder.version + 1,
            )
            repo.update(conn, reminder)
            cancelled.append(reminder)
    return cancelled


def require_nonempty(value: str, *, field_name: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must not be empty.")
    return value

