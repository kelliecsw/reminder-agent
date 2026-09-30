from datetime import date, time

from reminder_agent.domain.enums import Frequency, Weekday
from reminder_agent.domain.recurrence import (
    RecurrenceRule,
    ReminderSchedule,
    WeekdayRule,
)
from reminder_agent.telegram.views import (
    format_schedule,
    reminder_actions,
    snooze_actions,
)


def test_formats_weekly_schedule():
    schedule = ReminderSchedule(
        start_date=date(2026, 8, 22),
        local_time=time(20),
        timezone="Asia/Singapore",
        recurrence=RecurrenceRule(
            frequency=Frequency.WEEKLY,
            by_day=(
                WeekdayRule(Weekday.MONDAY),
                WeekdayRule(Weekday.THURSDAY),
            ),
        ),
    )

    assert format_schedule(schedule) == (
        "Every week on Monday and Thursday at 8:00 PM (Asia/Singapore)"
    )


def test_formats_monthly_ordinal_schedule():
    schedule = ReminderSchedule(
        start_date=date(2026, 8, 1),
        local_time=time(9),
        timezone="Asia/Singapore",
        recurrence=RecurrenceRule(
            frequency=Frequency.MONTHLY,
            by_day=(WeekdayRule(Weekday.SATURDAY, ordinal=2),),
        ),
    )

    assert format_schedule(schedule) == (
        "Every month on 2nd Saturday at 9:00 AM (Asia/Singapore)"
    )


def test_button_callback_data_contains_delivery_id():
    reminder_data = [
        button.callback_data
        for row in reminder_actions(42).inline_keyboard
        for button in row
    ]
    snooze_data = [
        button.callback_data
        for row in snooze_actions(42).inline_keyboard
        for button in row
    ]

    assert reminder_data == ["d:42:done", "d:42:s", "d:42:skip", "d:42:pause"]
    assert snooze_data == ["d:42:s10", "d:42:s60", "d:42:sday"]
