"""Native LangChain tools for goal and progress operations."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from langchain_core.tools import InjectedToolArg, tool
from pydantic import Field

from reminder_agent.domain.models import Goal, GoalProgress

from .registry import ToolRuntime


RuntimeArg = Annotated[ToolRuntime, InjectedToolArg]


@tool
def create_goal(
    title: Annotated[str, Field(min_length=1, max_length=300)],
    runtime: RuntimeArg,
    notes: Annotated[str | None, Field(max_length=2_000)] = None,
) -> dict:
    """Create a longer-running goal."""
    goal = runtime.goals.create(runtime.user_id, title, notes=notes)
    return {"goal": _goal_payload(goal)}


@tool
def list_goals(runtime: RuntimeArg) -> dict:
    """List the user's goals and their current statuses."""
    return {
        "goals": [_goal_payload(goal) for goal in runtime.goals.list(runtime.user_id)]
    }


@tool
def update_goal(
    goal_id: Annotated[int, Field(gt=0)],
    runtime: RuntimeArg,
    title: Annotated[str | None, Field(min_length=1, max_length=300)] = None,
    notes: Annotated[str | None, Field(max_length=2_000)] = None,
    clear_notes: bool = False,
) -> dict:
    """Update a known goal's title or notes.

    Set clear_notes to remove existing notes. Otherwise omitted optional fields
    remain unchanged.
    """
    kwargs = {}
    if title is not None:
        kwargs["title"] = title
    if notes is not None or clear_notes:
        kwargs["notes"] = notes
    goal = runtime.goals.update(runtime.user_id, goal_id, **kwargs)
    return {"goal": _goal_payload(goal)}


@tool
def complete_goal(
    goal_id: Annotated[int, Field(gt=0)],
    runtime: RuntimeArg,
) -> dict:
    """Complete one known goal."""
    return {"goal": _goal_payload(runtime.goals.complete(runtime.user_id, goal_id))}


@tool
def cancel_goal(
    goal_id: Annotated[int, Field(gt=0)],
    runtime: RuntimeArg,
) -> dict:
    """Cancel one known goal."""
    return {"goal": _goal_payload(runtime.goals.cancel(runtime.user_id, goal_id))}


@tool
def pause_goal(
    goal_id: Annotated[int, Field(gt=0)],
    runtime: RuntimeArg,
) -> dict:
    """Pause one known active goal."""
    return {"goal": _goal_payload(runtime.goals.pause(runtime.user_id, goal_id))}


@tool
def resume_goal(
    goal_id: Annotated[int, Field(gt=0)],
    runtime: RuntimeArg,
) -> dict:
    """Resume one known paused goal."""
    return {"goal": _goal_payload(runtime.goals.resume(runtime.user_id, goal_id))}


@tool
def record_goal_progress(
    goal_id: Annotated[int, Field(gt=0)],
    note: Annotated[str, Field(min_length=1, max_length=2_000)],
    runtime: RuntimeArg,
    occurred_on: date | None = None,
) -> dict:
    """Record a dated progress note for one known non-terminal goal."""
    progress = runtime.goals.record_progress(
        runtime.user_id,
        goal_id,
        note,
        occurred_on=occurred_on,
    )
    return {"progress": _progress_payload(progress)}


@tool
def list_goal_progress(
    goal_id: Annotated[int, Field(gt=0)],
    runtime: RuntimeArg,
) -> dict:
    """List recorded progress history for one known goal."""
    return {
        "progress": [
            _progress_payload(progress)
            for progress in runtime.goals.list_progress(runtime.user_id, goal_id)
        ]
    }


GOAL_TOOLS = [
    create_goal,
    list_goals,
    update_goal,
    complete_goal,
    cancel_goal,
    pause_goal,
    resume_goal,
    record_goal_progress,
    list_goal_progress,
]


def _goal_payload(goal: Goal) -> dict:
    return {
        "id": goal.id,
        "title": goal.title,
        "notes": goal.notes,
        "status": goal.status.value,
    }


def _progress_payload(progress: GoalProgress) -> dict:
    return {
        "id": progress.id,
        "goal_id": progress.goal_id,
        "occurred_on": progress.occurred_on.isoformat(),
        "note": progress.note,
    }


__all__ = ["GOAL_TOOLS"]
