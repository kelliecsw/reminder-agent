from pathlib import Path
import shutil

from reminder_agent.db.database import Database


SOURCE_MIGRATIONS = (
    Path(__file__).resolve().parents[3]
    / "reminder_agent"
    / "db"
    / "migrations"
)


def test_reliability_migration_upgrades_part_6_database(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name in ("001_initial.sql", "002_scheduler.sql", "003_telegram.sql"):
        shutil.copy2(SOURCE_MIGRATIONS / name, migrations)
    database = Database(tmp_path / "upgrade.db", migrations_dir=migrations)
    database.migrate()

    with database.transaction() as conn:
        conn.execute("INSERT INTO users (timezone) VALUES ('UTC')")
        conn.execute("INSERT INTO tasks (user_id, title) VALUES (1, 'Existing task')")
        conn.execute(
            """
            INSERT INTO reminders (
                user_id, task_id, purpose, start_date_local, local_time,
                timezone, next_fire_at_utc
            ) VALUES (
                1, 1, 'task_reminder', '2026-09-09', '09:00:00',
                'UTC', '2026-09-09T09:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO deliveries (
                user_id, reminder_id, reminder_version,
                scheduled_for_utc, deliver_at_utc
            ) VALUES (
                1, 1, 1,
                '2026-09-09T09:00:00+00:00',
                '2026-09-09T09:00:00+00:00'
            )
            """
        )

    shutil.copy2(SOURCE_MIGRATIONS / "004_reliability.sql", migrations)
    database.migrate()

    with database.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "telegram_updates",
            "telegram_outbox",
            "agent_threads",
            "tool_executions",
        } <= tables
        delivery = conn.execute(
            """
            SELECT status, claim_token, claimed_at_utc, lease_expires_at_utc
            FROM deliveries WHERE id = 1
            """
        ).fetchone()
        assert dict(delivery) == {
            "status": "pending",
            "claim_token": None,
            "claimed_at_utc": None,
            "lease_expires_at_utc": None,
        }

    shutil.copy2(SOURCE_MIGRATIONS / "005_outbox_entities.sql", migrations)
    database.migrate()

    with database.connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(telegram_outbox)")
        }
    assert "entities_json" in columns

    shutil.copy2(SOURCE_MIGRATIONS / "006_outbox_rich_message.sql", migrations)
    database.migrate()

    with database.connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(telegram_outbox)")
        }
    assert "rich_message_json" in columns

    shutil.copy2(SOURCE_MIGRATIONS / "007_drop_outbox_entities.sql", migrations)
    database.migrate()

    with database.connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(telegram_outbox)")
        }
    assert "rich_message_json" in columns
    assert "entities_json" not in columns
