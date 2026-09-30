from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging

from reminder_agent.clock import utc_now
from reminder_agent.domain.models import Delivery
from reminder_agent.services.deliveries import DeliveryService


logger = logging.getLogger(__name__)


class ReminderMaterializer:
    def __init__(
        self,
        delivery_service: DeliveryService,
        *,
        clock: Callable[[], datetime] = utc_now,
        batch_size: int = 100,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1.")
        self.delivery_service = delivery_service
        self.clock = clock
        self.batch_size = batch_size

    def run_once(self, now_utc: datetime | None = None) -> list[Delivery]:
        now_utc = now_utc or self.clock()
        deliveries = self.delivery_service.materialize_due(
            now_utc,
            limit=self.batch_size,
        )
        if deliveries:
            logger.info(
                "Materialized %s reminder delivery occurrence(s).",
                len(deliveries),
            )
        return deliveries
