from __future__ import annotations

from dataclasses import replace
import sqlite3

from reminder_agent.domain.enums import GoalStatus
from reminder_agent.domain.models import Goal


class GoalRepository:
    def create(
        self,
        conn: sqlite3.Connection,
        goal: Goal,
    ) -> Goal:
        cursor = conn.execute(
            """
            INSERT INTO goals (
                user_id,
                title,
                notes,
                status
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                goal.user_id,
                goal.title,
                goal.notes,
                goal.status.value,
            ),
        )

        return replace(goal, id=int(cursor.lastrowid))

    def get(
        self,
        conn: sqlite3.Connection,
        goal_id: int,
    ) -> Goal | None:
        row = conn.execute(
            """
            SELECT id, user_id, title, notes, status
            FROM goals
            WHERE id = ?
            """,
            (goal_id,),
        ).fetchone()

        return _row_to_goal(row) if row is not None else None

    def list_by_user(
        self,
        conn: sqlite3.Connection,
        user_id: int,
    ) -> list[Goal]:
        rows = conn.execute(
            """
            SELECT id, user_id, title, notes, status
            FROM goals
            WHERE user_id = ?
            ORDER BY id
            """,
            (user_id,),
        ).fetchall()

        return [_row_to_goal(row) for row in rows]

    def update(
        self,
        conn: sqlite3.Connection,
        goal: Goal,
    ) -> bool:
        if goal.id is None:
            raise ValueError("Cannot update a Goal without an id.")

        cursor = conn.execute(
            """
            UPDATE goals
            SET title = ?,
                notes = ?,
                status = ?,
                updated_at_utc = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                goal.title,
                goal.notes,
                goal.status.value,
                goal.id,
            ),
        )

        return cursor.rowcount == 1


def _row_to_goal(row: sqlite3.Row) -> Goal:
    return Goal(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        title=str(row["title"]),
        notes=row["notes"],
        status=GoalStatus(row["status"]),
    )
