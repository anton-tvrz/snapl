# Contract: Orchestrator

**Feature**: 005-orchestrator-temporal
**Date**: 2026-05-21
**Type**: Temporal workflows + activities + `AuditLog` ABC (Python)

## Overview

The Orchestrator's public surface has three parts:

1. **Workflows** — `DeployIntent`, `ScanDrift`, `ReconcileDevices`. Started by callers (CLI, API, tests, upstream automation) via a Temporal client.
2. **Activities** — typed functions executed by the worker that wrap each downstream NAF block call. Activities are not called directly by external code; they are scheduled by the workflows.
3. **`AuditLog` ABC** — the durable, append-only audit store with one concrete implementation (`SqliteAuditLog`). Constructed at worker bootstrap; consumed by the `record_audit_event` activity.

The Orchestrator depends on the existing public ABCs of `snapl-intent`, `snapl-executor`, `snapl-collector`, and `snapl-observability` — those contracts are not modified by this feature.

## Design Note: Results vs Exceptions

Workflows return `WorkflowResult` / `DriftScanResult` / `ReconcileResult` for all device-and-network-related outcomes — same pattern as the Executor and Collector. A failed deploy is a `WorkflowResult(success=False, reason=...)`, not a raised exception.

Exceptions are reserved for:
- **Programming errors** (invalid arguments, missing dependencies) — raised from workflow start (caller-side) or from activity validation
- **Temporal infrastructure errors** (cannot reach Temporal cluster, namespace not found) — surfaced from the Temporal client, not wrapped

Cancellation is delivered via `temporalio.exceptions.CancelledError` inside the workflow; the workflow catches it, writes a `cancelled` audit event, and returns `WorkflowResult(success=False, reason=CANCELLED)`.

---

## Workflows

### DeployIntent

The closed-loop deploy workflow. Fetches intent for one device, applies via Executor, retrieves running state via Collector, verifies via Observer, records audit events at each step.

```python
@workflow.defn(name="DeployIntent")
class DeployIntentWorkflow:
    """End-to-end durable deploy: intent → apply → collect → verify → audit."""

    @workflow.run
    async def run(self, device_id: UUID) -> WorkflowResult:
        ...
```

**Workflow ID convention**: `deploy-intent-{device_id}`. Started with `id_conflict_policy=USE_EXISTING` so concurrent callers join the in-flight execution (FR-009).

**Inputs**:
- `device_id: UUID` — the device to deploy intent for.

**Returns**: `WorkflowResult` with reason in `{SUCCEEDED, INTENT_UNAVAILABLE, APPLY_FAILED, COLLECT_FAILED, VERIFICATION_FAILED, AUDIT_FAILED, CANCELLED}`.

**Retry policy** (per activity):
- `fetch_desired_state`: 3 attempts, 2s backoff, non-retryable `IntentStoreNotFound`
- `apply_config`: 3 attempts, 5s backoff, non-retryable `ExecutorConfigError`
- `collect_running_state`: 3 attempts, 2s backoff
- `detect_drift`: 1 attempt (pure compute)
- `record_audit_event`: 5 attempts, 1s backoff (durability-critical)

**Cancellation**: Cooperative. Activity cancellation is propagated via `ActivityCancellationType.WAIT_CANCELLATION_COMPLETED`. Final `WORKFLOW_CANCELLED` audit event is written before the workflow returns.

### ScanDrift

Fan-out, read-only drift evaluation across every device in a use case.

```python
@workflow.defn(name="ScanDrift")
class ScanDriftWorkflow:
    """Read-only drift evaluation across a use case."""

    @workflow.run
    async def run(self, use_case_id: str) -> DriftScanResult:
        ...
```

**Workflow ID convention**: `scan-drift-{use_case_id}-{uuid4}`. Parallel scans of the same use case are permitted (read-only).

**Inputs**:
- `use_case_id: str` — e.g., `"dcfabric"`.

**Returns**: `DriftScanResult` summarising per-device drift findings. The workflow's own success/failure is reflected in its terminal audit event — per-device errors are captured inside `DriftScanResult.reports`.

