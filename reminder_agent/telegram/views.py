from __future__ import annotations

import calendar
from datetime import date, time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from reminder_agent.domain.enums import Frequency, ReminderStatus, Weekday
from reminder_agent.domain.models import Goal, Reminder, Task
from reminder_agent.domain.recurrence import (
    RecurrenceRule,
    ReminderSchedule,
    WeekdayRule,
)


_WEEKDAY_NAMES = {
    Weekday.MONDAY: "Monday",
    Weekday.TUESDAY: "Tuesday",
    Weekday.WEDNESDAY: "Wednesday",
    Weekday.THURSDAY: "Thursday",
    Weekday.FRIDAY: "Friday",
    Weekday.SATURDAY: "Saturday",
    Weekday.SUNDAY: "Sunday",
}


def reminder_actions(delivery_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Done", callback_data=f"d:{delivery_id}:done"),
                InlineKeyboardButton("Snooze", callback_data=f"d:{delivery_id}:s"),
                InlineKeyboardButton("Skip", callback_data=f"d:{delivery_id}:skip"),
            ],
            [
                InlineKeyboardButton("Pause", callback_data=f"d:{delivery_id}:pause"),
            ],
        ]
    )


def snooze_actions(delivery_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("10 minutes", callback_data=f"d:{delivery_id}:s10"),
                InlineKeyboardButton("1 hour", callback_data=f"d:{delivery_id}:s60"),
            ],
            [
                InlineKeyboardButton("Tomorrow", callback_data=f"d:{delivery_id}:sday"),
            ],
        ]
    )


def reminder_list_actions(reminders: list[Reminder]) -> InlineKeyboardMarkup | None:
    rows = [
        [
            InlineKeyboardButton(
                f"Resume reminder {reminder.id}",
                callback_data=f"r:{reminder.id}:resume",
            )
        ]
        for reminder in reminders
        if reminder.id is not None and reminder.status == ReminderStatus.PAUSED
    ]
    return InlineKeyboardMarkup(rows) if rows else None


def format_tasks(tasks: list[Task]) -> str:
    if not tasks:
        return "No active tasks."
    return "Tasks\n" + "\n".join(
        f"• {task.title} (#{task.id}, {task.status.value})" for task in tasks
    )


def format_goals(goals: list[Goal]) -> str:
    if not goals:
        return "No active goals."
    return "Goals\n" + "\n".join(
        f"• {goal.title} (#{goal.id}, {goal.status.value})" for goal in goals
    )


def format_reminders(
    reminders: list[Reminder],
    *,
    target_titles: dict[int, str] | None = None,
) -> str:
    if not reminders:
        return "No reminders."
    blocks = []
    target_titles = target_titles or {}
    for reminder in reminders:
        fallback_target = (
            f"task #{reminder.task_id}"
            if reminder.task_id is not None
            else f"goal #{reminder.goal_id}"
        )
        target = target_titles.get(reminder.id, fallback_target)
        blocks.append(
            f"Reminder #{reminder.id} · {target} · {reminder.status.value}\n"
            f"{format_schedule(reminder.schedule)}"
        )
    return "Reminders\n\n" + "\n\n".join(blocks)


def format_schedule(schedule: ReminderSchedule) -> str:
    at = _format_time(schedule.local_time)
    recurrence = schedule.recurrence
    if recurrence is None:
        return f"Once on {_format_date(schedule.start_date)} at {at} ({schedule.timezone})"

    description = _format_recurrence(recurrence, schedule.start_date)
    bounds = []
    if recurrence.count is not None:
        bounds.append(f"{recurrence.count} times")
    if recurrence.until is not None:
        bounds.append(f"until {_format_date(recurrence.until.date())}")
    suffix = f", {'; '.join(bounds)}" if bounds else ""
    return f"{description} at {at} ({schedule.timezone}){suffix}"


def _format_recurrence(rule: RecurrenceRule, start_date: date) -> str:
    interval = rule.interval
    if rule.frequency == Frequency.HOURLY:
        return "Every hour" if interval == 1 else f"Every {interval} hours"
    if rule.frequency == Frequency.DAILY:
        frequency = "Every day" if interval == 1 else f"Every {interval} days"
        if rule.by_day:
            days = _join_words([_WEEKDAY_NAMES[item.day] for item in rule.by_day])
            return f"{frequency} on {days}"
        return frequency
    if rule.frequency == Frequency.WEEKLY:
        days = rule.by_day or (WeekdayRule(Weekday(start_date.weekday())),)
        day_text = _join_words([_WEEKDAY_NAMES[item.day] for item in days])
        frequency = "Every week" if interval == 1 else f"Every {interval} weeks"
        return f"{frequency} on {day_text}" if day_text else frequency
    if rule.frequency == Frequency.MONTHLY:
        frequency = "Every month" if interval == 1 else f"Every {interval} months"
        selector = _monthly_selector(rule, start_date)
        return f"{frequency} on {selector}"

    frequency = "Every year" if interval == 1 else f"Every {interval} years"
    months = rule.by_month or (start_date.month,)
    month_days = rule.by_month_day or (start_date.day,)
    if len(months) == 1 and len(month_days) == 1:
        month_name = calendar.month_name[months[0]]
        return f"{frequency} on {month_name} {_ordinal(month_days[0])}"
    month_text = _join_words([calendar.month_name[item] for item in months])
    day_text = _join_words([_ordinal(item) for item in month_days])
    return f"{frequency} in {month_text} on {day_text}"


def _monthly_selector(rule: RecurrenceRule, start_date: date) -> str:
    if rule.by_day:
        parts = []
        for weekday in rule.by_day:
            if weekday.ordinal is None:
                parts.append(_WEEKDAY_NAMES[weekday.day])
            else:
                parts.append(
                    f"{_ordinal(weekday.ordinal)} {_WEEKDAY_NAMES[weekday.day]}"
                )
        joined = _join_words(parts)
        if rule.by_set_position:
            positions = _join_words([_ordinal(item) for item in rule.by_set_position])
            return f"the {positions} matching day ({joined})"
        return joined
    days = rule.by_month_day or (start_date.day,)
    return _join_words([_ordinal(day) for day in days])


def _format_time(value: time) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def _format_date(value: date) -> str:
    return f"{value.strftime('%b')} {value.day}, {value.year}"


def _ordinal(value: int) -> str:
    if value == -1:
        return "last"
    if value < 0:
        return f"{abs(value)}th-last"
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def _join_words(items: list[str]) -> str:
    if len(items) < 2:
        return items[0] if items else ""
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


__all__ = [
    "format_goals",
    "format_reminders",
    "format_schedule",
    "format_tasks",
    "reminder_actions",
    "reminder_list_actions",
    "snooze_actions",
]
