from __future__ import annotations

from dataclasses import replace
import sqlite3

from reminder_agent.domain.enums import TaskStatus
from reminder_agent.domain.models import Task


class TaskRepository:
    def create(
        self,
        conn: sqlite3.Connection,
        task: Task,
    ) -> Task:
        cursor = conn.execute(
            """
            INSERT INTO tasks (
                user_id,
                goal_id,
                title,
                notes,
                status
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                task.user_id,
                task.goal_id,
                task.title,
                task.notes,
                task.status.value,
            ),
        )

        return replace(task, id=int(cursor.lastrowid))

    def get(
        self,
        conn: sqlite3.Connection,
        task_id: int,
    ) -> Task | None:
        row = conn.execute(
            """
            SELECT id, user_id, goal_id, title, notes, status
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()

        return _row_to_task(row) if row is not None else None

    def list_by_user(
        self,
        conn: sqlite3.Connection,
        user_id: int,
    ) -> list[Task]:
        rows = conn.execute(
            """
            SELECT id, user_id, goal_id, title, notes, status
            FROM tasks
            WHERE user_id = ?
            ORDER BY id
            """,
            (user_id,),
        ).fetchall()

        return [_row_to_task(row) for row in rows]

    def list_by_goal(
        self,
        conn: sqlite3.Connection,
        goal_id: int,
    ) -> list[Task]:
        rows = conn.execute(
            """
            SELECT id, user_id, goal_id, title, notes, status
            FROM tasks
            WHERE goal_id = ?
            ORDER BY id
            """,
            (goal_id,),
        ).fetchall()

        return [_row_to_task(row) for row in rows]

    def update(
        self,
        conn: sqlite3.Connection,
        task: Task,
    ) -> bool:
        if task.id is None:
            raise ValueError("Cannot update a Task without an id.")

        cursor = conn.execute(
            """
            UPDATE tasks
            SET goal_id = ?,
                title = ?,
                notes = ?,
                status = ?,
                updated_at_utc = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                task.goal_id,
                task.title,
                task.notes,
                task.status.value,
                task.id,
            ),
        )

        return cursor.rowcount == 1


def _row_to_task(row: sqlite3.Row) -> Task:
    goal_id = row["goal_id"]

    return Task(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        goal_id=int(goal_id) if goal_id is not None else None,
        title=str(row["title"]),
        notes=row["notes"],
        status=TaskStatus(row["status"]),
    )