**Concurrency**: Per-device evaluation is fanned out via `asyncio.gather` of activity calls. Bounded by Temporal's task queue parallelism, not by application code.

### ReconcileDevices

Operator-initiated reconciliation: re-runs `DeployIntent` for an explicit list of device IDs.

```python
@workflow.defn(name="ReconcileDevices")
class ReconcileDevicesWorkflow:
    """Operator-initiated per-device reconciliation."""

    @workflow.run
    async def run(self, device_ids: list[UUID]) -> ReconcileResult:
        ...
```

**Workflow ID convention**: `reconcile-devices-{uuid4}`. Each call is a distinct workflow.

**Inputs**:
- `device_ids: list[UUID]` — devices to reconcile. Must be non-empty.

**Returns**: `ReconcileResult` aggregating per-device `WorkflowResult`.

**Execution**: Each target device is reconciled via `workflow.execute_child_workflow(DeployIntentWorkflow.run, device_id, id=f"deploy-intent-{device_id}", id_conflict_policy=USE_EXISTING)`. Children execute concurrently (subject to per-device `USE_EXISTING` serialization). A device missing from the SoT is recorded as `skipped` in the result.

**Raises (caller-side, on start)**:
- `ValueError`: `device_ids` is empty.

---

## Activities

Activities are async functions decorated with `@activity.defn`. Each wraps one downstream-block call and accepts/returns JSON-serializable types. The concrete downstream-block instances are injected via a module-level `Activities` container constructed in `worker/run.py`.

```python
@activity.defn(name="fetch_desired_state")
async def fetch_desired_state(device_id: UUID) -> DesiredState:
    """Fetch the desired state for a device from the Intent block."""
    return await activities.intent_store.get_desired_state_for_device(device_id)


@activity.defn(name="apply_config")
async def apply_config(desired: DesiredState) -> ApplyResult:
    """Apply the desired state to the device via the Executor."""
    return await activities.executor.apply(desired)


@activity.defn(name="collect_running_state")
async def collect_running_state(device: Device, paths: list[str]) -> CollectResult:
    """Retrieve current device state via the Collector."""
    if not paths:
        return await activities.collector.get_running_config(device)
    return await activities.collector.collect(device, paths)


@activity.defn(name="detect_drift")
async def detect_drift(desired: DesiredState, collected: CollectResult) -> DriftReport:
    """Compare desired vs collected via the Observer."""
    return await activities.observer.detect_drift(desired, collected)


@activity.defn(name="record_audit_event")
async def record_audit_event(event: AuditEvent) -> None:
    """Append an event to the durable AuditLog."""
    await activities.audit_log.append(event)
```

**Activity contract notes**:
- All activities are idempotent or naturally retry-safe:
  - `fetch_desired_state`, `collect_running_state`, `detect_drift`, `record_audit_event` are pure reads or appends.
  - `apply_config` relies on the Executor's existing apply semantics (gNMI SetRequest is naturally idempotent for desired-state convergence).
- Activity heartbeats are not used in this iteration — all activities are short (<60s). Longer-running activities would adopt `activity.heartbeat()` per Temporal best practice.

---

## AuditLog ABC

The durable, append-only audit store. Owned by the Orchestrator; takes over the durability obligation deferred by the Observability block's in-memory `AuditLog`.

```python
class AuditLog(ABC):
    """NAF Orchestrator durable audit store — append-only by contract."""

    @abstractmethod
    async def append(self, event: AuditEvent) -> None:
        """Append an immutable AuditEvent.

        Raises:
            AuditLogError: persistence failed after all retries.
        """

    @abstractmethod
    async def query_by_workflow(self, workflow_id: str) -> list[AuditEvent]:
        """Return events for a workflow ID in chronological order. Empty list if none."""

    @abstractmethod
    async def query_by_device(self, device_id: UUID) -> list[AuditEvent]:
        """Return events for a device across all workflows in chronological order."""

    @abstractmethod
    async def query_by_time_range(
        self,
        start: datetime,
        end: datetime,
    ) -> list[AuditEvent]:
        """Return events with `start <= timestamp < end`, chronological."""
```

### SqliteAuditLog

The concrete implementation backed by SQLite (via `aiosqlite`, WAL journal mode).

