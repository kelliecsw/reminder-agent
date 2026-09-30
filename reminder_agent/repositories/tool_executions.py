from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
import sqlite3

from reminder_agent.domain.enums import ToolExecutionStatus
from reminder_agent.domain.models import ToolExecution


class ToolExecutionRepository:
    def get(self, conn: sqlite3.Connection, key: str) -> ToolExecution | None:
        row = conn.execute(
            """
            SELECT id, idempotency_key, request_id, tool_call_id, tool_name,
                   arguments_json, status, result_json, error
            FROM tool_executions WHERE idempotency_key = ?
            """,
            (key,),
        ).fetchone()
        if row is None:
            return None
        return ToolExecution(
            id=int(row["id"]),
            idempotency_key=row["idempotency_key"],
            request_id=row["request_id"],
            tool_call_id=row["tool_call_id"],
            tool_name=row["tool_name"],
            arguments=json.loads(row["arguments_json"]),
            status=ToolExecutionStatus(row["status"]),
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error=row["error"],
        )

    def create(self, conn: sqlite3.Connection, item: ToolExecution, now_utc: datetime) -> ToolExecution:
        cursor = conn.execute(
            """
            INSERT INTO tool_executions (
                idempotency_key, request_id, tool_call_id, tool_name,
                arguments_json, status, result_json, error,
                created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.idempotency_key,
                item.request_id,
                item.tool_call_id,
                item.tool_name,
                json.dumps(item.arguments, ensure_ascii=False, sort_keys=True),
                item.status.value,
                json.dumps(item.result, ensure_ascii=False) if item.result is not None else None,
                item.error,
                now_utc.isoformat(),
                now_utc.isoformat(),
            ),
        )
        return replace(item, id=int(cursor.lastrowid))

    def complete(
        self,
        conn: sqlite3.Connection,
        *,
        key: str,
        result: dict,
        now_utc: datetime,
    ) -> bool:
        cursor = conn.execute(
            """
            UPDATE tool_executions
            SET status = ?, result_json = ?, error = NULL, updated_at_utc = ?
            WHERE idempotency_key = ? AND status = ?
            """,
            (
                ToolExecutionStatus.COMPLETED.value,
                json.dumps(result, ensure_ascii=False),
                now_utc.isoformat(),
                key,
                ToolExecutionStatus.RUNNING.value,
            ),
        )
        return cursor.rowcount == 1
