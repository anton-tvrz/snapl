"""Result models for the Executor building block (T007).

All result types are frozen dataclasses — immutable value objects.

Invariants:
  ApplyResult:   success=True  → error is None
                 success=False → error is set
  DryRunResult:  success=True  → payload is set, render_error is None
                 success=False → render_error is set, payload is None
  BatchResult:   succeeded + failed == total
  RemoveResult:  success=True  → error is None, refused is empty
                 success=False → error is set
                 dry_run=True  → device_response is None
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID  # noqa: TC003 — runtime import: pydantic/Temporal resolve across workflow boundary


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


@dataclass(frozen=True)
class RemoveResult:
    """Outcome of a dry_run_remove() or remove() call (spec 013).

    ``refused`` maps each path the guard turned down to its reason; when it is
    not empty nothing was sent. ``dry_run`` results never dialled, so they
    carry no device response.
    """

    device_id: UUID
    device_name: str
    success: bool
    paths: list[str]
    dry_run: bool
    refused: dict[str, str] = field(default_factory=dict)
    device_response: str | None = None
    error: str | None = None
    duration_ms: int = 0

    def __post_init__(self) -> None:
        if self.success and self.error is not None:
            raise ValueError("a successful RemoveResult has no error")
        if self.success and self.refused:
            raise ValueError("a successful RemoveResult has nothing refused")
        if not self.success and not self.error:
            raise ValueError("a failed RemoveResult needs an error")
        if self.dry_run and self.device_response is not None:
            raise ValueError("a dry run never dials, so it has no device_response")