```python
class SqliteAuditLog(AuditLog):
    """File-backed, append-only AuditLog via SQLite.

    Args:
        database_url: SQLite database path or ":memory:" for in-process testing.
    """

    def __init__(self, *, database_url: str) -> None: ...

    async def initialize(self) -> None:
        """Apply the schema (CREATE TABLE IF NOT EXISTS); call once at boot."""
```

Schema (DDL in `packages/orchestrator/snapl_orchestrator/audit/schema.sql`):

```sql
CREATE TABLE IF NOT EXISTS audit_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      TEXT NOT NULL UNIQUE,
    workflow_id   TEXT NOT NULL,
    workflow_type TEXT NOT NULL,
    target_id     TEXT,
    event_type    TEXT NOT NULL,
    activity_name TEXT,
    outcome       TEXT,
    reason        TEXT,
    payload_json  TEXT NOT NULL,
    timestamp     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_workflow_id ON audit_events(workflow_id);
CREATE INDEX IF NOT EXISTS idx_audit_target_id   ON audit_events(target_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp   ON audit_events(timestamp);
```

**No UPDATE / DELETE methods are exposed.** FR-008 is enforced at the API surface.

---

## Models

```python
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from snapl_observability.models import DriftItem, DriftReport


class WorkflowReason(StrEnum):
    SUCCEEDED            = "succeeded"
    INTENT_UNAVAILABLE   = "intent_unavailable"
    APPLY_FAILED         = "apply_failed"
    COLLECT_FAILED       = "collect_failed"
    VERIFICATION_FAILED  = "verification_failed"
    AUDIT_FAILED         = "audit_failed"
    CANCELLED            = "cancelled"
    DEVICE_NOT_FOUND     = "device_not_found"


class AuditEventType(StrEnum):
    WORKFLOW_STARTED     = "workflow_started"
    ACTIVITY_STARTED     = "activity_started"
    ACTIVITY_COMPLETED   = "activity_completed"
    ACTIVITY_FAILED      = "activity_failed"
    WORKFLOW_TERMINATED  = "workflow_terminated"
    WORKFLOW_CANCELLED   = "workflow_cancelled"


class WorkflowResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str
    workflow_type: str
    target_id: UUID | str
    success: bool
    reason: WorkflowReason
    detail: str | None = None
    started_at: datetime
    ended_at: datetime
    drift_items: list[DriftItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_invariants(self) -> "WorkflowResult":
        if self.success and self.reason != WorkflowReason.SUCCEEDED:
            raise ValueError(f"success=True requires reason=SUCCEEDED, got {self.reason}")
        if not self.success and self.reason == WorkflowReason.SUCCEEDED:
            raise ValueError("success=False requires a non-SUCCEEDED reason")
        if self.drift_items and self.reason != WorkflowReason.VERIFICATION_FAILED:
            raise ValueError("drift_items populated only for VERIFICATION_FAILED")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must be >= started_at")
        return self


class DriftScanResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str
    use_case_id: str
    reports: dict[UUID, DriftReport]
    total: int
    clean: int
    drifted: int
    errored: int
    started_at: datetime
    ended_at: datetime

    @model_validator(mode="after")
    def _check_counts(self) -> "DriftScanResult":
        if self.clean + self.drifted + self.errored != self.total:
            raise ValueError("clean + drifted + errored must equal total")
        if len(self.reports) != self.total:
            raise ValueError("reports length must equal total")
        return self


class ReconcileResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str
    device_results: dict[UUID, WorkflowResult]
    total: int
    succeeded: int
    failed: int
    skipped: int = 0
    started_at: datetime
    ended_at: datetime

    @model_validator(mode="after")
    def _check_counts(self) -> "ReconcileResult":
        if self.succeeded + self.failed + self.skipped != self.total:
            raise ValueError("succeeded + failed + skipped must equal total")
        if len(self.device_results) + self.skipped != self.total:
            raise ValueError("device_results plus skipped must equal total")
        return self


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    workflow_id: str
    workflow_type: str
    target_id: UUID | str | None = None
    event_type: AuditEventType
    activity_name: str | None = None
    outcome: str | None = None
    reason: WorkflowReason | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime
    actor: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> "AuditEvent":
        if self.event_type in {
            AuditEventType.ACTIVITY_STARTED,
            AuditEventType.ACTIVITY_COMPLETED,
            AuditEventType.ACTIVITY_FAILED,
        } and self.activity_name is None:
            raise ValueError(f"{self.event_type.value} requires activity_name")
        if self.event_type == AuditEventType.WORKFLOW_TERMINATED and self.reason is None:
            raise ValueError("WORKFLOW_TERMINATED requires reason")
        if self.outcome is not None and self.outcome not in {"success", "failure", "cancelled"}:
            raise ValueError(f"outcome must be one of success/failure/cancelled, got {self.outcome}")
        return self
```

