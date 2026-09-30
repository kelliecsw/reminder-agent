from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import (
    DAILY,
    HOURLY,
    MONTHLY,
    WEEKLY,
    YEARLY,
    FR,
    MO,
    SA,
    SU,
    TH,
    TU,
    WE,
    rrule,
)

from .enums import Frequency, Weekday
from .errors import InvalidRecurrenceError, InvalidTimezoneError


_FREQUENCY_MAP = {
    Frequency.HOURLY: HOURLY,
    Frequency.DAILY: DAILY,
    Frequency.WEEKLY: WEEKLY,
    Frequency.MONTHLY: MONTHLY,
    Frequency.YEARLY: YEARLY,
}

_WEEKDAY_MAP = {
    Weekday.MONDAY: MO,
    Weekday.TUESDAY: TU,
    Weekday.WEDNESDAY: WE,
    Weekday.THURSDAY: TH,
    Weekday.FRIDAY: FR,
    Weekday.SATURDAY: SA,
    Weekday.SUNDAY: SU,
}


@dataclass(frozen=True)
class WeekdayRule:
    """
    Select a weekday, optionally by ordinal position.

    Examples:
        WeekdayRule(Weekday.MONDAY)
            -> Monday

        WeekdayRule(Weekday.SATURDAY, ordinal=2)
            -> second Saturday

        WeekdayRule(Weekday.FRIDAY, ordinal=-1)
            -> last Friday
    """

    day: Weekday
    ordinal: int | None = None

    def __post_init__(self) -> None:
        if self.ordinal is not None:
            if self.ordinal == 0 or not -53 <= self.ordinal <= 53:
                raise InvalidRecurrenceError(
                    "Weekday ordinal must be between -53 and 53, excluding 0."
                )


@dataclass(frozen=True)
class RecurrenceRule:
    """
    Practical RFC 5545-style recurrence rule.

    Examples:
        Every 3 days:
            frequency=Frequency.DAILY
            interval=3

        Every Monday and Thursday:
            frequency=Frequency.WEEKLY
            by_day=(
                WeekdayRule(Weekday.MONDAY),
                WeekdayRule(Weekday.THURSDAY),
            )

        Second Saturday of every month:
            frequency=Frequency.MONTHLY
            by_day=(WeekdayRule(Weekday.SATURDAY, ordinal=2),)

        Last weekday of every month:
            frequency=Frequency.MONTHLY
            by_day=(
                WeekdayRule(Weekday.MONDAY),
                WeekdayRule(Weekday.TUESDAY),
                WeekdayRule(Weekday.WEDNESDAY),
                WeekdayRule(Weekday.THURSDAY),
                WeekdayRule(Weekday.FRIDAY),
            )
            by_set_position=(-1,)

        Every Christmas:
            frequency=Frequency.YEARLY
            by_month=(12,)
            by_month_day=(25,)

    count and until are mutually exclusive.
    """

    frequency: Frequency
    interval: int = 1

    by_day: tuple[WeekdayRule, ...] = ()
    by_month_day: tuple[int, ...] = ()
    by_month: tuple[int, ...] = ()
    by_set_position: tuple[int, ...] = ()

    count: int | None = None
    until: datetime | None = None

    def __post_init__(self) -> None:
        if self.interval < 1:
            raise InvalidRecurrenceError(
                "Recurrence interval must be at least 1."
            )

        if self.count is not None and self.count < 1:
            raise InvalidRecurrenceError(
                "Recurrence count must be at least 1."
            )

        if self.count is not None and self.until is not None:
            raise InvalidRecurrenceError(
                "Recurrence cannot define both count and until."
            )

        if self.until is not None:
            _require_aware_datetime(self.until, field_name="until")

        if len(set(self.by_day)) != len(self.by_day):
            raise InvalidRecurrenceError(
                "by_day must not contain duplicate weekday rules."
            )

        if len(set(self.by_month_day)) != len(self.by_month_day):
            raise InvalidRecurrenceError(
                "by_month_day must not contain duplicate values."
            )

        if len(set(self.by_month)) != len(self.by_month):
            raise InvalidRecurrenceError(
                "by_month must not contain duplicate values."
            )

        if len(set(self.by_set_position)) != len(self.by_set_position):
            raise InvalidRecurrenceError(
                "by_set_position must not contain duplicate values."
            )

        if any(
            value == 0 or not -31 <= value <= 31
            for value in self.by_month_day
        ):
            raise InvalidRecurrenceError(
                "by_month_day values must be between -31 and 31, excluding 0."
            )

        if any(not 1 <= month <= 12 for month in self.by_month):
            raise InvalidRecurrenceError(
                "by_month values must be between 1 and 12."
            )

        if any(
            value == 0 or not -366 <= value <= 366
            for value in self.by_set_position
        ):
            raise InvalidRecurrenceError(
                "by_set_position values must be between -366 and 366, excluding 0."
            )

        has_ordinal_day = any(
            weekday.ordinal is not None
            for weekday in self.by_day
        )

        if (
            has_ordinal_day
            and self.frequency not in {Frequency.MONTHLY, Frequency.YEARLY}
        ):
            raise InvalidRecurrenceError(
                "Ordinal weekday rules are only supported for monthly or yearly recurrence."
            )

        if self.frequency == Frequency.HOURLY and (
            self.by_day
            or self.by_month_day
            or self.by_month
            or self.by_set_position
        ):
            raise InvalidRecurrenceError(
                "Hourly recurrence does not support calendar selectors."
            )

        if self.frequency == Frequency.DAILY and (
            self.by_month_day
            or self.by_month
            or self.by_set_position
        ):
            raise InvalidRecurrenceError(
                "Daily recurrence does not support month-based selectors."
            )

        if self.frequency == Frequency.WEEKLY and (
            self.by_month_day
            or self.by_month
            or self.by_set_position
        ):
            raise InvalidRecurrenceError(
                "Weekly recurrence only supports by_day selectors."
            )

        if self.frequency == Frequency.MONTHLY and self.by_month:
            raise InvalidRecurrenceError(
                "Monthly recurrence does not use by_month; use yearly recurrence instead."
            )

        if self.by_set_position and not (
            self.by_day or self.by_month_day
        ):
            raise InvalidRecurrenceError(
                "by_set_position requires by_day or by_month_day selectors."
            )


