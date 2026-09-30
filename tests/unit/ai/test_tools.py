from datetime import UTC, datetime

from reminder_agent.ai.tools import (
    GOAL_TOOLS,
    REMINDER_TOOLS,
    TASK_TOOLS,
    TIME_TOOLS,
    WEB_TOOLS,
    get_all_tools,
)
from reminder_agent.ai.tools.registry import ToolContext, create_tool_registry
from reminder_agent.db.database import Database
from reminder_agent.services.goals import GoalService
from reminder_agent.services.reminders import ReminderService
from reminder_agent.services.tasks import TaskService
from reminder_agent.services.tool_executions import ToolExecutionService


NOW = datetime(2026, 8, 24, 0, 0, tzinfo=UTC)


def _registry(database: Database):
    return create_tool_registry(
        tasks=TaskService(database),
        goals=GoalService(database, clock=lambda: NOW),
        reminders=ReminderService(database, clock=lambda: NOW),
    )


def test_tools_are_explicitly_discovered_by_category():
    assert get_all_tools() == [
        *TIME_TOOLS,
        *TASK_TOOLS,
        *GOAL_TOOLS,
        *REMINDER_TOOLS,
        *WEB_TOOLS,
    ]
    assert len({tool.name for tool in get_all_tools()}) == len(get_all_tools())


def test_model_facing_tool_schemas_never_include_user_id(database: Database):
    schemas = _registry(database).schemas()
    assert schemas
    assert all(
        "user_id"
        not in schema.tool_call_schema.model_json_schema().get("properties", {})
        for schema in schemas
    )
    assert all(
        "runtime"
        not in schema.tool_call_schema.model_json_schema().get("properties", {})
        for schema in schemas
    )


def test_registry_injects_trusted_user_id(database: Database, user_id: int):
    registry = _registry(database)
    result = registry.execute(
        name="create_task",
        arguments={"title": "Buy printer ink"},
        context=ToolContext(
            user_id=user_id,
            timezone="Asia/Singapore",
            now_utc=NOW,
        ),
    )

    assert result["status"] == "success"
    with database.connect() as conn:
        task = TaskService(database).task_repo.get(conn, result["task"]["id"])
    assert task.user_id == user_id


def test_model_cannot_override_trusted_user_id(database: Database, user_id: int):
    result = _registry(database).execute(
        name="create_task",
        arguments={"title": "Wrong owner", "user_id": user_id + 1},
        context=ToolContext(
            user_id=user_id,
            timezone="Asia/Singapore",
            now_utc=NOW,
        ),
    )
    assert result["status"] == "error"


def test_current_datetime_uses_trusted_clock_and_is_not_persisted(
    database: Database,
    user_id: int,
):
    registry = create_tool_registry(
        tasks=TaskService(database),
        goals=GoalService(database, clock=lambda: NOW),
        reminders=ReminderService(database, clock=lambda: NOW),
        execution_service=ToolExecutionService(database),
    )

    result = registry.execute(
        name="get_current_datetime",
        arguments={},
        context=ToolContext(
            user_id=user_id,
            timezone="Asia/Singapore",
            now_utc=NOW,
        ),
        request_id="telegram:clock-test",
        tool_call_id="clock-1",
    )

    assert result == {
        "status": "success",
        "action": "get_current_datetime",
        "utc_datetime": "2026-08-24T00:00:00+00:00",
        "local_datetime": "2026-08-24T08:00:00+08:00",
        "local_date": "2026-08-24",
        "local_time": "08:00:00",
        "weekday": "Monday",
        "timezone": "Asia/Singapore",
    }
    with database.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM tool_executions").fetchone()[0]
    assert count == 0
