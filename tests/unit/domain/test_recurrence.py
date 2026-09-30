from datetime import UTC, date, datetime, time

from reminder_agent.domain.enums import Frequency, Weekday
from reminder_agent.domain.recurrence import (
    RecurrenceRule,
    ReminderSchedule,
    WeekdayRule,
    next_occurrence,
)


def test_one_off_reminder_returns_scheduled_time():
    schedule = ReminderSchedule(
        start_date=date(2026, 8, 22),
        local_time=time(9, 0),
        timezone="Asia/Singapore",
    )

    result = next_occurrence(
        schedule,
        after_utc=datetime(2026, 8, 21, 0, 0, tzinfo=UTC),
    )

    assert result == datetime(2026, 8, 22, 1, 0, tzinfo=UTC)


def test_one_off_reminder_returns_none_after_it_has_passed():
    schedule = ReminderSchedule(
        start_date=date(2026, 8, 22),
        local_time=time(9, 0),
        timezone="Asia/Singapore",
    )

    result = next_occurrence(
        schedule,
        after_utc=datetime(2026, 8, 22, 2, 0, tzinfo=UTC),
    )

    assert result is None


def test_every_three_days_is_anchored_to_start_date():
    schedule = ReminderSchedule(
        start_date=date(2026, 8, 22),
        local_time=time(9, 0),
        timezone="Asia/Singapore",
        recurrence=RecurrenceRule(
            frequency=Frequency.DAILY,
            interval=3,
        ),
    )

    # 29 Aug 2026 00:00 Singapore = 28 Aug 2026 16:00 UTC.
    # Sequence is 22, 25, 28, 31 Aug, so the next occurrence is 31 Aug.
    result = next_occurrence(
        schedule,
        after_utc=datetime(2026, 8, 28, 16, 0, tzinfo=UTC),
    )

    assert result == datetime(2026, 8, 31, 1, 0, tzinfo=UTC)


def test_weekly_reminder_on_monday_and_thursday():
    schedule = ReminderSchedule(
        start_date=date(2026, 8, 17),
        local_time=time(9, 0),
        timezone="Asia/Singapore",
        recurrence=RecurrenceRule(
            frequency=Frequency.WEEKLY,
            by_day=(
                WeekdayRule(Weekday.MONDAY),
                WeekdayRule(Weekday.THURSDAY),
            ),
        ),
    )

    # After Monday 17 Aug, the next selected day is Thursday 20 Aug.
    result = next_occurrence(
        schedule,
        after_utc=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
    )

    assert result == datetime(2026, 8, 20, 1, 0, tzinfo=UTC)


def test_second_saturday_of_every_month():
    schedule = ReminderSchedule(
        start_date=date(2026, 8, 1),
        local_time=time(10, 0),
        timezone="Asia/Singapore",
        recurrence=RecurrenceRule(
            frequency=Frequency.MONTHLY,
            by_day=(
                WeekdayRule(
                    Weekday.SATURDAY,
                    ordinal=2,
                ),
            ),
        ),
    )

    result = next_occurrence(
        schedule,
        after_utc=datetime(2026, 8, 1, 0, 0, tzinfo=UTC),
    )

    # Second Saturday of Aug 2026 is 8 Aug.
    assert result == datetime(2026, 8, 8, 2, 0, tzinfo=UTC)