@dataclass(frozen=True)
class ReminderSchedule:
    """
    Defines when a reminder starts and how it repeats.

    recurrence=None means the reminder fires once only.
    """

    start_date: date
    local_time: time
    timezone: str
    recurrence: RecurrenceRule | None = None

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise InvalidTimezoneError(
                f"Unknown IANA timezone: {self.timezone}"
            ) from exc


def next_occurrence(
    schedule: ReminderSchedule,
    *,
    after_utc: datetime,
) -> datetime | None:
    """
    Return the first scheduled occurrence strictly after ``after_utc``.

    The returned datetime is timezone-aware and normalized to UTC.
    """
    _require_aware_datetime(after_utc, field_name="after_utc")

    timezone = ZoneInfo(schedule.timezone)
    start_local = datetime.combine(
        schedule.start_date,
        schedule.local_time,
        tzinfo=timezone,
    )

    after_utc = after_utc.astimezone(UTC)

    if schedule.recurrence is None:
        start_utc = start_local.astimezone(UTC)
        return start_utc if start_utc > after_utc else None

    rule = _build_rrule(
        schedule=schedule,
        start_local=start_local,
    )

    next_local = rule.after(
        after_utc.astimezone(timezone),
        inc=False,
    )

    if next_local is None:
        return None

    return next_local.astimezone(UTC)


def _build_rrule(
    *,
    schedule: ReminderSchedule,
    start_local: datetime,
):
    recurrence = schedule.recurrence

    if recurrence is None:
        raise InvalidRecurrenceError(
            "Cannot build a recurrence rule for a one-off schedule."
        )

    kwargs = {
        "freq": _FREQUENCY_MAP[recurrence.frequency],
        "dtstart": start_local,
        "interval": recurrence.interval,
    }

    if recurrence.by_day:
        kwargs["byweekday"] = tuple(
            _to_dateutil_weekday(rule)
            for rule in recurrence.by_day
        )

    if recurrence.by_month_day:
        kwargs["bymonthday"] = recurrence.by_month_day

    if recurrence.by_month:
        kwargs["bymonth"] = recurrence.by_month

    if recurrence.by_set_position:
        kwargs["bysetpos"] = recurrence.by_set_position

    if recurrence.count is not None:
        kwargs["count"] = recurrence.count

    if recurrence.until is not None:
        kwargs["until"] = recurrence.until.astimezone(UTC)

    return rrule(**kwargs)


def _to_dateutil_weekday(rule: WeekdayRule):
    weekday = _WEEKDAY_MAP[rule.day]

    if rule.ordinal is None:
        return weekday

    return weekday(rule.ordinal)


def _require_aware_datetime(
    value: datetime,
    *,
    field_name: str,
) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            f"{field_name} must be a timezone-aware datetime."
        )
