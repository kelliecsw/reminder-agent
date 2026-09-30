"""Trusted runtime injection and execution for native LangChain tools."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from reminder_agent.domain.errors import ApplicationError, DomainError
from reminder_agent.services.goals import GoalService
from reminder_agent.services.reminders import ReminderService
from reminder_agent.services.tasks import TaskService
from reminder_agent.services.tool_executions import ToolExecutionService


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ToolContext:
    user_id: int
    timezone: str
    now_utc: datetime


@dataclass(frozen=True, slots=True)
class ToolRuntime:
    user_id: int
    timezone: str
    now_utc: datetime
    tasks: TaskService
    goals: GoalService
    reminders: ReminderService


class ToolRegistry:
    def __init__(
        self,
        *,
        tasks: TaskService,
        goals: GoalService,
        reminders: ReminderService,
        tools: list[Any] | None = None,
        execution_service: ToolExecutionService | None = None,
    ) -> None:
        if tools is None:
            from . import get_all_tools

            tools = get_all_tools()
        self._tools = {tool.name: tool for tool in tools}
        if len(self._tools) != len(tools):
            raise ValueError("Tool names must be unique.")
        self.tasks = tasks
        self.goals = goals
        self.reminders = reminders
        self.execution_service = execution_service

    def schemas(self) -> list[Any]:
        return list(self._tools.values())

    def is_external_read(self, name: str) -> bool:
        return self.execution_kind(name) == "external_read"

    def execution_kind(self, name: str) -> str:
        selected = self._tools.get(name)
        if selected is None:
            return "application"
        return str((selected.metadata or {}).get("execution_kind", "application"))

    async def aexecute(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
        request_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute async external reads without a database idempotency lock."""

        if not self.is_external_read(name):
            return self.execute(
                name=name,
                arguments=arguments,
                context=context,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )

        selected, error = self._validated_tool(name, arguments)
        if error is not None:
            return error
        assert selected is not None
        runtime = self._runtime(context)
        try:
            payload_args = dict(arguments)
            if "runtime" in selected.get_input_schema().model_fields:
                payload_args["runtime"] = runtime
            payload = await selected.ainvoke(payload_args)
            return {
                "status": "success",
                "action": name,
                **_json_safe(payload),
            }
        except (ValidationError, ValueError) as exc:
            return _error_result(name, _error_code(exc), str(exc))
        except Exception:
            logger.exception("[TOOLS] Unexpected failure tool=%s", name)
            return _error_result(
                name,
                "internal_error",
                "The external operation failed safely. Please try again.",
            )

    def execute(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
        request_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> dict[str, Any]:
        selected, error = self._validated_tool(name, arguments)
        if error is not None:
            return error
        assert selected is not None
        runtime = self._runtime(context)

        def invoke() -> dict[str, Any]:
            try:
                payload_args = dict(arguments)
                if "runtime" in selected.get_input_schema().model_fields:
                    payload_args["runtime"] = runtime
                payload = selected.invoke(payload_args)
                return {
                    "status": "success",
                    "action": name,
                    **_json_safe(payload),
                }
            except (ApplicationError, DomainError, ValidationError, ValueError) as exc:
                return _error_result(name, _error_code(exc), str(exc))
            except Exception:
                logger.exception("[TOOLS] Unexpected failure tool=%s", name)
                return _error_result(
                    name,
                    "internal_error",
                    "The operation failed safely. Please try again.",
                )

        if (
            self.execution_service is not None
            and request_id is not None
            and tool_call_id is not None
            and self.persists_result(name)
        ):
            return self.execution_service.execute(
                request_id=request_id,
                tool_call_id=tool_call_id,
                tool_name=name,
                arguments=arguments,
                now_utc=context.now_utc,
                operation=invoke,
            )
        return invoke()

    def persists_result(self, name: str) -> bool:
        selected = self._tools.get(name)
        if selected is None:
            return True
        return (selected.metadata or {}).get("persist_tool_result", True) is not False

    def _runtime(self, context: ToolContext) -> ToolRuntime:
        return ToolRuntime(
            user_id=context.user_id,
            timezone=context.timezone,
            now_utc=context.now_utc,
            tasks=self.tasks,
            goals=self.goals,
            reminders=self.reminders,
        )

    def _validated_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> tuple[Any | None, dict[str, Any] | None]:
        selected = self._tools.get(name)
        if selected is None:
            return None, _error_result(
                name,
                "unknown_tool",
                f"Unknown tool: {name}",
            )
        allowed_arguments = set(selected.tool_call_schema.model_fields)
        unexpected = sorted(set(arguments) - allowed_arguments)
        if unexpected:
            return None, _error_result(
                name,
                "invalid_arguments",
                f"Unexpected tool arguments: {', '.join(unexpected)}",
            )
        return selected, None


def create_tool_registry(
    *,
    tasks: TaskService,
    goals: GoalService,
    reminders: ReminderService,
    execution_service: ToolExecutionService | None = None,
    tools: list[Any] | None = None,
) -> ToolRegistry:
    return ToolRegistry(
        tasks=tasks,
        goals=goals,
        reminders=reminders,
        execution_service=execution_service,
        tools=tools,
    )


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _error_code(exc: Exception) -> str:
    name = type(exc).__name__
    if name.endswith("Error"):
        name = name[:-5]
    chars = []
    for index, char in enumerate(name):
        if char.isupper() and index:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars) or "invalid_input"


def _error_result(action: str, code: str, message: str) -> dict[str, Any]:
    return {
        "status": "error",
        "action": action,
        "error": code,
        "message": message,
    }


__all__ = [
    "ToolContext",
    "ToolRegistry",
    "ToolRuntime",
    "create_tool_registry",
]
