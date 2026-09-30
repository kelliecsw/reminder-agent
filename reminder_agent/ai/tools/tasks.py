"""Native LangChain tools for task operations."""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import InjectedToolArg, tool
from pydantic import Field

from reminder_agent.domain.models import Task

from .registry import ToolRuntime


RuntimeArg = Annotated[ToolRuntime, InjectedToolArg]


@tool
def create_task(
    title: Annotated[str, Field(min_length=1, max_length=300)],
    runtime: RuntimeArg,
    notes: Annotated[str | None, Field(max_length=2_000)] = None,
    goal_id: Annotated[int | None, Field(gt=0)] = None,
) -> dict:
    """Create a standalone task or a task attached to a known goal."""
    task = runtime.tasks.create(
        runtime.user_id,
        title,
        notes=notes,
        goal_id=goal_id,
    )
    return {"task": _task_payload(task)}


@tool
def list_tasks(
    runtime: RuntimeArg,
    goal_id: Annotated[int | None, Field(gt=0)] = None,
) -> dict:
    """List the user's tasks, optionally restricted to one known goal."""
    return {
        "tasks": [
            _task_payload(task)
            for task in runtime.tasks.list(runtime.user_id, goal_id=goal_id)
        ]
    }


@tool
def update_task(
    task_id: Annotated[int, Field(gt=0)],
    runtime: RuntimeArg,
    title: Annotated[str | None, Field(min_length=1, max_length=300)] = None,
    notes: Annotated[str | None, Field(max_length=2_000)] = None,
    goal_id: Annotated[int | None, Field(gt=0)] = None,
    clear_notes: bool = False,
    detach_from_goal: bool = False,
) -> dict:
    """Update a known task's title, notes, or goal relationship.

    Set clear_notes to remove existing notes. Set detach_from_goal to make the
    task standalone. Otherwise omitted optional fields remain unchanged.
    """
    kwargs = {}
    if title is not None:
        kwargs["title"] = title
    if notes is not None or clear_notes:
        kwargs["notes"] = notes
    if goal_id is not None or detach_from_goal:
        kwargs["goal_id"] = goal_id
    task = runtime.tasks.update(runtime.user_id, task_id, **kwargs)
    return {"task": _task_payload(task)}


@tool
def complete_task(
    task_id: Annotated[int, Field(gt=0)],
    runtime: RuntimeArg,
) -> dict:
    """Complete one known task."""
    return {"task": _task_payload(runtime.tasks.complete(runtime.user_id, task_id))}


@tool
def cancel_task(
    task_id: Annotated[int, Field(gt=0)],
    runtime: RuntimeArg,
) -> dict:
    """Cancel one known task."""
    return {"task": _task_payload(runtime.tasks.cancel(runtime.user_id, task_id))}


@tool
def pause_task(
    task_id: Annotated[int, Field(gt=0)],
    runtime: RuntimeArg,
) -> dict:
    """Pause one known active task."""
    return {"task": _task_payload(runtime.tasks.pause(runtime.user_id, task_id))}


@tool
def resume_task(
    task_id: Annotated[int, Field(gt=0)],
    runtime: RuntimeArg,
) -> dict:
    """Resume one known paused task."""
    return {"task": _task_payload(runtime.tasks.resume(runtime.user_id, task_id))}


TASK_TOOLS = [
    create_task,
    list_tasks,
    update_task,
    complete_task,
    cancel_task,
    pause_task,
    resume_task,
]


def _task_payload(task: Task) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "notes": task.notes,
        "goal_id": task.goal_id,
        "status": task.status.value,
    }


__all__ = ["TASK_TOOLS"]
