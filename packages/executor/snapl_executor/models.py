"""Result models for the Executor building block (T007).

All result types are frozen dataclasses — immutable value objects.

Invariants:
  ApplyResult:   success=True  → error is None
                 success=False → error is set
  DryRunResult:  success=True  → payload is set, render_error is None
                 success=False → render_error is set, payload is None
  BatchResult:   succeeded + failed == total
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of a single apply() or rollback() call."""

    device_id: UUID
    device_name: str
    success: bool
    payload: dict
    device_response: str | None = None
    error: str | None = None
    is_rollback: bool = False
    duration_ms: int = 0


@dataclass(frozen=True)
class DryRunResult:
    """Outcome of a dry_run() call. No gNMI connection is made."""

    device_id: UUID
    device_name: str
    success: bool
    payload: dict | None = None
    render_error: str | None = None


@dataclass(frozen=True)
class BatchResult:
    """Aggregated outcome of an apply_batch() call."""

    results: dict[UUID, ApplyResult] = field(default_factory=dict)
    total: int = 0
    succeeded: int = 0
    failed: int = 0
