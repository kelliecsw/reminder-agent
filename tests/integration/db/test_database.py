import sqlite3

import pytest

from reminder_agent.db.database import Database


def test_migration_creates_expected_tables(database: Database):
    expected_tables = {
        "schema_migrations",
        "users",
        "goals",
        "tasks",
        "goal_progress",
        "reminders",
        "deliveries",
        "telegram_accounts",
        "telegram_updates",
        "telegram_outbox",
        "agent_threads",
        "tool_executions",
    }

    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()

    actual_tables = {row["name"] for row in rows}

    assert expected_tables <= actual_tables


def test_database_connection_enables_expected_sqlite_settings(
    database: Database,
):
    with database.connect() as conn:
        foreign_keys = conn.execute(
            "PRAGMA foreign_keys"
        ).fetchone()[0]

        journal_mode = conn.execute(
            "PRAGMA journal_mode"
        ).fetchone()[0]

        busy_timeout = conn.execute(
            "PRAGMA busy_timeout"
        ).fetchone()[0]

    assert foreign_keys == 1
    assert journal_mode.lower() == "wal"
    assert busy_timeout == 5000


def test_running_migrations_twice_is_safe(database: Database):
    database.migrate()

    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT migration_id
            FROM schema_migrations
            WHERE migration_id = '001_initial'
            """
        ).fetchall()

    assert len(rows) == 1


def test_database_rejects_reminder_targeting_goal_and_task(
    database: Database,
):
    with database.transaction() as conn:
        user_id = conn.execute(
            """
            INSERT INTO users (timezone)
            VALUES ('Asia/Singapore')
            """
        ).lastrowid

        goal_id = conn.execute(
            """
            INSERT INTO goals (user_id, title)
            VALUES (?, 'Run a half marathon')
            """,
            (user_id,),
        ).lastrowid

        task_id = conn.execute(
            """
            INSERT INTO tasks (user_id, goal_id, title)
            VALUES (?, ?, 'Run 5 km')
            """,
            (user_id, goal_id),
        ).lastrowid

    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as conn:
            conn.execute(
                """
                INSERT INTO reminders (
                    user_id,
                    goal_id,
                    task_id,
                    purpose,
                    start_date_local,
                    local_time,
                    timezone
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    goal_id,
                    task_id,
                    "task_reminder",
                    "2026-08-22",
                    "09:00:00",
                    "Asia/Singapore",
                ),
            )
