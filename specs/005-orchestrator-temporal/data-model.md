# Data Model: NAF Orchestrator — Temporal Workflows

**Feature**: 005-orchestrator-temporal
**Date**: 2026-05-21
**Source**: Feature spec + research

## Overview

The Orchestrator owns three categories of types:

1. **Workflow result types** — the terminal outputs returned to callers from each workflow.
2. **Audit types** — the durable, append-only event record used by the `AuditLog`.
3. **Internal command types** — typed payloads passed between workflow and activity layers (kept thin; most activity inputs are simple primitives or types re-exported from downstream blocks).

All public models are Pydantic v2, `frozen=True`, `extra="forbid"` (consistent with snapl-observability's models and the project constitution's contract-first principle).

Types that flow through Temporal activity boundaries must be JSON-serializable. Pydantic v2's `model_dump()` / `model_validate()` handle this; UUIDs and datetimes serialize as ISO strings via Pydantic's default JSON mode.

---

## Input Types (consumed from downstream blocks, unchanged)

These are not redefined here — the Orchestrator imports them as-is:

| Type | Source | Used in |
|------|--------|---------|
| `Device` | `snapl_intent.models` | `fetch_desired_state` output, all per-device workflows |
| `DesiredState` | `snapl_intent.models` | `fetch_desired_state` output → `apply_config` input |
| `ApplyResult` | `snapl_executor.models` | `apply_config` output |
| `CollectResult` | `snapl_collector.models` | `collect_running_state` output |
| `DriftReport` | `snapl_observability.models` | `detect_drift` output |
| `DriftStatus`, `DriftItem` | `snapl_observability.models` | nested inside `DriftReport` |

The Orchestrator does **not** redefine any device, state, apply, collect, or drift type. Doing so would couple it to internal shapes of the downstream blocks — exactly what the ABCs are designed to prevent.

---

## Workflow Result Types (owned by snapl_orchestrator)

### WorkflowReason (enum)

The terminal reason code for any `WorkflowResult`. Single source of truth for caller-side branching.

```python
class WorkflowReason(StrEnum):
    SUCCEEDED            = "succeeded"
    INTENT_UNAVAILABLE   = "intent_unavailable"     # FR-012
    APPLY_FAILED         = "apply_failed"
    COLLECT_FAILED       = "collect_failed"
    VERIFICATION_FAILED  = "verification_failed"    # FR-013
    AUDIT_FAILED         = "audit_failed"           # FR-006 durability breach
    CANCELLED            = "cancelled"              # FR-011
    DEVICE_NOT_FOUND     = "device_not_found"       # reconcile edge case
```

### WorkflowResult

The terminal outcome of a single workflow run. Returned by `DeployIntent`, embedded per device in `ReconcileResult`, and (in summary form) the carrier for `ScanDrift`.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| workflow_id | str | required | Temporal workflow ID (`deploy-intent-{device_id}` or per-call UUID) |
| workflow_type | str | required | Workflow type name (`DeployIntent`, `ScanDrift`, `ReconcileDevices`) |
| target_id | UUID \| str | required | Device UUID (for per-device) or use-case identifier (for fabric-wide) |
| success | bool | required | True iff `reason == SUCCEEDED` |
| reason | WorkflowReason | required | Terminal reason code |
| detail | str \| None | optional | Human-readable detail (e.g., underlying error message). None on success |
| started_at | datetime | required | UTC timestamp when the workflow started |
| ended_at | datetime | required | UTC timestamp when the workflow terminated |
| drift_items | list[DriftItem] | default=[] | Populated when `reason == VERIFICATION_FAILED` to identify what diverged |

**Invariants**:
- `success == True` ⇔ `reason == WorkflowReason.SUCCEEDED`
- `success == False` requires a non-None `detail` for every reason except `CANCELLED` (which is self-describing)
- `drift_items` non-empty ⇒ `reason == VERIFICATION_FAILED`
- `ended_at >= started_at`

### DriftScanResult

The outcome of a `ScanDrift` workflow. Per-device drift evaluation across a use case.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| workflow_id | str | required | Temporal workflow ID of the scan |
| use_case_id | str | required | Use case scanned (e.g., `dcfabric`) |
| reports | dict[UUID, DriftReport] | required | Per-device `DriftReport` keyed by device UUID |
| total | int | required | Total devices evaluated |
| clean | int | required | Devices with `status == CLEAN` |
| drifted | int | required | Devices with `status == DRIFTED` |
| errored | int | required | Devices with `status == ERROR` |
| started_at | datetime | required | UTC timestamp |
| ended_at | datetime | required | UTC timestamp |

**Invariants**:
- `clean + drifted + errored == total`
- `len(reports) == total`
- Each report's `device_id` matches its dict key

### ReconcileResult

