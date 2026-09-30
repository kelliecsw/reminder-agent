from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
import sqlite3

from reminder_agent.domain.enums import (
    Frequency,
    ReminderPurpose,
    ReminderStatus,
    Weekday,
)
from reminder_agent.domain.models import Reminder
from reminder_agent.domain.recurrence import (
    RecurrenceRule,
    ReminderSchedule,
    WeekdayRule,
)


class ReminderRepository:
    def create(
        self,
        conn: sqlite3.Connection,
        reminder: Reminder,
    ) -> Reminder:
        cursor = conn.execute(
            """
            INSERT INTO reminders (
                user_id,
                goal_id,
                task_id,
                purpose,
                start_date_local,
                local_time,
                timezone,
                recurrence_json,
                next_fire_at_utc,
                status,
                version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reminder.user_id,
                reminder.goal_id,
                reminder.task_id,
                reminder.purpose.value,
                reminder.schedule.start_date.isoformat(),
                reminder.schedule.local_time.isoformat(),
                reminder.schedule.timezone,
                _recurrence_to_json(reminder.schedule.recurrence),
                _datetime_to_text(reminder.next_fire_at_utc),
                reminder.status.value,
                reminder.version,
            ),
        )

        return replace(reminder, id=int(cursor.lastrowid))

    def get(
        self,
        conn: sqlite3.Connection,
        reminder_id: int,
    ) -> Reminder | None:
        row = conn.execute(
            """
            SELECT
                id,
                user_id,
                goal_id,
                task_id,
                purpose,
                start_date_local,
                local_time,
                timezone,
                recurrence_json,
                next_fire_at_utc,
                status,
                version
            FROM reminders
            WHERE id = ?
            """,
            (reminder_id,),
        ).fetchone()

        return _row_to_reminder(row) if row is not None else None

    def list_by_user(
        self,
        conn: sqlite3.Connection,
        user_id: int,
    ) -> list[Reminder]:
        rows = conn.execute(
            """
            SELECT
                id,
                user_id,
                goal_id,
                task_id,
                purpose,
                start_date_local,
                local_time,
                timezone,
                recurrence_json,
                next_fire_at_utc,
                status,
                version
            FROM reminders
            WHERE user_id = ?
            ORDER BY id
            """,
            (user_id,),
        ).fetchall()

        return [_row_to_reminder(row) for row in rows]

    def list_by_task(
        self,
        conn: sqlite3.Connection,
        task_id: int,
    ) -> list[Reminder]:
        rows = conn.execute(
            """
            SELECT
                id,
                user_id,
                goal_id,
                task_id,
                purpose,
                start_date_local,
                local_time,
                timezone,
                recurrence_json,
                next_fire_at_utc,
                status,
                version
            FROM reminders
            WHERE task_id = ?
            ORDER BY id
            """,
            (task_id,),
        ).fetchall()

        return [_row_to_reminder(row) for row in rows]

    def list_by_goal(
        self,
        conn: sqlite3.Connection,
        goal_id: int,
    ) -> list[Reminder]:
        rows = conn.execute(
            """
            SELECT
                id,
                user_id,
                goal_id,
                task_id,
                purpose,
                start_date_local,
                local_time,
                timezone,
                recurrence_json,
                next_fire_at_utc,
                status,
                version
            FROM reminders
            WHERE goal_id = ?
            ORDER BY id
            """,
            (goal_id,),
        ).fetchall()

        return [_row_to_reminder(row) for row in rows]

    def find_due(
        self,
        conn: sqlite3.Connection,
        now_utc: datetime,
        *,
        limit: int = 100,
    ) -> list[Reminder]:
        rows = conn.execute(
            """
            SELECT
                id,
                user_id,
                goal_id,
                task_id,
                purpose,
                start_date_local,
                local_time,
                timezone,
                recurrence_json,
                next_fire_at_utc,
                status,
                version
            FROM reminders
            WHERE status = ?
              AND next_fire_at_utc IS NOT NULL
              AND next_fire_at_utc <= ?
            ORDER BY next_fire_at_utc, id
            LIMIT ?
            """,
            (
                ReminderStatus.ACTIVE.value,
                now_utc.isoformat(),
                limit,
            ),
        ).fetchall()

        return [_row_to_reminder(row) for row in rows]

    def update(
        self,
        conn: sqlite3.Connection,
        reminder: Reminder,
    ) -> bool:
        if reminder.id is None:
            raise ValueError("Cannot update a Reminder without an id.")

        cursor = conn.execute(
            """
            UPDATE reminders
            SET goal_id = ?,
                task_id = ?,
                purpose = ?,
                start_date_local = ?,
                local_time = ?,
                timezone = ?,
                recurrence_json = ?,
                next_fire_at_utc = ?,
                status = ?,
                version = ?,
                updated_at_utc = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                reminder.goal_id,
                reminder.task_id,
                reminder.purpose.value,
                reminder.schedule.start_date.isoformat(),
                reminder.schedule.local_time.isoformat(),
                reminder.schedule.timezone,
                _recurrence_to_json(reminder.schedule.recurrence),
                _datetime_to_text(reminder.next_fire_at_utc),
                reminder.status.value,
                reminder.version,
                reminder.id,
            ),
        )

        return cursor.rowcount == 1


