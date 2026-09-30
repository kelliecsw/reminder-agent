from dataclasses import replace

from reminder_agent.db.database import Database
from reminder_agent.domain.enums import TaskStatus
from reminder_agent.domain.models import Goal, Task
from reminder_agent.repositories.goals import GoalRepository
from reminder_agent.repositories.tasks import TaskRepository


def test_standalone_task_round_trips(
    database: Database,
    user_id: int,
):
    repo = TaskRepository()

    task = Task(
        user_id=user_id,
        title="Buy printer ink",
    )

    with database.transaction() as conn:
        saved = repo.create(conn, task)

    with database.connect() as conn:
        loaded = repo.get(conn, saved.id)

    assert loaded == saved
    assert loaded.goal_id is None


def test_goal_can_have_multiple_persisted_tasks(
    database: Database,
    user_id: int,
):
    goal_repo = GoalRepository()
    task_repo = TaskRepository()

    with database.transaction() as conn:
        goal = goal_repo.create(
            conn,
            Goal(
                user_id=user_id,
                title="Run a half marathon",
            ),
        )

        task_1 = task_repo.create(
            conn,
            Task(
                user_id=user_id,
                goal_id=goal.id,
                title="Run 5 km",
            ),
        )

        task_2 = task_repo.create(
            conn,
            Task(
                user_id=user_id,
                goal_id=goal.id,
                title="Buy running shoes",
            ),
        )

    with database.connect() as conn:
        tasks = task_repo.list_by_goal(conn, goal.id)

    assert tasks == [task_1, task_2]


def test_task_update_is_persisted(
    database: Database,
    user_id: int,
):
    repo = TaskRepository()

    with database.transaction() as conn:
        saved = repo.create(
            conn,
            Task(
                user_id=user_id,
                title="Buy printer ink",
            ),
        )

    updated_task = replace(
        saved,
        title="Buy black printer ink",
        status=TaskStatus.COMPLETED,
    )

    with database.transaction() as conn:
        assert repo.update(conn, updated_task) is True

    with database.connect() as conn:
        assert repo.get(conn, saved.id) == updated_task
