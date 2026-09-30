"""Native model tools, explicitly grouped for predictable discovery."""

from .goals import GOAL_TOOLS
from .reminders import REMINDER_TOOLS
from .tasks import TASK_TOOLS
from .time import TIME_TOOLS
from .web import WEB_TOOLS


def get_task_tools() -> list:
    return TASK_TOOLS.copy()


def get_goal_tools() -> list:
    return GOAL_TOOLS.copy()


def get_reminder_tools() -> list:
    return REMINDER_TOOLS.copy()


def get_time_tools() -> list:
    return TIME_TOOLS.copy()


def get_web_tools() -> list:
    return WEB_TOOLS.copy()


def get_all_tools() -> list:
    return [
        *TIME_TOOLS,
        *TASK_TOOLS,
        *GOAL_TOOLS,
        *REMINDER_TOOLS,
        *WEB_TOOLS,
    ]


def tool_result_persists(name: str) -> bool:
    selected = next((tool for tool in ALL_TOOLS if tool.name == name), None)
    if selected is None:
        return True
    return (selected.metadata or {}).get("persist_tool_result", True) is not False


ALL_TOOLS = get_all_tools()


__all__ = [
    "ALL_TOOLS",
    "GOAL_TOOLS",
    "REMINDER_TOOLS",
    "TASK_TOOLS",
    "TIME_TOOLS",
    "WEB_TOOLS",
    "get_all_tools",
    "get_goal_tools",
    "get_reminder_tools",
    "get_task_tools",
    "get_time_tools",
    "get_web_tools",
    "tool_result_persists",
]
