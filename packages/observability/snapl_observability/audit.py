"""In-memory append-only audit log.

Durable persistence is out of scope for this iteration and deferred to the
Orchestrator (Temporal already provides workflow audit). This implementation
satisfies FR-007 / FR-008 / FR-009 and SC-004 with minimal surface.
"""

from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from snapl_observability.models import AuditEntry


class AuditLog:
    """In-memory list[AuditEntry] guarded by a threading lock."""

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._lock = Lock()

    def append(self, entry: AuditEntry) -> None:
        with self._lock:
            self._entries.append(entry)

    def query_by_device(self, device_id: UUID) -> list[AuditEntry]:
        """Return entries for a device in chronological order. Returns a list copy."""
        with self._lock:
            matching = [e for e in self._entries if e.device_id == device_id]
        return sorted(matching, key=lambda e: e.timestamp)

    def all(self) -> list[AuditEntry]:
        """Return every entry in chronological order. Returns a list copy."""
        with self._lock:
            snapshot = list(self._entries)
        return sorted(snapshot, key=lambda e: e.timestamp)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


class BoundedAuditLog(AuditLog):
    """AuditLog with a fixed capacity — appends beyond ``maxlen`` evict the oldest.

    For long-running processes whose durable audit sink lives elsewhere (the
    Temporal worker writes the Orchestrator's AuditLog): an unbounded observer
    log there is a linear memory leak nothing ever reads (#67).
    """

    def __init__(self, *, maxlen: int = 1000) -> None:
        if maxlen <= 0:
            raise ValueError(f"maxlen must be positive, got {maxlen}")
        super().__init__()
        self._maxlen = maxlen

    def append(self, entry: AuditEntry) -> None:
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._maxlen:
                del self._entries[0]
