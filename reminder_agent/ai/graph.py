"""Small auditable LangGraph goal, task, and reminder loop."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from functools import partial
from typing import Any, Literal
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from reminder_agent.ai.prompt import build_system_prompt
from reminder_agent.ai.safety import sanitize_untrusted_content
from reminder_agent.ai.state import AgentState
from reminder_agent.ai.tools import tool_result_persists
from reminder_agent.ai.tools.registry import ToolContext, ToolRegistry
from reminder_agent.clock import utc_now
from reminder_agent.services.agent_threads import AgentThreadService


logger = logging.getLogger(__name__)
LOG_SEPARATOR = "=" * 60


async def _agent_node(
    state: AgentState,
    *,
    model_with_tools: Any,
    model_without_tools: Any,
    registry: ToolRegistry,
    log_private_content: bool,
    thread_service: AgentThreadService | None,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    model_round = state.get("model_round_count", 0)
    tool_round = state.get("tool_round_count", 0)
    tool_limit = state.get("tool_round_limit", 6)

    logger.info("")
    logger.info(LOG_SEPARATOR)
    logger.info(
        "[AGENT] Step %s · request_id=%s:",
        model_round,
        state.get("request_id") or "-",
    )

    invocation = [
        SystemMessage(content=build_system_prompt(dict(state))),
        *state["messages"],
    ]
    model = model_with_tools if tool_round < tool_limit else model_without_tools
    if tool_round >= tool_limit:
        invocation.append(
            SystemMessage(
                content=(
                    "Tool round limit reached. Give a safe concise final response "
                    "without requesting more tools."
                )
            )
        )

    response: AIMessage = await model.ainvoke(invocation)

    if response.content:
        logger.info("[AGENT] Message:")
        logger.info(
            "%s",
            _private_value(
                _message_content_text(response.content),
                enabled=log_private_content,
            ),
        )
    if response.tool_calls:
        logger.info("[AGENT] Tool calls: %s", len(response.tool_calls))
        for call in response.tool_calls:
            logger.info(
                "  - %s: %s",
                call.get("name"),
                _private_value(
                    call.get("args", {}),
                    enabled=log_private_content,
                ),
            )
    logger.info(LOG_SEPARATOR)

    update = {
        "messages": [response],
        "model_round_count": model_round + 1,
    }
    if thread_service is not None:
        saved = thread_service.save(
            state["user_id"],
            _durable_messages(
                [*state["messages"], response],
                registry=registry,
            ),
            expected_version=state.get("thread_version"),
            now_utc=clock(),
        )
        update["thread_version"] = saved.version
    return update


async def _tool_node(
    state: AgentState,
    *,
    registry: ToolRegistry,
    clock: Callable[[], datetime],
    log_private_content: bool,
    thread_service: AgentThreadService | None,
) -> dict[str, Any]:
    last = state["messages"][-1]
    if not isinstance(last, AIMessage) or not last.tool_calls:
        return {}

    calls = list(last.tool_calls)
    validation_errors = _validate_tool_batch(
        calls,
        registry=registry,
    )
    tool_messages = []
    accumulated_effects: dict[str, Any] = {}
    context = ToolContext(
        user_id=state["user_id"],
        timezone=state["timezone"],
        now_utc=clock(),
    )
    for call in calls:
        logger.info("")
        logger.info(LOG_SEPARATOR)
        logger.info(
            "[TOOLS] Executing: %s · request_id=%s",
            call["name"],
            state.get("request_id") or "-",
        )

        if call["id"] in validation_errors:
            result = validation_errors[call["id"]]
        else:
            result = await _execute_tool(
                call["name"],
                dict(call.get("args", {})),
                call["id"],
                state=state,
                registry=registry,
                context=context,
            )

        result_str = json.dumps(result, ensure_ascii=False, default=str)
        final_result_str = _finalize_tool_result(call["name"], result_str)

        effects = _process_tool_side_effects(
            call["name"],
            dict(call.get("args", {})),
            result,
            {**state, **accumulated_effects},
        )
        accumulated_effects.update(effects)

        tool_messages.append(
            ToolMessage(
                content=final_result_str,
                name=call["name"],
                tool_call_id=call["id"],
            )
        )

        logger.info("[TOOLS] Result:")
        logger.info(
            "%s",
            _private_value(
                final_result_str,
                enabled=log_private_content,
            ),
        )
        logger.info(LOG_SEPARATOR)

    now = clock()
    update = {
        "messages": tool_messages,
        "tool_round_count": state.get("tool_round_count", 0) + 1,
        "now_utc": now.isoformat(),
        "now_local": now.astimezone(ZoneInfo(state["timezone"])).isoformat(),
        **accumulated_effects,
    }
    if thread_service is not None:
        saved = thread_service.save(
            state["user_id"],
            _durable_messages(
                [*state["messages"], *tool_messages],
                registry=registry,
            ),
            expected_version=state.get("thread_version"),
            now_utc=now,
        )
        update["thread_version"] = saved.version
    return update


def _route_after_agent(state: AgentState) -> Literal["tools", "end"]:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "end"


def _route_from_start(state: AgentState) -> Literal["agent", "tools"]:
    if state.get("messages"):
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
    return "agent"


def create_reminder_agent(
    *,
    model: Any,
    registry: ToolRegistry,
    clock: Callable[[], datetime] = utc_now,
    retry_attempts: int = 1,
    log_private_content: bool = False,
    thread_service: AgentThreadService | None = None,
):
    model_with_tools = model.bind_tools(
        registry.schemas(),
        parallel_tool_calls=False,
    ).with_retry(stop_after_attempt=max(1, retry_attempts + 1))
    model_without_tools = model.with_retry(
        stop_after_attempt=max(1, retry_attempts + 1)
    )

    graph = StateGraph(AgentState)
    graph.add_node(
        "agent",
        partial(
            _agent_node,
            model_with_tools=model_with_tools,
            model_without_tools=model_without_tools,
            registry=registry,
            log_private_content=log_private_content,
            thread_service=thread_service,
            clock=clock,
        ),
    )
    graph.add_node(
        "tools",
        partial(
            _tool_node,
            registry=registry,
            clock=clock,
            log_private_content=log_private_content,
            thread_service=thread_service,
        ),
    )
    graph.add_conditional_edges(
        START,
        _route_from_start,
        {"agent": "agent", "tools": "tools"},
    )
    graph.add_conditional_edges(
        "agent",
        _route_after_agent,
        {"tools": "tools", "end": END},
    )
    graph.add_edge("tools", "agent")
    return graph.compile()


def initial_state(
    *,
    messages: list[Any],
    user_id: int,
    timezone: str,
    now_utc: datetime,
    tool_round_limit: int,
    request_id: str | None = None,
    thread_version: int | None = None,
) -> AgentState:
    return {
        "messages": _durable_messages(messages),
        "user_id": user_id,
        "timezone": timezone,
        "now_utc": now_utc.isoformat(),
        "now_local": now_utc.astimezone(ZoneInfo(timezone)).isoformat(),
        "model_round_count": 0,
        "tool_round_count": 0,
        "tool_round_limit": tool_round_limit,
        "errors": [],
        "request_id": request_id,
        "thread_version": thread_version,
    }


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def _private_value(value: Any, *, enabled: bool, limit: int = 500) -> str:
    if not enabled:
        return "<redacted>"
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _tool_error(action: str, code: str, message: str) -> dict[str, Any]:
    return {
        "status": "error",
        "action": action,
        "error": code,
        "message": message,
    }


async def _execute_tool(
    tool_name: str,
    tool_args: dict[str, Any],
    tool_call_id: str,
    *,
    state: AgentState,
    registry: ToolRegistry,
    context: ToolContext,
) -> dict[str, Any]:
    """Execute one validated tool call through the trusted registry."""

    return await registry.aexecute(
        name=tool_name,
        arguments=tool_args,
        context=context,
        request_id=state.get("request_id"),
        tool_call_id=tool_call_id,
    )


def _finalize_tool_result(_tool_name: str, result_str: str) -> str:
    """Apply graph-level policy before a tool result reaches the model."""

    return str(sanitize_untrusted_content(result_str))


def _process_tool_side_effects(
    _tool_name: str,
    _tool_args: dict[str, Any],
    _result: dict[str, Any],
    _state: AgentState | dict[str, Any],
) -> dict[str, Any]:
    """Return graph-state changes caused by one completed tool call."""

    return {}


def _validate_tool_batch(
    calls: list[dict[str, Any]],
    *,
    registry: ToolRegistry,
) -> dict[str, dict[str, Any]]:
    """Reject batches that cross execution trust boundaries."""

    execution_kinds = {
        registry.execution_kind(str(call.get("name", "")))
        for call in calls
    }
    if len(execution_kinds) > 1:
        return {
            call["id"]: _tool_error(
                call["name"],
                "mixed_tool_execution_kinds",
                (
                    "Tools from different execution kinds must be called in "
                    "separate steps."
                ),
            )
            for call in calls
        }
    return {}


def _durable_messages(
    messages: list[Any],
    *,
    registry: ToolRegistry | None = None,
) -> list[Any]:
    """Remove nonpersistent tool calls/results from durable history."""

    ephemeral_call_ids = {
        str(call.get("id"))
        for message in messages
        if isinstance(message, AIMessage)
        and any(
            not _persists_tool_result(call.get("name"), registry)
            for call in message.tool_calls
        )
        for call in message.tool_calls
    }
    durable = []
    for message in messages:
        if (
            isinstance(message, AIMessage)
            and any(
                not _persists_tool_result(call.get("name"), registry)
                for call in message.tool_calls
            )
        ):
            continue
        if (
            isinstance(message, ToolMessage)
            and str(message.tool_call_id) in ephemeral_call_ids
        ):
            continue
        durable.append(message)
    return durable


def _persists_tool_result(
    tool_name: str | None,
    registry: ToolRegistry | None,
) -> bool:
    if tool_name is None:
        return True
    if registry is not None:
        return registry.persists_result(tool_name)
    return tool_result_persists(tool_name)


__all__ = ["create_reminder_agent", "initial_state"]