def _row_to_reminder(row: sqlite3.Row) -> Reminder:
    goal_id = row["goal_id"]
    task_id = row["task_id"]

    schedule = ReminderSchedule(
        start_date=datetime.strptime(
            row["start_date_local"],
            "%Y-%m-%d",
        ).date(),
        local_time=datetime.fromisoformat(
            f"2000-01-01T{row['local_time']}"
        ).time(),
        timezone=str(row["timezone"]),
        recurrence=_recurrence_from_json(row["recurrence_json"]),
    )

    next_fire_at_utc = (
        datetime.fromisoformat(row["next_fire_at_utc"])
        if row["next_fire_at_utc"] is not None
        else None
    )

    return Reminder(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        goal_id=int(goal_id) if goal_id is not None else None,
        task_id=int(task_id) if task_id is not None else None,
        purpose=ReminderPurpose(row["purpose"]),
        schedule=schedule,
        next_fire_at_utc=next_fire_at_utc,
        status=ReminderStatus(row["status"]),
        version=int(row["version"]),
    )


def _recurrence_to_json(
    recurrence: RecurrenceRule | None,
) -> str | None:
    if recurrence is None:
        return None

    payload = {
        "frequency": recurrence.frequency.value,
        "interval": recurrence.interval,
        "by_day": [
            {
                "day": int(rule.day),
                "ordinal": rule.ordinal,
            }
            for rule in recurrence.by_day
        ],
        "by_month_day": list(recurrence.by_month_day),
        "by_month": list(recurrence.by_month),
        "by_set_position": list(recurrence.by_set_position),
        "count": recurrence.count,
        "until": _datetime_to_text(recurrence.until),
    }

    return json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    )


def _recurrence_from_json(
    value: str | None,
) -> RecurrenceRule | None:
    if value is None:
        return None

    payload = json.loads(value)

    return RecurrenceRule(
        frequency=Frequency(payload["frequency"]),
        interval=int(payload["interval"]),
        by_day=tuple(
            WeekdayRule(
                day=Weekday(item["day"]),
                ordinal=item["ordinal"],
            )
            for item in payload["by_day"]
        ),
        by_month_day=tuple(
            int(item)
            for item in payload["by_month_day"]
        ),
        by_month=tuple(
            int(item)
            for item in payload["by_month"]
        ),
        by_set_position=tuple(
            int(item)
            for item in payload["by_set_position"]
        ),
        count=(
            int(payload["count"])
            if payload["count"] is not None
            else None
        ),
        until=(
            datetime.fromisoformat(payload["until"])
            if payload["until"] is not None
            else None
        ),
    )


def _datetime_to_text(
    value: datetime | None,
) -> str | None:
    return value.isoformat() if value is not None else None
