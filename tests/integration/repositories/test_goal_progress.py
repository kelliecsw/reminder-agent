from datetime import date

from reminder_agent.db.database import Database
from reminder_agent.domain.models import Goal, GoalProgress
from reminder_agent.repositories.goal_progress import GoalProgressRepository
from reminder_agent.repositories.goals import GoalRepository


def test_goal_progress_create_get_and_list(database: Database, user_id: int):
    goals = GoalRepository()
    progress = GoalProgressRepository()
    with database.transaction() as conn:
        goal = goals.create(conn, Goal(user_id=user_id, title="Train"))
        saved = progress.create(
            conn,
            GoalProgress(
                goal_id=goal.id,
                user_id=user_id,
                occurred_on=date(2026, 8, 20),
                note="Ran 5 km",
            ),
        )

    with database.connect() as conn:
        assert progress.get(conn, saved.id) == saved
        assert progress.list_by_goal(conn, goal.id) == [saved]
