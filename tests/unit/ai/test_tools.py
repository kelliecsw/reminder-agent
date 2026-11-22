from datetime import UTC, date, datetime, time, timedelta

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
from reminder_agent.domain.enums import ReminderPurpose
from reminder_agent.domain.recurrence import ReminderSchedule
from reminder_agent.scheduler.materializer import ReminderMaterializer
from reminder_agent.services.deliveries import DeliveryService
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
        deliveries=DeliveryService(database),
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
        deliveries=DeliveryService(database),
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


def test_list_reminders_includes_snoozed_reminders_with_pending_delivery(
    database: Database,
    user_id: int,
):
    task = TaskService(database).create(user_id, "Drink water")
    ReminderService(database, clock=lambda: NOW.replace(day=23)).create(
        user_id,
        purpose=ReminderPurpose.TASK_REMINDER,
        task_id=task.id,
        schedule=ReminderSchedule(
            start_date=date(2026, 8, 24),
            local_time=time(8),
            timezone="Asia/Singapore",
        ),
    )
    deliveries = DeliveryService(database)
    delivery = ReminderMaterializer(deliveries).run_once(NOW)[0]
    claimed = deliveries.claim_ready(NOW)
    deliveries.mark_sent(claimed.id, claim_token=claimed.claim_token)
    deliveries.snooze(user_id, delivery.id, NOW + timedelta(minutes=10))

    result = _registry(database).execute(
        name="list_reminders",
        arguments={},
        context=ToolContext(
            user_id=user_id,
            timezone="Asia/Singapore",
            now_utc=NOW,
        ),
    )

    assert result["status"] == "success"
    payloads = result["reminders"]
    assert len(payloads) == 1
    assert payloads[0]["status"] == "completed"
    assert payloads[0]["pending_delivery"] == {
        "status": "retry_wait",
        "deliver_at_utc": (NOW + timedelta(minutes=10)).isoformat(),
        "snoozed": True,
    }
