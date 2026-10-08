from pathlib import Path
import shutil

from reminder_agent.db.database import Database


SOURCE_MIGRATIONS = (
    Path(__file__).resolve().parents[3]
    / "reminder_agent"
    / "db"
    / "migrations"
)


def test_agent_thread_compression_migration_backfills_existing_thread(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for migration in sorted(SOURCE_MIGRATIONS.glob("*.sql")):
        if migration.name == "009_agent_thread_compression.sql":
            continue
        shutil.copy2(migration, migrations)

    database = Database(tmp_path / "upgrade.db", migrations_dir=migrations)
    database.migrate()
    with database.transaction() as conn:
        conn.execute("INSERT INTO users (timezone) VALUES ('UTC')")
        conn.execute(
            """
            INSERT INTO agent_threads (
                application_user_id, messages_json, version,
                created_at_utc, updated_at_utc
            ) VALUES (1, '[]', 1, '2026-01-01T00:00:00+00:00',
                      '2026-01-01T00:00:00+00:00')
            """
        )

    shutil.copy2(
        SOURCE_MIGRATIONS / "009_agent_thread_compression.sql",
        migrations,
    )
    database.migrate()

    with database.connect() as conn:
        row = conn.execute(
            """
            SELECT context_summary, compression_step_count
            FROM agent_threads WHERE application_user_id = 1
            """
        ).fetchone()

    assert dict(row) == {
        "context_summary": "",
        "compression_step_count": 0,
    }
