from __future__ import annotations

from dataclasses import replace
from datetime import date
import sqlite3

from reminder_agent.domain.models import GoalProgress


class GoalProgressRepository:
    def create(
        self,
        conn: sqlite3.Connection,
        progress: GoalProgress,
    ) -> GoalProgress:
        cursor = conn.execute(
            """
            INSERT INTO goal_progress (goal_id, user_id, occurred_on, note)
            VALUES (?, ?, ?, ?)
            """,
            (
                progress.goal_id,
                progress.user_id,
                progress.occurred_on.isoformat(),
                progress.note,
            ),
        )
        return replace(progress, id=int(cursor.lastrowid))

    def get(
        self,
        conn: sqlite3.Connection,
        progress_id: int,
    ) -> GoalProgress | None:
        row = conn.execute(
            """
            SELECT id, goal_id, user_id, occurred_on, note
            FROM goal_progress
            WHERE id = ?
            """,
            (progress_id,),
        ).fetchone()
        return _row_to_progress(row) if row is not None else None

    def list_by_goal(
        self,
        conn: sqlite3.Connection,
        goal_id: int,
    ) -> list[GoalProgress]:
        rows = conn.execute(
            """
            SELECT id, goal_id, user_id, occurred_on, note
            FROM goal_progress
            WHERE goal_id = ?
            ORDER BY occurred_on, id
            """,
            (goal_id,),
        ).fetchall()
        return [_row_to_progress(row) for row in rows]


def _row_to_progress(row: sqlite3.Row) -> GoalProgress:
    return GoalProgress(
        id=int(row["id"]),
        goal_id=int(row["goal_id"]),
        user_id=int(row["user_id"]),
        occurred_on=date.fromisoformat(row["occurred_on"]),
        note=str(row["note"]),
    )
