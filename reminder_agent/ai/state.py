"""LangGraph state for one in-process chat request."""

from __future__ import annotations

from typing import Any, TypedDict

from langchain_core.messages import BaseMessage


class AgentState(TypedDict, total=False):
    messages: list[BaseMessage]
    user_id: int
    timezone: str
    now_utc: str
    now_local: str

    model_round_count: int
    tool_round_count: int
    tool_round_limit: int

    errors: list[str]
    request_id: str | None
    thread_version: int | None

    context_summary: str
    compression_step_count: int
    compression_prepared_result: dict[str, Any] | None
    memory_rewrite_status: str | None


__all__ = ["AgentState"]
