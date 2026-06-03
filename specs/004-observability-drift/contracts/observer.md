# Contract: Observer

**Feature**: 004-observability-drift
**Date**: 2026-05-14
**Type**: Abstract Base Class (Python)

## Overview

The `Observer` ABC is the public interface of the NAF Observability building block. The Orchestrator, Presentation, and any operator-facing tooling interact with the Observability layer exclusively through this contract. The concrete `StructuralObserver` class implements this ABC for structural value-equality drift detection across the snapl-intent entity types.

## Design Note: Results vs Exceptions

Consistent with the `Collector` and `Executor` ABCs, `Observer` returns result objects for all drift outcomes — including the `ERROR` case where the upstream Collector failed. A failed CollectResult is reflected as `DriftReport(status=ERROR, error=...)`, never re-raised. This is required for:

- **Batch detection**: one device's failed collection must not abort drift analysis for the others.
- **Event emission**: the bus needs an event for every check, including errors, so consumers can detect liveness gaps.
- **Audit completeness**: every invocation produces an audit entry, including failure paths.

Python exceptions are still raised for: invalid constructor arguments, programming errors (empty batch, type-mismatched inputs), and registering a non-callable handler.

## Interface Definition

```python
from __future__ import annotations

from abc import ABC, abstractmethod

from snapl_collector.models import CollectResult
from snapl_intent.models import DesiredState

from snapl_observability.models import (
    AuditEntry,
    BatchDriftReport,
    DriftReport,
    ObservabilityEvent,
)


class Observer(ABC):
    """NAF Observability building block — drift, events, and audit interface."""

    # ── Drift Detection ─────────────────────────────────────────────────

    @abstractmethod
    async def detect_drift(
        self,
        desired: DesiredState,
        actual: CollectResult,
    ) -> DriftReport:
        """Compare desired state against live collected state.

        Produces a structured DriftReport listing every attribute where
        desired and actual values differ. A failed CollectResult is
        reflected as DriftReport(status=ERROR) — never raised.

        Args:
            desired: The intended configuration for the device, from snapl_intent.
            actual: The live collected state for the same device, from snapl_collector.

        Returns:
            DriftReport with status CLEAN, DRIFTED, or ERROR.

        Raises:
            ValueError: desired.device.id != actual.device_id (programming error
                — the caller paired the wrong inputs).
        """

    @abstractmethod
    async def detect_drift_batch(
        self,
        pairs: list[tuple[DesiredState, CollectResult]],
    ) -> BatchDriftReport:
        """Run drift detection across multiple devices.

        Each pair is processed independently; one device's outcome does not
        affect another's. Pairs with mismatched device IDs raise immediately
        before any analysis is attempted.

        Args:
            pairs: List of (DesiredState, CollectResult) pairs. Must be
                non-empty and every pair must reference matching device IDs.

        Returns:
            BatchDriftReport with per-device DriftReports and aggregate counts.

        Raises:
            ValueError: pairs is empty, or any pair has mismatched device IDs.
        """

    # ── Event Emission ──────────────────────────────────────────────────

    @abstractmethod
    async def emit_event(self, report: DriftReport) -> ObservabilityEvent:
        """Emit a structured event derived from a DriftReport.

        Constructs an ObservabilityEvent whose event_type is mapped 1:1 from
        the report's status (DRIFTED → drift_detected, CLEAN → state_clean,
        ERROR → drift_error). Dispatches the event to every registered
        EventBus handler and returns the event object so callers can attach
        it to logs or pass it on.

        Args:
            report: A DriftReport produced by detect_drift().

        Returns:
            The ObservabilityEvent that was dispatched.
        """

    # ── Audit Logging ───────────────────────────────────────────────────

    @abstractmethod
    async def log_audit(self, entry: AuditEntry) -> None:
        """Append an immutable AuditEntry to the audit log.

        Args:
            entry: A pre-constructed AuditEntry. The Observer does not
                modify or wrap it — append-only.
        """
```

## Concrete Implementation: StructuralObserver