The aggregated outcome of a `ReconcileDevices` workflow. A per-device `WorkflowResult` for each target device.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| workflow_id | str | required | Temporal workflow ID of the reconcile |
| device_results | dict[UUID, WorkflowResult] | required | Per-device deploy outcome |
| total | int | required | Devices attempted |
| succeeded | int | required | Devices whose deploy succeeded |
| failed | int | required | Devices whose deploy failed (any failure reason) |
| skipped | int | default=0 | Devices skipped (e.g., not found in SoT — edge case) |
| started_at | datetime | required | UTC timestamp |
| ended_at | datetime | required | UTC timestamp |

**Invariants**:
- `succeeded + failed + skipped == total`
- `len(device_results) + skipped == total` (skipped devices have no `WorkflowResult`)
- Each device result's `target_id` matches its dict key

---

## Audit Types (owned by snapl_orchestrator)

### AuditEventType (enum)

```python
class AuditEventType(StrEnum):
    WORKFLOW_STARTED     = "workflow_started"
    ACTIVITY_STARTED     = "activity_started"
    ACTIVITY_COMPLETED   = "activity_completed"
    ACTIVITY_FAILED      = "activity_failed"
    WORKFLOW_TERMINATED  = "workflow_terminated"
    WORKFLOW_CANCELLED   = "workflow_cancelled"
```

### AuditEvent

A single durable, append-only record of something the platform did. Persisted by the `AuditLog`. Designed to be uniformly addressable for the three query patterns required by FR-007 (by workflow ID, by device ID, by time range).

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| event_id | UUID | required, unique | Stable identifier for this event |
| workflow_id | str | required | Owning workflow's Temporal ID |
| workflow_type | str | required | `DeployIntent` / `ScanDrift` / `ReconcileDevices` |
| target_id | UUID \| str \| None | optional | Device UUID, use-case ID, or None for cross-cutting events |
| event_type | AuditEventType | required | What happened |
| activity_name | str \| None | optional | Required iff `event_type` is one of `ACTIVITY_STARTED / ACTIVITY_COMPLETED / ACTIVITY_FAILED` |
| outcome | str \| None | optional | `"success"`, `"failure"`, `"cancelled"`, or None for `*_STARTED` events |
| reason | WorkflowReason \| None | optional | Terminal reason; required iff `event_type == WORKFLOW_TERMINATED` |
| payload | dict[str, Any] | default={} | Event-specific structured detail (e.g., activity inputs/outputs summary) |
| timestamp | datetime | required | UTC timestamp |
| actor | str \| None | optional | Caller identity if known (e.g., CLI username); deferred to Presentation block but field reserved now |

**Invariants**:
- `event_type ∈ {ACTIVITY_STARTED, ACTIVITY_COMPLETED, ACTIVITY_FAILED}` ⇒ `activity_name is not None`
- `event_type == WORKFLOW_TERMINATED` ⇒ `reason is not None`
- `outcome` is one of `{"success", "failure", "cancelled", None}`
- `payload` must be JSON-serializable (validated by Pydantic v2)

**Append-only contract**: No `update` or `delete` operation is exposed on `AuditEvent` — once persisted, the record is permanent (FR-008). Programmatic mutation attempts must raise.

---

## Internal Types

### ApplyOutcome (slim re-projection for workflow → workflow communication)

The verification step (R6) needs the list of YANG paths that the apply activity wrote. The Executor's `ApplyResult` already carries that information; the Orchestrator does not introduce a new shape — it passes the `ApplyResult` through to the verification step. Listed here for clarity, no new type added.

### Activity dependency container

```python
@dataclass
class Activities:
    intent_store: IntentStore
    executor: Executor
    collector: Collector
    observer: Observer
    audit_log: AuditLog
```

Constructed once in `worker/run.py`, passed to the Temporal worker via the `activities=` parameter of `Worker(...)`. Activity functions resolve their dependencies from this container so the workflow layer never imports concrete implementations.

---

## Entity Relationships

```
   ┌──────────────────────────────────────────────────────────────┐
   │                       Temporal Workflows                       │
   │                                                                │
   │  DeployIntent     ScanDrift       ReconcileDevices            │
   │       │              │                  │                      │
   │       │              │                  │   spawns child       │
   │       │              │                  ▼                      │
   │       │              │           DeployIntent (per device)     │
   │       │              │                  │                      │
   └───────┼──────────────┼──────────────────┼──────────────────────┘
           │              │                  │
           ▼              ▼                  ▼
   ┌──────────────────────────────────────────────────────────────┐
   │                       Activities                              │
   │                                                                │
   │  fetch_desired_state  → IntentStore                            │
   │  apply_config         → Executor                               │
   │  collect_running_state→ Collector                              │
   │  detect_drift         → Observer                               │
   │  record_audit_event   → AuditLog (SqliteAuditLog)              │
   └────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                       ┌─────────────────────┐
                       │ SQLite audit_events │
                       │   (durable, WAL)    │
                       └─────────────────────┘
```

