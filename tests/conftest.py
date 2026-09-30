from pathlib import Path

import pytest

from reminder_agent.db.database import Database
from reminder_agent.repositories.users import UserRepository


MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[1]
    / "reminder_agent"
    / "db"
    / "migrations"
)


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(
        tmp_path / "test.db",
        migrations_dir=MIGRATIONS_DIR,
    )
    db.migrate()
    return db


@pytest.fixture
def user_id(database: Database) -> int:
    repo = UserRepository()

    with database.transaction() as conn:
        return repo.create(
            conn,
            timezone="Asia/Singapore",
        )
