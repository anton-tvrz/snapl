"""Result models for the NAF Collector building block."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True)
class CollectResult:
    """Outcome of a single collect() or get_running_config() call.

    Invariants:
    - success=True  → error is None
    - success=False → error is set; data is empty dict
    """

    device_id: UUID
    device_name: str
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    paths: list[str] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0
    timestamp: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True)
class BatchCollectResult:
    """Aggregated outcome of a collect_batch() call.

    Invariant: succeeded + failed == total
    """

    results: dict[UUID, CollectResult] = field(default_factory=dict)
    total: int = 0
    succeeded: int = 0
    failed: int = 0
