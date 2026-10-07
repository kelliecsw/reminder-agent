"""Native LangChain tools and structured inputs for reminder operations."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated, Literal

from langchain_core.tools import InjectedToolArg, tool
from pydantic import BaseModel, ConfigDict, Field

from reminder_agent.domain.enums import Frequency, ReminderPurpose, Weekday
from reminder_agent.domain.models import Reminder
from reminder_agent.domain.recurrence import (
    RecurrenceRule,
    ReminderSchedule,
    WeekdayRule,
)

from .registry import ToolRuntime


RuntimeArg = Annotated[ToolRuntime, InjectedToolArg]


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WeekdayRuleArgs(ToolArgs):
    day: Literal[
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
    ordinal: int | None = Field(default=None, ge=-53, le=53)


class RecurrenceArgs(ToolArgs):
    frequency: Frequency
    interval: int = Field(default=1, ge=1)
    by_day: list[WeekdayRuleArgs] = Field(default_factory=list)
    by_month_day: list[int] = Field(default_factory=list)
    by_month: list[int] = Field(default_factory=list)
    by_set_position: list[int] = Field(default_factory=list)
    count: int | None = Field(default=None, ge=1)
    until: datetime | None = None


class ScheduleArgs(ToolArgs):
    start_date: date
    local_time: time
    timezone: str | None = Field(
        default=None,
        description="IANA timezone; omit to use the user's current timezone.",
    )
    recurrence: RecurrenceArgs | None = None


@tool
def create_task_reminder(
    task_id: Annotated[int, Field(gt=0)],
    schedule: ScheduleArgs,
    runtime: RuntimeArg,
) -> dict:
    """Create a reminder for one known task using a structured local schedule."""
    reminder = runtime.reminders.create(
        runtime.user_id,
        purpose=ReminderPurpose.TASK_REMINDER,
        task_id=task_id,
        schedule=_schedule(schedule, runtime.timezone),
    )
    return {"reminder": _reminder_payload(reminder)}


@tool
def create_goal_reminder(
    goal_id: Annotated[int, Field(gt=0)],
    schedule: ScheduleArgs,
    runtime: RuntimeArg,
) -> dict:
    """Create a reminder for one known goal using a structured local schedule."""
    reminder = runtime.reminders.create(
        runtime.user_id,
        purpose=ReminderPurpose.GOAL_REMINDER,
        goal_id=goal_id,
        schedule=_schedule(schedule, runtime.timezone),
    )
    return {"reminder": _reminder_payload(reminder)}


@tool
def get_reminder(
    reminder_id: Annotated[int, Field(gt=0)],
    runtime: RuntimeArg,
) -> dict:
    """Get one known reminder and its current schedule."""
    reminder = runtime.reminders.get(runtime.user_id, reminder_id)
    return {"reminder": _reminder_payload(reminder)}


@tool
def list_reminders(runtime: RuntimeArg) -> dict:
    """List the user's reminders, targets, schedules, and statuses."""
    return {
        "reminders": [
            _reminder_payload(reminder)
            for reminder in runtime.reminders.list(runtime.user_id)
        ]
    }


@tool
def reschedule_reminder(
    reminder_id: Annotated[int, Field(gt=0)],
    schedule: ScheduleArgs,
    runtime: RuntimeArg,
) -> dict:
    """Replace the complete schedule of one known live reminder."""
    reminder = runtime.reminders.reschedule(
        runtime.user_id,
        reminder_id,
        _schedule(schedule, runtime.timezone),
    )
    return {"reminder": _reminder_payload(reminder)}


@tool
def pause_reminder(
    reminder_id: Annotated[int, Field(gt=0)],
    runtime: RuntimeArg,
) -> dict:
    """Pause a known reminder; use for cancel, stop, or turn off requests."""
    reminder = runtime.reminders.pause(runtime.user_id, reminder_id)
    return {"reminder": _reminder_payload(reminder)}


@tool
def resume_reminder(
    reminder_id: Annotated[int, Field(gt=0)],
    runtime: RuntimeArg,
) -> dict:
    """Resume a known paused reminder from its next future occurrence."""
    reminder = runtime.reminders.resume(runtime.user_id, reminder_id)
    return {"reminder": _reminder_payload(reminder)}


REMINDER_TOOLS = [
    create_task_reminder,
    create_goal_reminder,
    get_reminder,
    list_reminders,
    reschedule_reminder,
    pause_reminder,
    resume_reminder,
]


def _schedule(args: ScheduleArgs, default_timezone: str) -> ReminderSchedule:
    recurrence = args.recurrence
    return ReminderSchedule(
        start_date=args.start_date,
        local_time=args.local_time,
        timezone=args.timezone or default_timezone,
        recurrence=(
            RecurrenceRule(
                frequency=recurrence.frequency,
                interval=recurrence.interval,
                by_day=tuple(
                    WeekdayRule(day=_WEEKDAYS[rule.day], ordinal=rule.ordinal)
                    for rule in recurrence.by_day
                ),
                by_month_day=tuple(recurrence.by_month_day),
                by_month=tuple(recurrence.by_month),
                by_set_position=tuple(recurrence.by_set_position),
                count=recurrence.count,
                until=recurrence.until,
            )
            if recurrence is not None
            else None
        ),
    )


def _reminder_payload(reminder: Reminder) -> dict:
    recurrence = reminder.schedule.recurrence
    return {
        "id": reminder.id,
        "purpose": reminder.purpose.value,
        "task_id": reminder.task_id,
        "goal_id": reminder.goal_id,
        "status": reminder.status.value,
        "version": reminder.version,
        "next_fire_at_utc": (
            reminder.next_fire_at_utc.isoformat()
            if reminder.next_fire_at_utc is not None
            else None
        ),
        "schedule": {
            "start_date": reminder.schedule.start_date.isoformat(),
            "local_time": reminder.schedule.local_time.isoformat(),
            "timezone": reminder.schedule.timezone,
            "recurrence": (
                {
                    "frequency": recurrence.frequency.value,
                    "interval": recurrence.interval,
                    "by_day": [
                        {"day": rule.day.name.lower(), "ordinal": rule.ordinal}
                        for rule in recurrence.by_day
                    ],
                    "by_month_day": list(recurrence.by_month_day),
                    "by_month": list(recurrence.by_month),
                    "by_set_position": list(recurrence.by_set_position),
                    "count": recurrence.count,
                    "until": (
                        recurrence.until.isoformat()
                        if recurrence.until is not None
                        else None
                    ),
                }
                if recurrence is not None
                else None
            ),
        },
    }


_WEEKDAYS = {
    "monday": Weekday.MONDAY,
    "tuesday": Weekday.TUESDAY,
    "wednesday": Weekday.WEDNESDAY,
    "thursday": Weekday.THURSDAY,
    "friday": Weekday.FRIDAY,
    "saturday": Weekday.SATURDAY,
    "sunday": Weekday.SUNDAY,
}


__all__ = ["REMINDER_TOOLS", "RecurrenceArgs", "ScheduleArgs", "WeekdayRuleArgs"]
