from pathlib import Path
import shutil
import sqlite3

import pytest

from reminder_agent.db.database import Database


SOURCE_MIGRATIONS = (
    Path(__file__).resolve().parents[3]
    / "reminder_agent"
    / "db"
    / "migrations"
)


def test_scheduler_migration_preserves_existing_deliveries(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    shutil.copy2(SOURCE_MIGRATIONS / "001_initial.sql", migrations)
    database = Database(tmp_path / "upgrade.db", migrations_dir=migrations)
    database.migrate()

    with database.transaction() as conn:
        conn.execute("INSERT INTO users (timezone) VALUES ('UTC')")
        conn.execute(
            "INSERT INTO tasks (user_id, title) VALUES (1, 'Existing task')"
        )
        conn.execute(
            """
            INSERT INTO reminders (
                user_id, task_id, purpose, start_date_local, local_time,
                timezone, next_fire_at_utc
            )
            VALUES (
                1, 1, 'task_reminder', '2026-08-28', '09:00:00',
                'UTC', '2026-08-28T09:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO deliveries (
                user_id, reminder_id, reminder_version,
                scheduled_for_utc, status
            )
            VALUES (1, 1, 1, '2026-08-28T09:00:00+00:00', 'pending')
            """
        )

    shutil.copy2(SOURCE_MIGRATIONS / "002_scheduler.sql", migrations)
    database.migrate()

    with database.connect() as conn:
        row = conn.execute(
            """
            SELECT scheduled_for_utc, deliver_at_utc, attempt_count, last_error
            FROM deliveries
            WHERE id = 1
            """
        ).fetchone()
        assert dict(row) == {
            "scheduled_for_utc": "2026-08-28T09:00:00+00:00",
            "deliver_at_utc": "2026-08-28T09:00:00+00:00",
            "attempt_count": 0,
            "last_error": None,
        }

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO deliveries (
                    user_id, reminder_id, reminder_version,
                    scheduled_for_utc, deliver_at_utc
                )
                VALUES (
                    1, 1, 1, '2026-08-28T09:00:00+00:00',
                    '2026-08-28T09:00:00+00:00'
                )
                """
            )
