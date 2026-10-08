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
    context_summary: str = ""
    compression_step_count: int = 0


class AgentThreadService:
    def __init__(
        self,
        database: Database,
        *,
        repository: AgentThreadRepository | None = None,
    ) -> None:
        self.database = database
        self.repository = repository or AgentThreadRepository()

    def load(self, user_id: int) -> LoadedAgentThread:
        with self.database.connect() as conn:
            thread = self.repository.get(conn, user_id)
        if thread is None:
            return LoadedAgentThread(messages=[], version=None)
        return LoadedAgentThread(
            messages=list(messages_from_dict(thread.messages)),
            version=thread.version,
            context_summary=thread.context_summary,
            compression_step_count=thread.compression_step_count,
        )

    def save(
        self,
        user_id: int,
        messages: Sequence[BaseMessage],
        *,
        expected_version: int | None,
        now_utc: datetime,
        context_summary: str = "",
        compression_step_count: int = 0,
    ) -> LoadedAgentThread:
        durable_messages = list(messages)
        with self.database.transaction(immediate=True) as conn:
            saved = self.repository.save(
                conn,
                AgentThread(
                    application_user_id=user_id,
                    messages=messages_to_dict(durable_messages),
                    context_summary=context_summary,
                    compression_step_count=compression_step_count,
                ),
                expected_version=expected_version,
                now_utc=now_utc,
            )
        return LoadedAgentThread(
            messages=durable_messages,
            version=saved.version,
            context_summary=saved.context_summary,
            compression_step_count=saved.compression_step_count,
        )


__all__ = ["AgentThreadService", "LoadedAgentThread"]
