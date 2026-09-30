from dataclasses import replace

from reminder_agent.db.database import Database
from reminder_agent.domain.enums import GoalStatus
from reminder_agent.domain.models import Goal
from reminder_agent.repositories.goals import GoalRepository


def test_goal_create_read_list_and_update(
    database: Database,
    user_id: int,
):
    repo = GoalRepository()

    goal = Goal(
        user_id=user_id,
        title="Run a half marathon",
        notes="Train consistently",
    )

    with database.transaction() as conn:
        saved = repo.create(conn, goal)

    assert saved.id is not None

    with database.connect() as conn:
        loaded = repo.get(conn, saved.id)
        listed = repo.list_by_user(conn, user_id)

    assert loaded == saved
    assert listed == [saved]

    updated_goal = replace(
        saved,
        title="Complete a half marathon",
        status=GoalStatus.PAUSED,
    )

    with database.transaction() as conn:
        assert repo.update(conn, updated_goal) is True

    with database.connect() as conn:
        assert repo.get(conn, saved.id) == updated_goal
