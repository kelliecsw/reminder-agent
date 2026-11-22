from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from reminder_agent.domain.enums import (
    DeliveryStatus,
    Frequency,
    ReminderPurpose,
    ReminderStatus,
    Weekday,
)
from reminder_agent.domain.models import Delivery, Reminder
from reminder_agent.domain.recurrence import (
    RecurrenceRule,
    ReminderSchedule,
    WeekdayRule,
)
from reminder_agent.telegram.views import (
    format_reminders,
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


def _snoozed_reminder_fixture():
    reminder = Reminder(
        user_id=1,
        purpose=ReminderPurpose.TASK_REMINDER,
        task_id=1,
        schedule=ReminderSchedule(
            start_date=date(2026, 11, 22),
            local_time=time(15, 25),
            timezone="Asia/Singapore",
        ),
        id=7,
        status=ReminderStatus.COMPLETED,
    )
    delivery = Delivery(
        user_id=1,
        reminder_id=7,
        reminder_version=1,
        scheduled_for_utc=datetime(2026, 11, 22, 7, 25, tzinfo=UTC),
        id=3,
        deliver_at_utc=datetime(2026, 11, 22, 7, 46, tzinfo=UTC),
        status=DeliveryStatus.RETRY_WAIT,
    )
    return reminder, delivery


def test_format_reminders_labels_snoozed_completed_reminders():
    reminder, delivery = _snoozed_reminder_fixture()

    text = format_reminders(
        [reminder],
        pending={7: delivery},
        timezone=ZoneInfo("Asia/Singapore"),
    )

    assert "Reminder #7 · task #1 · snoozed" in text
    assert "Snoozed until Nov 22, 2026 at 3:46 PM (Asia/Singapore)" in text


def test_format_reminders_keeps_active_label_for_snoozed_active_reminders():
    reminder, delivery = _snoozed_reminder_fixture()
    active = Reminder(
        user_id=1,
        purpose=ReminderPurpose.TASK_REMINDER,
        task_id=1,
        schedule=reminder.schedule,
        id=7,
        status=ReminderStatus.ACTIVE,
    )

    text = format_reminders(
        [active],
        pending={7: delivery},
        timezone=ZoneInfo("Asia/Singapore"),
    )

    assert "· active" in text
    assert "· snoozed" not in text
    assert "Snoozed until Nov 22, 2026 at 3:46 PM (Asia/Singapore)" in text


def test_format_reminders_ignores_terminal_pending_deliveries():
    reminder, delivery = _snoozed_reminder_fixture()
    acted_on = Delivery(
        user_id=1,
        reminder_id=7,
        reminder_version=1,
        scheduled_for_utc=delivery.scheduled_for_utc,
        id=3,
        deliver_at_utc=delivery.deliver_at_utc,
        status=DeliveryStatus.ACTED_ON,
    )

    text = format_reminders(
        [reminder],
        pending={7: acted_on},
        timezone=ZoneInfo("Asia/Singapore"),
    )

    assert "· completed" in text
    assert "Snoozed until" not in text
