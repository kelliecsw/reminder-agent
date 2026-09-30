from __future__ import annotations

from datetime import datetime
import json
import sqlite3

from reminder_agent.domain.models import AgentThread


class AgentThreadRepository:
    def get(self, conn: sqlite3.Connection, user_id: int) -> AgentThread | None:
        row = conn.execute(
            """
            SELECT application_user_id, messages_json, version
            FROM agent_threads WHERE application_user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return AgentThread(
            application_user_id=int(row["application_user_id"]),
            messages=json.loads(row["messages_json"]),
            version=int(row["version"]),
        )

    def save(
        self,
        conn: sqlite3.Connection,
        thread: AgentThread,
        *,
        expected_version: int | None,
        now_utc: datetime,
    ) -> AgentThread:
        messages = json.dumps(thread.messages, ensure_ascii=False)
        if expected_version is None:
            cursor = conn.execute(
                """
                INSERT INTO agent_threads (
                    application_user_id, messages_json,
                    version, created_at_utc, updated_at_utc
                ) VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(application_user_id) DO NOTHING
                """,
                (
                    thread.application_user_id,
                    messages,
                    now_utc.isoformat(),
                    now_utc.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Agent thread was created concurrently.")
            return AgentThread(
                application_user_id=thread.application_user_id,
                messages=thread.messages,
                version=1,
            )
        new_version = expected_version + 1
        cursor = conn.execute(
            """
            UPDATE agent_threads
            SET messages_json = ?, version = ?,
                updated_at_utc = ?
            WHERE application_user_id = ? AND version = ?
            """,
            (
                messages,
                new_version,
                now_utc.isoformat(),
                thread.application_user_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Agent thread changed concurrently.")
        return AgentThread(
            application_user_id=thread.application_user_id,
            messages=thread.messages,
            version=new_version,
        )
