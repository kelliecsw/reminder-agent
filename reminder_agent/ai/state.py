"""LangGraph state for one in-process chat request."""

from __future__ import annotations

from typing import Annotated, Sequence, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[Sequence[BaseMessage], add_messages]
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


__all__ = ["AgentState"]