**DeployIntent flow** (single device):
```
caller ── start_workflow ──► DeployIntent[device_id]
                                  │
                                  ▼
                          record_audit_event(WORKFLOW_STARTED)
                                  │
                                  ▼
                          fetch_desired_state(device_id)
                                  │
                                  ▼ (record activity completed)
                          apply_config(desired)
                                  │
                                  ▼ (record activity completed)
                          collect_running_state(device, paths=apply.paths)
                                  │
                                  ▼ (record activity completed)
                          detect_drift(desired, collected)
                                  │
                                  ▼
                  [drift.status]   CLEAN    DRIFTED    ERROR
                                   │         │          │
                                   ▼         ▼          ▼
                          SUCCEEDED   VERIFICATION_FAILED   APPLY_FAILED/COLLECT_FAILED
                                   │
                                   ▼
                          record_audit_event(WORKFLOW_TERMINATED)
                                   │
                                   ▼
                          return WorkflowResult
```

**ScanDrift flow** (read-only fan-out):
```
caller ── start_workflow ──► ScanDrift[use_case_id]
                                  │
                          fetch all devices in use case (intent activity)
                                  │
                          fan-out per device (asyncio.gather of activities):
                              fetch_desired_state → collect_running_state → detect_drift
                                  │
                          aggregate DriftReport[] into DriftScanResult
                                  │
                          record_audit_event(WORKFLOW_TERMINATED)
                                  │
                          return DriftScanResult
```

**ReconcileDevices flow** (composition of DeployIntent):
```
caller ── start_workflow ──► ReconcileDevices[uuid]
                                  │
                          for each device_id in target list:
                              execute_child_workflow(DeployIntent[device_id])
                                  │
                          aggregate per-device WorkflowResult into ReconcileResult
                                  │
                          record_audit_event(WORKFLOW_TERMINATED)
                                  │
                          return ReconcileResult
```

---

## State Transitions

Workflows are state machines whose transitions correspond to activity boundaries. Allowed terminal states per workflow:

### DeployIntent

| State | Trigger | Next |
|-------|---------|------|
| `started` | workflow.run entry | `fetching_intent` |
| `fetching_intent` | activity scheduled | `applying` (success) / terminal `INTENT_UNAVAILABLE` (failure after retries) |
| `applying` | apply activity scheduled | `collecting` (success) / terminal `APPLY_FAILED` (failure after retries) |
| `collecting` | collect activity scheduled | `verifying` (success) / terminal `COLLECT_FAILED` (failure after retries) |
| `verifying` | detect_drift activity scheduled | terminal `SUCCEEDED` (CLEAN) / terminal `VERIFICATION_FAILED` (DRIFTED) / terminal `COLLECT_FAILED` (ERROR) |
| any in-flight state | cancellation signal | terminal `CANCELLED` |
| terminal | — | (immutable; recorded in audit log) |

### ScanDrift

| State | Trigger | Next |
|-------|---------|------|
| `started` | workflow.run entry | `enumerating` |
| `enumerating` | fetch device list | `scanning` |
| `scanning` | per-device fan-out | terminal `SUCCEEDED` (always — scan always completes, individual device errors are recorded inside the `DriftScanResult`) |

### ReconcileDevices

| State | Trigger | Next |
|-------|---------|------|
| `started` | workflow.run entry | `dispatching` |
| `dispatching` | per-device child workflow scheduling | `awaiting` |
| `awaiting` | all child workflows terminate | terminal `SUCCEEDED` (the reconcile workflow always completes; per-device failures are captured in `ReconcileResult.device_results`) |
| any in-flight state | cancellation | terminal `CANCELLED` — pending children are cancelled cooperatively |

---

## Data Format Contract

### Workflow ↔ Caller

Callers receive `WorkflowResult`, `DriftScanResult`, or `ReconcileResult` as the workflow's return value. All three are Pydantic models — callers can `model_dump_json()` for serialisation or access typed fields directly.

### Workflow ↔ Activity

Activity inputs and outputs are JSON-serializable via Pydantic. Where the downstream block's model is already Pydantic v2 (`DesiredState`, `DriftReport`), it passes through unchanged. The Collector's dataclass-based `CollectResult` is converted to a dict at the activity boundary (the activity function's signature accepts/returns dict-or-pydantic; conversion happens at the edge).

### Activity ↔ AuditLog

The activity receives an `AuditEvent` Pydantic model and calls `audit_log.append(event)`. The `SqliteAuditLog` serialises `payload` via `model.payload` (already a dict) and stores it as a JSON string in the `payload_json` column.

---

## Database Schema (SQLite)

The `audit_events` table (DDL in `audit/schema.sql`) is the only persistence the Orchestrator owns. Schema repeated here for cross-reference; authoritative source is the DDL file.

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

Queries supported (FR-007):
- `SELECT … WHERE workflow_id = ? ORDER BY id ASC` — events for a workflow, chronological
- `SELECT … WHERE target_id = ? ORDER BY id ASC` — events for a device, chronological
- `SELECT … WHERE timestamp BETWEEN ? AND ? ORDER BY id ASC` — events within a time range
