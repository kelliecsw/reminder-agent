from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json

from reminder_agent.db.database import Database
from reminder_agent.domain.enums import ToolExecutionStatus
from reminder_agent.domain.models import ToolExecution
from reminder_agent.repositories.tool_executions import ToolExecutionRepository


class ToolExecutionService:
    def __init__(
        self,
        database: Database,
        *,
        repository: ToolExecutionRepository | None = None,
    ) -> None:
        self.database = database
        self.repository = repository or ToolExecutionRepository()

    def execute(
        self,
        *,
        request_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict,
        now_utc: datetime,
        operation: Callable[[], dict],
    ) -> dict:
        key = f"{request_id}:{tool_call_id}"
        with self.database.transaction(immediate=True) as conn:
            existing = self.repository.get(conn, key)
            if existing is not None:
                if existing.tool_name != tool_name or existing.arguments != arguments:
                    raise RuntimeError(
                        "Idempotency key was reused with a different tool call."
                    )
                if (
                    existing.status == ToolExecutionStatus.COMPLETED
                    and existing.result is not None
                ):
                    return existing.result
                raise RuntimeError("An incomplete tool execution already exists.")

            self.repository.create(
                conn,
                ToolExecution(
                    idempotency_key=key,
                    request_id=request_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments=json.loads(json.dumps(arguments, default=str)),
                    status=ToolExecutionStatus.RUNNING,
                ),
                now_utc,
            )
            result = operation()
            if not self.repository.complete(
                conn,
                key=key,
                result=result,
                now_utc=now_utc,
            ):
                raise RuntimeError("Tool execution result could not be persisted.")
            return result


__all__ = ["ToolExecutionService"]