---

## Exceptions

```python
class OrchestratorError(Exception):
    """Base class for programming errors in the Orchestrator module."""

class OrchestratorConfigError(OrchestratorError):
    """Invalid Orchestrator configuration (missing Temporal endpoint, bad DB URL, etc.)."""

class AuditLogError(OrchestratorError):
    """Append or query failed against the durable audit store after retries."""
```

`OrchestratorError` and subclasses are raised only for programming errors (invalid arguments, broken configuration). Workflow-domain failures (a device unreachable, intent missing, verification mismatch) are returned via `WorkflowResult(success=False, reason=...)`, never raised.

---

## Worker Bootstrap

```python
# packages/orchestrator/snapl_orchestrator/worker/run.py
from temporalio.client import Client
from temporalio.worker import Worker

from snapl_orchestrator.activities import (
    apply_config,
    collect_running_state,
    detect_drift,
    fetch_desired_state,
    record_audit_event,
)
from snapl_orchestrator.workflows.deploy_intent import DeployIntentWorkflow
from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow
from snapl_orchestrator.workflows.reconcile_devices import ReconcileDevicesWorkflow


async def run_worker(
    *,
    temporal_host: str,
    namespace: str,
    task_queue: str,
    activities: Activities,  # constructed by caller with concrete IntentStore/Executor/Collector/Observer/AuditLog
) -> None:
    client = await Client.connect(temporal_host, namespace=namespace)
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[
            DeployIntentWorkflow,
            ScanDriftWorkflow,
            ReconcileDevicesWorkflow,
        ],
        activities=[
            fetch_desired_state,
            apply_config,
            collect_running_state,
            detect_drift,
            record_audit_event,
        ],
    )
    await worker.run()
```

The `invoke orchestrator.start` task in `tasks/` calls `run_worker()` with environment-configured parameters (`TEMPORAL_HOST`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TASK_QUEUE`, `SNAPL_AUDIT_DB`).

---

## Consumer Notes

- **Presentation (future)**: Builds a Temporal client, calls `client.start_workflow(DeployIntentWorkflow.run, device_id, id=f"deploy-intent-{device_id}", task_queue=..., id_conflict_policy=USE_EXISTING)`. To list running workflows, calls `client.list_workflows(query=...)` using Temporal's visibility API. To inspect history, queries `client.get_workflow_handle(workflow_id).fetch_history()`.
- **External automation**: Same surface as Presentation — start workflows via Temporal client, observe outcomes via `WorkflowResult` return values.
- **Observability**: Continues to expose its in-process `AuditLog` for in-process consumers. The durable Orchestrator-owned log is the canonical source for cross-workflow and post-restart queries.
- **Tests**: Use `temporalio.testing.WorkflowEnvironment.start_time_skipping()` to run workflows in-process with mocked activities. No live Temporal cluster required for unit tests.

---

## Relationship to Other Contracts

- **Consumes** the `IntentStore`, `Executor`, `Collector`, `Observer` ABCs unchanged (FR-014).
- **Does not implement** any of the four downstream ABCs.
- **Replaces** the durability obligation that the Observability `AuditLog` deferred (FR-015) — the Observability in-memory log continues to exist; the Orchestrator durable log is the new persistence boundary for cross-workflow queries.
- **Mirrors** the Executor's and Collector's "result-over-exception" pattern for device/network-domain outcomes.
