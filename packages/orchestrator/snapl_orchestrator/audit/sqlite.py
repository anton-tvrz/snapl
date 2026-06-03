"""SqliteAuditLog — durable, append-only audit log backed by SQLite (aiosqlite).

WAL journal mode is enabled at connection open so reads do not block writes.
The append path serialises writes through an asyncio.Lock — one writer per
worker process; reads use short-lived connections.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

import aiosqlite

from snapl_orchestrator.audit.abc import AuditLog
from snapl_orchestrator.exceptions import AuditLogError
from snapl_orchestrator.models import AuditEvent, AuditEventType, WorkflowReason

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_SELECT_SQL = """
SELECT
    event_id, workflow_id, workflow_type, target_id,
    event_type, activity_name, outcome, reason,
    payload_json, timestamp, actor
FROM audit_events
"""


class SqliteAuditLog(AuditLog):
    """File-backed, append-only AuditLog via SQLite + aiosqlite.

    Args:
        database_url: SQLite path. Use ":memory:" for in-process testing.
    """

    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url
        self._write_lock = asyncio.Lock()

    @property
    def database_url(self) -> str:
        return self._database_url

    async def initialize(self) -> None:
        """Apply the schema and set WAL journal mode. Call once at boot."""
        ddl = _SCHEMA_PATH.read_text()
        try:
            async with aiosqlite.connect(self._database_url) as conn:
                if self._database_url != ":memory:":
                    await conn.execute("PRAGMA journal_mode=WAL;")
                await conn.executescript(ddl)
                await conn.commit()
        except aiosqlite.Error as exc:
            raise AuditLogError(f"failed to initialize audit log: {exc}") from exc

    async def append(self, event: AuditEvent) -> None:
        try:
            async with self._write_lock, aiosqlite.connect(self._database_url) as conn:
                await conn.execute(
                    """
                    INSERT INTO audit_events (
                        event_id, workflow_id, workflow_type, target_id,
                        event_type, activity_name, outcome, reason,
                        payload_json, timestamp, actor
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(event.event_id),
                        event.workflow_id,
                        event.workflow_type,
                        _serialize_target(event.target_id),
                        event.event_type.value,
                        event.activity_name,
                        event.outcome,
                        event.reason.value if event.reason else None,
                        json.dumps(event.payload, default=str),
                        event.timestamp.isoformat(),
                        event.actor,
                    ),
                )
                await conn.commit()
        except aiosqlite.IntegrityError as exc:
            raise AuditLogError(f"event_id {event.event_id} already persisted") from exc
        except aiosqlite.Error as exc:
            raise AuditLogError(f"append failed: {exc}") from exc

    async def query_by_workflow(self, workflow_id: str) -> list[AuditEvent]:
        return await self._query(
            "WHERE workflow_id = ? ORDER BY id ASC",
            (workflow_id,),
        )

    async def query_by_device(self, device_id: UUID) -> list[AuditEvent]:
        return await self._query(
            "WHERE target_id = ? ORDER BY id ASC",
            (str(device_id),),
        )

    async def query_by_time_range(
        self,
        start: datetime,
        end: datetime,
    ) -> list[AuditEvent]:
        return await self._query(
            "WHERE timestamp >= ? AND timestamp < ? ORDER BY id ASC",
            (start.isoformat(), end.isoformat()),
        )

    async def _query(self, where_clause: str, params: tuple) -> list[AuditEvent]:
        # where_clause is a hard-coded string from the public query methods, never user input.
        sql = _SELECT_SQL + where_clause
        try:
            async with aiosqlite.connect(self._database_url) as conn:
                conn.row_factory = aiosqlite.Row
                cursor = await conn.execute(sql, params)
                rows = await cursor.fetchall()
                return [_row_to_event(row) for row in rows]
        except aiosqlite.Error as exc:
            raise AuditLogError(f"query failed: {exc}") from exc


def _serialize_target(target_id) -> str | None:
    if target_id is None:
        return None
    return str(target_id)


def _row_to_event(row) -> AuditEvent:
    target_raw = row["target_id"]
    target: UUID | str | None
    if target_raw is None:
        target = None
    else:
        # Try to parse as UUID; fall back to plain string (use-case IDs).
        try:
            target = UUID(target_raw)
        except ValueError:
            target = target_raw

    return AuditEvent(
        event_id=UUID(row["event_id"]),
        workflow_id=row["workflow_id"],
        workflow_type=row["workflow_type"],
        target_id=target,
        event_type=AuditEventType(row["event_type"]),
        activity_name=row["activity_name"],
        outcome=row["outcome"],
        reason=WorkflowReason(row["reason"]) if row["reason"] else None,
        payload=json.loads(row["payload_json"]),
        timestamp=datetime.fromisoformat(row["timestamp"]),
        actor=row["actor"],
    )
