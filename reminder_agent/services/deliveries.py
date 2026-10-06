from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from reminder_agent.db.database import Database
from reminder_agent.domain.enums import (
    DeliveryStatus,
    GoalStatus,
    ReminderStatus,
    TaskStatus,
)
from reminder_agent.domain.errors import (
    InvalidOwnershipError,
    InvalidStateTransitionError,
    NotFoundError,
)
from reminder_agent.domain.models import Delivery
from reminder_agent.domain.recurrence import next_occurrence
from reminder_agent.repositories.deliveries import DeliveryRepository
from reminder_agent.repositories.goals import GoalRepository
from reminder_agent.repositories.reminders import ReminderRepository
from reminder_agent.repositories.tasks import TaskRepository

from ._common import require_reminder


@dataclass(frozen=True)
class DeliveryMessage:
    delivery_id: int
    user_id: int
    text: str


class DeliveryService:
    def __init__(
        self,
        database: Database,
        *,
        delivery_repo: DeliveryRepository | None = None,
        reminder_repo: ReminderRepository | None = None,
        task_repo: TaskRepository | None = None,
        goal_repo: GoalRepository | None = None,
    ) -> None:
        self.database = database
        self.delivery_repo = delivery_repo or DeliveryRepository()
        self.reminder_repo = reminder_repo or ReminderRepository()
        self.task_repo = task_repo or TaskRepository()
        self.goal_repo = goal_repo or GoalRepository()

    def get(self, user_id: int, delivery_id: int) -> Delivery:
        with self.database.connect() as conn:
            return self._require_delivery(conn, user_id, delivery_id)

    def list(self, user_id: int, reminder_id: int) -> list[Delivery]:
        with self.database.connect() as conn:
            require_reminder(conn, self.reminder_repo, user_id, reminder_id)
            deliveries = self.delivery_repo.list_by_reminder(conn, reminder_id)
            if any(delivery.user_id != user_id for delivery in deliveries):
                raise InvalidOwnershipError(
                    "Delivery and reminder ownership do not match."
                )
            return deliveries

    def materialize_due(
        self,
        now_utc: datetime,
        *,
        limit: int = 100,
    ) -> list[Delivery]:
        _require_utc(now_utc, field_name="now_utc")
        created: list[Delivery] = []

        with self.database.transaction(immediate=True) as conn:
            reminders = self.reminder_repo.find_due(conn, now_utc, limit=limit)
            for reminder in reminders:
                if reminder.id is None or reminder.next_fire_at_utc is None:
                    continue

                scheduled_for = reminder.next_fire_at_utc
                delivery, was_created = self.delivery_repo.create_occurrence(
                    conn,
                    Delivery(
                        user_id=reminder.user_id,
                        reminder_id=reminder.id,
                        reminder_version=reminder.version,
                        scheduled_for_utc=scheduled_for,
                        deliver_at_utc=scheduled_for,
                    ),
                )
                if was_created:
                    created.append(delivery)

                following = next_occurrence(
                    reminder.schedule,
                    after_utc=scheduled_for,
                )
                updated = replace(
                    reminder,
                    next_fire_at_utc=following,
                    status=(
                        ReminderStatus.COMPLETED
                        if following is None
                        else reminder.status
                    ),
                )
                self.reminder_repo.update(conn, updated)

        return created

    def claim_ready(
        self,
        now_utc: datetime,
        *,
        lease_seconds: float = 60.0,
    ) -> Delivery | None:
        _require_utc(now_utc, field_name="now_utc")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive.")
        with self.database.transaction(immediate=True) as conn:
            for delivery in self.delivery_repo.find_ready(conn, now_utc):
                if delivery.id is None:
                    continue
                token = uuid4().hex
                if self.delivery_repo.claim(
                    conn,
                    delivery.id,
                    now_utc,
                    claim_token=token,
                    lease_expires_at_utc=now_utc + timedelta(seconds=lease_seconds),
                ):
                    claimed = self.delivery_repo.get(conn, delivery.id)
                    if claimed is None:
                        raise RuntimeError("Claimed delivery disappeared.")
                    return claimed
        return None

    def prepare_for_send(
        self,
        delivery_id: int,
        *,
        claim_token: str,
    ) -> DeliveryMessage | None:
        with self.database.transaction() as conn:
            delivery = self._get_delivery(conn, delivery_id)
            if delivery.status != DeliveryStatus.CLAIMED:
                raise InvalidStateTransitionError(
                    f"Cannot prepare {delivery.status.value} delivery {delivery_id}."
                )
            self._require_claim(delivery, claim_token)

            reminder = self.reminder_repo.get(conn, delivery.reminder_id)
            if (
                reminder is None
                or reminder.user_id != delivery.user_id
                or reminder.version != delivery.reminder_version
                or reminder.status
                not in {ReminderStatus.ACTIVE, ReminderStatus.COMPLETED}
            ):
                self.delivery_repo.update(
                    conn,
                    replace(
                        delivery,
                        status=DeliveryStatus.CANCELLED,
                        claim_token=None,
                        claimed_at_utc=None,
                        lease_expires_at_utc=None,
                    ),
                )
                return None

            text: str | None = None
            if reminder.task_id is not None:
                task = self.task_repo.get(conn, reminder.task_id)
                if (
                    task is not None
                    and task.user_id == delivery.user_id
                    and task.status not in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
                ):
                    text = task.title
            elif reminder.goal_id is not None:
                goal = self.goal_repo.get(conn, reminder.goal_id)
                if (
                    goal is not None
                    and goal.user_id == delivery.user_id
                    and goal.status not in {GoalStatus.COMPLETED, GoalStatus.CANCELLED}
                ):
                    text = f"Goal reminder: {goal.title}"

            if text is None:
                self.delivery_repo.update(
                    conn,
                    replace(
                        delivery,
                        status=DeliveryStatus.CANCELLED,
                        claim_token=None,
                        claimed_at_utc=None,
                        lease_expires_at_utc=None,
                    ),
                )
                return None

            return DeliveryMessage(
                delivery_id=delivery_id,
                user_id=delivery.user_id,
                text=text,
            )

    def mark_sent(
        self,
        delivery_id: int,
        *,
        claim_token: str,
    ) -> Delivery:
        return self._system_transition(
            delivery_id,
            from_status=DeliveryStatus.CLAIMED,
            to_status=DeliveryStatus.SENT,
            claim_token=claim_token,
        )

    def retry(
        self,
        delivery_id: int,
        *,
        error: str,
        retry_at_utc: datetime,
        max_attempts: int,
        claim_token: str,
    ) -> Delivery:
        _require_utc(retry_at_utc, field_name="retry_at_utc")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")

        with self.database.transaction() as conn:
            delivery = self._get_delivery(conn, delivery_id)
            if delivery.status != DeliveryStatus.CLAIMED:
                raise InvalidStateTransitionError(
                    f"Cannot retry {delivery.status.value} delivery {delivery_id}."
                )
            self._require_claim(delivery, claim_token)
            updated = replace(
                delivery,
                deliver_at_utc=retry_at_utc,
                status=(
                    DeliveryStatus.FAILED
                    if delivery.attempt_count >= max_attempts
                    else DeliveryStatus.RETRY_WAIT
                ),
                last_error=error.strip()[:2_000] or "Unknown delivery error",
                claim_token=None,
                claimed_at_utc=None,
                lease_expires_at_utc=None,
            )
            self.delivery_repo.update(conn, updated)
            return updated

    def snooze(
        self,
        user_id: int,
        delivery_id: int,
        deliver_at_utc: datetime,
    ) -> Delivery:
        _require_utc(deliver_at_utc, field_name="deliver_at_utc")
        with self.database.transaction() as conn:
            delivery = self._require_delivery(conn, user_id, delivery_id)
            if delivery.status != DeliveryStatus.SENT:
                raise InvalidStateTransitionError(
                    f"Cannot snooze {delivery.status.value} delivery {delivery_id}."
                )
            updated = replace(
                delivery,
                deliver_at_utc=deliver_at_utc,
                status=DeliveryStatus.RETRY_WAIT,
                last_error=None,
            )
            self.delivery_repo.update(conn, updated)
            return updated

    def skip(self, user_id: int, delivery_id: int) -> Delivery:
        return self._user_transition(user_id, delivery_id, DeliveryStatus.SKIPPED)

    def mark_acted_on(self, user_id: int, delivery_id: int) -> Delivery:
        return self._user_transition(user_id, delivery_id, DeliveryStatus.ACTED_ON)

    def _user_transition(
        self,
        user_id: int,
        delivery_id: int,
        status: DeliveryStatus,
    ) -> Delivery:
        with self.database.transaction() as conn:
            delivery = self._require_delivery(conn, user_id, delivery_id)
            if delivery.status != DeliveryStatus.SENT:
                raise InvalidStateTransitionError(
                    f"Cannot mark {delivery.status.value} delivery {delivery_id} as {status.value}."
                )
            updated = replace(delivery, status=status)
            self.delivery_repo.update(conn, updated)
            return updated

    def _system_transition(
        self,
        delivery_id: int,
        *,
        from_status: DeliveryStatus,
        to_status: DeliveryStatus,
        claim_token: str,
    ) -> Delivery:
        with self.database.transaction() as conn:
            delivery = self._get_delivery(conn, delivery_id)
            if delivery.status != from_status:
                raise InvalidStateTransitionError(
                    f"Cannot move delivery {delivery_id} from "
                    f"{delivery.status.value} to {to_status.value}."
                )
            self._require_claim(delivery, claim_token)
            updated = replace(
                delivery,
                status=to_status,
                last_error=None,
                claim_token=None,
                claimed_at_utc=None,
                lease_expires_at_utc=None,
            )
            self.delivery_repo.update(conn, updated)
            return updated

    def _get_delivery(self, conn, delivery_id: int) -> Delivery:
        delivery = self.delivery_repo.get(conn, delivery_id)
        if delivery is None:
            raise NotFoundError(f"Delivery {delivery_id} was not found.")
        return delivery

    @staticmethod
    def _require_claim(delivery: Delivery, claim_token: str) -> None:
        if delivery.claim_token != claim_token:
            raise InvalidStateTransitionError(
                f"Delivery {delivery.id} is owned by a different worker claim."
            )

    def _require_delivery(self, conn, user_id: int, delivery_id: int) -> Delivery:
        delivery = self._get_delivery(conn, delivery_id)
        if delivery.user_id != user_id:
            raise InvalidOwnershipError(
                f"Delivery {delivery_id} does not belong to user {user_id}."
            )
        reminder = require_reminder(
            conn, self.reminder_repo, user_id, delivery.reminder_id
        )
        if reminder.user_id != delivery.user_id:
            raise InvalidOwnershipError(
                "Delivery and reminder ownership do not match."
            )
        return delivery


def _require_utc(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field_name} must be expressed in UTC.")
