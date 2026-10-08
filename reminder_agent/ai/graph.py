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

from reminder_agent.ai.compression import (
    CompressionConfig,
    compression_config_from_settings,
    count_prompt_tokens,
    count_text_tokens,
    count_tool_schema_tokens,
    ensure_tool_call_integrity,
    historical_summary_message,
    prepare_compression,
    update_context_summary,
)
from reminder_agent.ai.prompt import build_system_prompt
from reminder_agent.ai.safety import sanitize_untrusted_content
from reminder_agent.ai.state import AgentState
from reminder_agent.ai.tools import tool_result_persists
from reminder_agent.ai.tools.registry import ToolContext, ToolRegistry
from reminder_agent.clock import utc_now
from reminder_agent.config import Settings
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

    invocation = [SystemMessage(content=build_system_prompt(dict(state)))]
    if state.get("context_summary", "").strip():
        invocation.append(historical_summary_message(state["context_summary"]))
    invocation.extend(state["messages"])
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

    next_messages = [*state["messages"], response]
    compression_step_count = state.get("compression_step_count", 0) + 1
    update = {
        "messages": next_messages,
        "model_round_count": model_round + 1,
        "compression_step_count": compression_step_count,
    }
    if thread_service is not None:
        saved = thread_service.save(
            state["user_id"],
            _durable_messages(
                next_messages,
                registry=registry,
            ),
            expected_version=state.get("thread_version"),
            now_utc=clock(),
            context_summary=state.get("context_summary", ""),
            compression_step_count=compression_step_count,
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
    next_messages = [*state["messages"], *tool_messages]
    update = {
        "messages": next_messages,
        "tool_round_count": state.get("tool_round_count", 0) + 1,
        "now_utc": now.isoformat(),
        "now_local": now.astimezone(ZoneInfo(state["timezone"])).isoformat(),
        **accumulated_effects,
    }
    if thread_service is not None:
        saved = thread_service.save(
            state["user_id"],
            _durable_messages(
                next_messages,
                registry=registry,
            ),
            expected_version=state.get("thread_version"),
            now_utc=now,
            context_summary=state.get("context_summary", ""),
            compression_step_count=state.get("compression_step_count", 0),
        )
        update["thread_version"] = saved.version
    return update


async def _route_after_agent(state: AgentState) -> Literal["tools", "end"]:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "end"


async def _route_from_start(
    state: AgentState,
) -> Literal["compress_prepare", "tools"]:
    if state.get("messages"):
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
    return "compress_prepare"


async def _compress_prepare_node(
    state: AgentState,
    *,
    tool_schema_tokens: int,
    compression_config: CompressionConfig,
) -> dict[str, Any]:
    prepared = prepare_compression(
        messages=state["messages"],
        context_summary=state.get("context_summary", ""),
        compression_step_count=state.get("compression_step_count", 0),
        system_prompt=build_system_prompt(dict(state)),
        tool_schema_tokens=tool_schema_tokens,
        config=compression_config,
    )
    if prepared is None:
        return {
            "compression_prepared_result": None,
            "memory_rewrite_status": None,
        }
    logger.info(
        "[COMPRESSION] Prepared reasons=%s tokens=%s->%s turns=%s removed=%s",
        ",".join(prepared["trigger_reasons"]),
        prepared["tokens_before"],
        prepared["projected_tokens"],
        prepared["turns_before"],
        prepared["turns_removed"],
    )
    return {
        "compression_prepared_result": prepared,
        "memory_rewrite_status": None,
    }


async def _route_after_compress_prepare(
    state: AgentState,
) -> Literal["agent", "memory_chat"]:
    if state.get("compression_prepared_result") is None:
        return "agent"
    return "memory_chat"


async def _memory_chat_node(
    state: AgentState,
    *,
    model_without_tools: Any,
    compression_config: CompressionConfig,
) -> dict[str, Any]:
    prepared = state.get("compression_prepared_result")
    if prepared is None:
        raise RuntimeError("memory_chat called without a prepared compression result.")
    summary_exchanges = prepared.get("summary_exchanges", [])
    summary = await update_context_summary(
        model=model_without_tools,
        existing_summary=str(prepared.get("context_summary", "")),
        exchanges=summary_exchanges,
        config=compression_config,
    )
    return {
        "compression_prepared_result": {
            **prepared,
            "context_summary": summary,
        },
        "memory_rewrite_status": (
            "updated" if summary_exchanges else "unchanged"
        ),
    }


async def _compress_commit_node(
    state: AgentState,
    *,
    registry: ToolRegistry,
    tool_schema_tokens: int,
    compression_config: CompressionConfig,
    thread_service: AgentThreadService | None,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    prepared = state.get("compression_prepared_result")
    if prepared is None:
        raise RuntimeError("compress_commit called without a prepared result.")
    messages = list(prepared["messages"])
    context_summary = str(prepared.get("context_summary", ""))
    ensure_tool_call_integrity(messages)
    final_tokens = count_prompt_tokens(
        system_prompt=build_system_prompt(dict(state)),
        context_summary=context_summary,
        messages=messages,
        tool_schema_tokens=tool_schema_tokens,
    )
    if final_tokens > compression_config.token_threshold:
        raise RuntimeError(
            "Compressed conversation still exceeds the token threshold: "
            f"{final_tokens} > {compression_config.token_threshold}."
        )

    update: dict[str, Any] = {
        "messages": messages,
        "context_summary": context_summary,
        "compression_step_count": 0,
        "compression_prepared_result": None,
    }
    if thread_service is not None:
        saved = thread_service.save(
            state["user_id"],
            _durable_messages(messages, registry=registry),
            expected_version=state.get("thread_version"),
            now_utc=clock(),
            context_summary=context_summary,
            compression_step_count=0,
        )
        update["thread_version"] = saved.version
    logger.info(
        "[COMPRESSION] Committed tokens=%s summary_tokens=%s",
        final_tokens,
        count_text_tokens(context_summary),
    )
    return update


def create_reminder_agent(
    *,
    settings: Settings,
    model: Any,
    registry: ToolRegistry,
    clock: Callable[[], datetime] = utc_now,
    thread_service: AgentThreadService | None = None,
):
    retry_attempts = settings.llm_retry_attempts
    log_private_content = settings.log_private_content
    compression_config = compression_config_from_settings(settings)
    registry_schemas = registry.schemas()
    tool_schema_tokens = count_tool_schema_tokens(registry_schemas)
    model_with_tools = model.bind_tools(
        registry_schemas,
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
        "compress_prepare",
        partial(
            _compress_prepare_node,
            tool_schema_tokens=tool_schema_tokens,
            compression_config=compression_config,
        ),
    )
    graph.add_node(
        "memory_chat",
        partial(
            _memory_chat_node,
            model_without_tools=model_without_tools,
            compression_config=compression_config,
        ),
    )
    graph.add_node(
        "compress_commit",
        partial(
            _compress_commit_node,
            registry=registry,
            tool_schema_tokens=tool_schema_tokens,
            compression_config=compression_config,
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
        {"compress_prepare": "compress_prepare", "tools": "tools"},
    )
    graph.add_conditional_edges(
        "compress_prepare",
        _route_after_compress_prepare,
        {"agent": "agent", "memory_chat": "memory_chat"},
    )
    graph.add_edge("memory_chat", "compress_commit")
    graph.add_edge("compress_commit", "agent")
    graph.add_conditional_edges(
        "agent",
        _route_after_agent,
        {"tools": "tools", "end": END},
    )
    graph.add_edge("tools", "compress_prepare")
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
    context_summary: str = "",
    compression_step_count: int = 0,
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
        "context_summary": context_summary,
        "compression_step_count": compression_step_count,
        "compression_prepared_result": None,
        "memory_rewrite_status": None,
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
