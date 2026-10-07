from pathlib import Path
import shutil

from reminder_agent.db.database import Database


SOURCE_MIGRATIONS = (
    Path(__file__).resolve().parents[3]
    / "reminder_agent"
    / "db"
    / "migrations"
)


def test_goal_reminder_migration_preserves_existing_relationships(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for migration in sorted(SOURCE_MIGRATIONS.glob("*.sql")):
        if migration.name == "008_goal_reminders.sql":
            continue
        shutil.copy2(migration, migrations)

    database = Database(tmp_path / "upgrade.db", migrations_dir=migrations)
    database.migrate()

    with database.transaction() as conn:
        conn.execute("INSERT INTO users (timezone) VALUES ('UTC')")
        conn.execute("INSERT INTO goals (user_id, title) VALUES (1, 'Train')")
        conn.execute(
            """
            INSERT INTO reminders (
                user_id,
                goal_id,
                purpose,
                start_date_local,
                local_time,
                timezone,
                next_fire_at_utc
            )
            VALUES (
                1,
                1,
                'goal_check_in',
                '2026-09-09',
                '09:00:00',
                'UTC',
                '2026-09-09T09:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO deliveries (
                user_id,
                reminder_id,
                reminder_version,
                scheduled_for_utc,
                deliver_at_utc
            )
            VALUES (
                1,
                1,
                1,
                '2026-09-09T09:00:00+00:00',
                '2026-09-09T09:00:00+00:00'
            )
            """
        )

    shutil.copy2(SOURCE_MIGRATIONS / "008_goal_reminders.sql", migrations)
    database.migrate()

    with database.connect() as conn:
        reminder = conn.execute(
            "SELECT id, purpose, goal_id FROM reminders WHERE id = 1"
        ).fetchone()
        delivery = conn.execute(
            "SELECT id, reminder_id FROM deliveries WHERE id = 1"
        ).fetchone()
        delivery_foreign_keys = conn.execute(
            "PRAGMA foreign_key_list(deliveries)"
        ).fetchall()
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()

    assert dict(reminder) == {
        "id": 1,
        "purpose": "goal_reminder",
        "goal_id": 1,
    }
    assert dict(delivery) == {"id": 1, "reminder_id": 1}
    assert any(row["table"] == "reminders" for row in delivery_foreign_keys)
    assert violations == []
