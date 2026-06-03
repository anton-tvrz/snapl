"""AuditLog ABC — durable, append-only audit store for the NAF Orchestrator.

The Orchestrator owns this contract. The Observability block's in-memory
`AuditLog` defers persistence to this interface (see spec FR-015).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from snapl_orchestrator.models import AuditEvent


class AuditLog(ABC):
    """Durable, append-only audit store."""

    @abstractmethod
    async def append(self, event: AuditEvent) -> None:
        """Append an immutable AuditEvent.

        Raises:
            AuditLogError: persistence failed after all retries.
        """

    @abstractmethod
    async def query_by_workflow(self, workflow_id: str) -> list[AuditEvent]:
        """Return events for a workflow ID in chronological order.

        Returns an empty list if no events match.
        """

    @abstractmethod
    async def query_by_device(self, device_id: UUID) -> list[AuditEvent]:
        """Return events for a device across all workflows in chronological order."""

    @abstractmethod
    async def query_by_time_range(
        self,
        start: datetime,
        end: datetime,
    ) -> list[AuditEvent]:
        """Return events with ``start <= timestamp < end``, chronological order."""
