from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from langchain_core.messages import BaseMessage, messages_from_dict, messages_to_dict

from reminder_agent.db.database import Database
from reminder_agent.domain.models import AgentThread
from reminder_agent.repositories.agent_threads import AgentThreadRepository


@dataclass(frozen=True)
class LoadedAgentThread:
    messages: list[BaseMessage]
    version: int | None


class AgentThreadService:
    def __init__(
        self,
        database: Database,
        *,
        repository: AgentThreadRepository | None = None,
        history_limit: int = 100,
    ) -> None:
        if history_limit < 1:
            raise ValueError("history_limit must be at least 1.")
        self.database = database
        self.repository = repository or AgentThreadRepository()
        self.history_limit = history_limit

    def load(self, user_id: int) -> LoadedAgentThread:
        with self.database.connect() as conn:
            thread = self.repository.get(conn, user_id)
        if thread is None:
            return LoadedAgentThread(messages=[], version=None)
        return LoadedAgentThread(
            messages=list(messages_from_dict(thread.messages)),
            version=thread.version,
        )

    def save(
        self,
        user_id: int,
        messages: Sequence[BaseMessage],
        *,
        expected_version: int | None,
        now_utc: datetime,
    ) -> LoadedAgentThread:
        trimmed = list(messages)[-self.history_limit :]
        with self.database.transaction(immediate=True) as conn:
            saved = self.repository.save(
                conn,
                AgentThread(
                    application_user_id=user_id,
                    messages=messages_to_dict(trimmed),
                ),
                expected_version=expected_version,
                now_utc=now_utc,
            )
        return LoadedAgentThread(
            messages=trimmed,
            version=saved.version,
        )


__all__ = ["AgentThreadService", "LoadedAgentThread"]
