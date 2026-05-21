"""In-memory AuditLog used for testing and ephemeral workers.

Durable persistence is provided by `SqliteAuditLog`. This implementation
satisfies the same ABC so workflow code is unaware of which one is wired up.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from snapl_orchestrator.audit.abc import AuditLog

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from snapl_orchestrator.models import AuditEvent


class InMemoryAuditLog(AuditLog):
    """asyncio.Lock-guarded list[AuditEvent]; chronological reads on query."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = asyncio.Lock()

    async def append(self, event: AuditEvent) -> None:
        async with self._lock:
            self._events.append(event)

    async def query_by_workflow(self, workflow_id: str) -> list[AuditEvent]:
        async with self._lock:
            matching = [e for e in self._events if e.workflow_id == workflow_id]
        return sorted(matching, key=lambda e: e.timestamp)

    async def query_by_device(self, device_id: UUID) -> list[AuditEvent]:
        async with self._lock:
            matching = [e for e in self._events if e.target_id == device_id]
        return sorted(matching, key=lambda e: e.timestamp)

    async def query_by_time_range(
        self,
        start: datetime,
        end: datetime,
    ) -> list[AuditEvent]:
        async with self._lock:
            matching = [e for e in self._events if start <= e.timestamp < end]
        return sorted(matching, key=lambda e: e.timestamp)

    def __len__(self) -> int:
        return len(self._events)
