from reminder_agent.db.database import Database
from reminder_agent.repositories.users import UserRepository


def test_create_read_and_update_user_timezone(database: Database):
    repo = UserRepository()

    with database.transaction() as conn:
        user_id = repo.create(
            conn,
            timezone="Asia/Singapore",
        )

    with database.connect() as conn:
        assert repo.get_timezone(conn, user_id) == "Asia/Singapore"

    with database.transaction() as conn:
        updated = repo.update_timezone(
            conn,
            user_id,
            timezone="Asia/Tokyo",
        )

    assert updated is True

    with database.connect() as conn:
        assert repo.get_timezone(conn, user_id) == "Asia/Tokyo"
