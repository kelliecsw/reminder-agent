from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .enums import (
    DeliveryStatus,
    GoalStatus,
    ReminderPurpose,
    ReminderStatus,
    TaskStatus,
    OutboxStatus,
    TelegramUpdateStatus,
    ToolExecutionStatus,
)
from .errors import InvalidReminderTargetError
from .recurrence import ReminderSchedule


@dataclass(kw_only=True)
class Goal:
    user_id: int
    title: str

    id: int | None = None
    notes: str | None = None
    status: GoalStatus = GoalStatus.ACTIVE


@dataclass(kw_only=True)
class GoalProgress:
    goal_id: int
    user_id: int
    occurred_on: date
    note: str

    id: int | None = None

    def __post_init__(self) -> None:
        if not self.note.strip():
            raise ValueError("Goal progress note must not be empty.")


@dataclass(kw_only=True)
class Task:
    user_id: int
    title: str

    id: int | None = None
    goal_id: int | None = None
    notes: str | None = None
    status: TaskStatus = TaskStatus.ACTIVE


@dataclass(kw_only=True)
class Reminder:
    user_id: int
    purpose: ReminderPurpose
    schedule: ReminderSchedule

    id: int | None = None
    goal_id: int | None = None
    task_id: int | None = None

    next_fire_at_utc: datetime | None = None
    status: ReminderStatus = ReminderStatus.ACTIVE
    version: int = 1

    def __post_init__(self) -> None:
        has_goal = self.goal_id is not None
        has_task = self.task_id is not None

        if has_goal == has_task:
            raise InvalidReminderTargetError(
                "Reminder must target exactly one goal or one task."
            )

        if (
            self.purpose == ReminderPurpose.TASK_REMINDER
            and self.task_id is None
        ):
            raise InvalidReminderTargetError(
                "A task reminder must target a task."
            )

        if (
            self.purpose == ReminderPurpose.GOAL_REMINDER
            and self.goal_id is None
        ):
            raise InvalidReminderTargetError(
                "A goal reminder must target a goal."
            )

        if self.version < 1:
            raise ValueError(
                "Reminder version must be at least 1."
            )

        if self.next_fire_at_utc is not None:
            _require_utc_datetime(
                self.next_fire_at_utc,
                field_name="next_fire_at_utc",
            )


@dataclass(kw_only=True)
class Delivery:
    user_id: int
    reminder_id: int
    reminder_version: int
    scheduled_for_utc: datetime

    id: int | None = None
    deliver_at_utc: datetime | None = None
    status: DeliveryStatus = DeliveryStatus.PENDING
    attempt_count: int = 0
    last_error: str | None = None
    claim_token: str | None = None
    claimed_at_utc: datetime | None = None
    lease_expires_at_utc: datetime | None = None

    def __post_init__(self) -> None:
        if self.reminder_version < 1:
            raise ValueError(
                "Delivery reminder_version must be at least 1."
            )

        _require_utc_datetime(
            self.scheduled_for_utc,
            field_name="scheduled_for_utc",
        )

        if self.deliver_at_utc is None:
            self.deliver_at_utc = self.scheduled_for_utc
        else:
            _require_utc_datetime(
                self.deliver_at_utc,
                field_name="deliver_at_utc",
            )

        if self.attempt_count < 0:
            raise ValueError("Delivery attempt_count must not be negative.")


@dataclass(kw_only=True)
class TelegramUpdateRecord:
    update_id: int
    telegram_user_id: int
    telegram_chat_id: int
    application_user_id: int
    update_kind: str
    payload: dict[str, Any]
    next_attempt_at_utc: datetime
    status: TelegramUpdateStatus = TelegramUpdateStatus.RECEIVED
    attempt_count: int = 0
    last_error: str | None = None
    claim_token: str | None = None
    claimed_at_utc: datetime | None = None
    lease_expires_at_utc: datetime | None = None


@dataclass(kw_only=True)
class OutboxMessage:
    dedupe_key: str
    application_user_id: int
    text: str
    next_attempt_at_utc: datetime
    id: int | None = None
    delivery_id: int | None = None
    telegram_chat_id: int | None = None
    keyboard: dict[str, Any] | None = None
    rich_message: dict[str, Any] | None = None
    status: OutboxStatus = OutboxStatus.PENDING
    attempt_count: int = 0
    last_error: str | None = None
    claim_token: str | None = None
    claimed_at_utc: datetime | None = None
    lease_expires_at_utc: datetime | None = None
    telegram_message_id: int | None = None


@dataclass(kw_only=True)
class AgentThread:
    application_user_id: int
    messages: list[dict[str, Any]]
    context_summary: str = ""
    compression_step_count: int = 0
    version: int = 1


@dataclass(kw_only=True)
class ToolExecution:
    idempotency_key: str
    request_id: str
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    status: ToolExecutionStatus
    id: int | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


def _require_utc_datetime(
    value: datetime,
    *,
    field_name: str,
) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            f"{field_name} must be a timezone-aware datetime."
        )

    if value.utcoffset() != timedelta(0):
        raise ValueError(
            f"{field_name} must be expressed in UTC."
        )