```python
class StructuralObserver(Observer):
    """Structural value-equality Observer for snapl-intent entity types.

    Walks the intent models field-by-field and compares against the
    corresponding entries in CollectResult.data using the per-entity field
    map defined in structural/diff.py.

    Args:
        event_bus: Dispatcher for ObservabilityEvents. Defaults to a fresh
            EventBus with no handlers registered.
        audit_log: Storage for AuditEntries. Defaults to a fresh in-memory
            AuditLog.
        component_name: String written to AuditEntry.component for every
            internally-generated entry. Defaults to "StructuralObserver".
    """

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        audit_log: AuditLog | None = None,
        component_name: str = "StructuralObserver",
    ) -> None: ...
```

## Models

```python
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DriftStatus(str, Enum):
    CLEAN = "clean"
    DRIFTED = "drifted"
    ERROR = "error"


class EventType(str, Enum):
    DRIFT_DETECTED = "drift_detected"
    STATE_CLEAN = "state_clean"
    DRIFT_ERROR = "drift_error"


class AuditOperation(str, Enum):
    DETECT_DRIFT = "detect_drift"
    EMIT_EVENT = "emit_event"
    LOG_AUDIT = "log_audit"


class AuditOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"


class DriftItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    path: str
    desired: Any | None
    actual: Any | None
    entity_kind: str


class DriftReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    device_id: UUID
    device_name: str
    status: DriftStatus
    items: list[DriftItem]
    error: str | None = None
    timestamp: datetime


class BatchDriftReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    reports: dict[UUID, DriftReport]
    total: int
    clean: int
    drifted: int
    errored: int


class ObservabilityEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    event_type: EventType
    device_id: UUID
    device_name: str
    report: DriftReport
    timestamp: datetime


class AuditEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    operation: AuditOperation
    device_id: UUID | None = None
    component: str
    outcome: AuditOutcome
    detail: dict[str, Any] = {}
    timestamp: datetime
```

## Services

```python
from collections.abc import Callable
from threading import Lock


class EventBus:
    """In-process synchronous event dispatcher."""

    def __init__(self) -> None: ...

    def register(self, handler: Callable[[ObservabilityEvent], None]) -> None: ...

    def emit(self, event: ObservabilityEvent) -> None:
        """Invoke every registered handler. Per-handler exceptions are
        logged at WARNING and do not propagate."""

    @property
    def handlers(self) -> tuple[Callable[[ObservabilityEvent], None], ...]: ...


class AuditLog:
    """In-memory append-only audit store."""

    def __init__(self) -> None: ...

    def append(self, entry: AuditEntry) -> None: ...

    def query_by_device(self, device_id: UUID) -> list[AuditEntry]:
        """Return entries for a device in chronological order."""

    def all(self) -> list[AuditEntry]:
        """Return every entry in chronological order."""

    def __len__(self) -> int: ...
```

## Exceptions

```python
class ObserverError(Exception):
    """Base class for programming errors in the Observability module."""
```

Note: `ObserverError` and subclasses are raised only for programming errors (mismatched device IDs in detect_drift inputs, empty batch, registering a non-callable handler). Drift-detection failures and upstream Collector errors are returned as `DriftReport(status=ERROR)`.

## Consumer Notes

- **Orchestrator**: After a Collector activity returns, pair its `CollectResult` with the corresponding `DesiredState` from Intent and call `await observer.detect_drift(desired, actual)`. Use the returned `DriftReport.status` to branch the workflow (e.g., signal a remediation activity on `DRIFTED`). Register a handler that raises a Temporal signal when an `ObservabilityEvent` arrives.
- **Presentation**: Display `DriftReport.items` directly — each item already carries the path and both values. Query `AuditLog.query_by_device(device_id)` to render the operation history for a device.
- **Tests / Local development**: Construct a `StructuralObserver()` with no arguments — the defaults give you a fresh `EventBus` and `AuditLog`, both fully unit-testable in-process.

## Relationship to Other Block Contracts

The `Observer` ABC mirrors the `Collector` and `Executor` ABCs in:
- The result-over-exception pattern for outcomes the platform classifies (drift / clean / error).
- The `*_batch()` shape: a parallel-safe method returning a `Batch*Report` aggregate.
- The `async` method signatures, even where no I/O occurs (R6) — preserves a uniform call shape across NAF blocks.

The Observer differs from the Collector and Executor in:
- It performs no network I/O. Inputs are pre-fetched data structures.
- It owns long-lived state (`EventBus` handlers, `AuditLog` entries) — Collector and Executor are stateless.
- It has no vendor subpackage. The `structural/` subpackage isolates the diff *strategy*, not vendor specifics.
