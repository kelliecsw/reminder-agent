"""Trusted clock tool for time-sensitive model decisions."""

from __future__ import annotations

from datetime import UTC
from typing import Annotated
from zoneinfo import ZoneInfo

from langchain_core.tools import InjectedToolArg, tool

from .registry import ToolRuntime


RuntimeArg = Annotated[ToolRuntime, InjectedToolArg]


@tool
def get_current_datetime(runtime: RuntimeArg) -> dict[str, str]:
    """Get the current UTC and user-local date and time from the trusted clock."""

    now_utc = runtime.now_utc.astimezone(UTC)
    now_local = now_utc.astimezone(ZoneInfo(runtime.timezone))
    return {
        "utc_datetime": now_utc.isoformat(),
        "local_datetime": now_local.isoformat(),
        "local_date": now_local.date().isoformat(),
        "local_time": now_local.timetz().isoformat(),
        "weekday": now_local.strftime("%A"),
        "timezone": runtime.timezone,
    }


get_current_datetime.metadata = {
    "execution_kind": "ephemeral_read",
    "persist_tool_result": False,
}
TIME_TOOLS = [get_current_datetime]


__all__ = ["TIME_TOOLS", "get_current_datetime"]
