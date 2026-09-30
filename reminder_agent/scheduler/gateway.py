from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class MessageGateway(Protocol):
    async def send(
        self,
        *,
        delivery_id: int,
        user_id: int,
        text: str,
    ) -> None: ...


class ConsoleGateway:
    async def send(
        self,
        *,
        delivery_id: int,
        user_id: int,
        text: str,
    ) -> None:
        print(f"REMINDER: {text}", flush=True)


@dataclass(frozen=True)
class SentMessage:
    delivery_id: int
    user_id: int
    text: str


@dataclass
class FakeGateway:
    failures_remaining: int = 0
    messages: list[SentMessage] = field(default_factory=list)

    async def send(
        self,
        *,
        delivery_id: int,
        user_id: int,
        text: str,
    ) -> None:
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise RuntimeError("Fake gateway send failed.")
        self.messages.append(
            SentMessage(
                delivery_id=delivery_id,
                user_id=user_id,
                text=text,
            )
        )
