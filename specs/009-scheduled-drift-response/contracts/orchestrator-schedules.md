# Contract: Orchestrator — Drift Schedules and the DriftResponse Workflow

**Feature**: 009-scheduled-drift-response
**Date**: 2026-10-28
**Type**: Temporal workflow + activity + schedule-control functions (Python)

## Overview

This feature adds three things to `snapl-orchestrator`. Nothing in an existing workflow, activity, model field or block ABC changes.

1. **Workflow `DriftResponse`**, started by a drift schedule. It scans a use case through a child `ScanDrift` and applies the schedule's response.
2. **Activity `stand_down_schedule`**, which switches a schedule from `remediate` to `report` from inside a run (US4).
3. **Module `snapl_orchestrator.schedules`**, the plain async functions that create, inspect and steer drift schedules. The CLI calls them. They work without a worker (FR-022).

Plus one pure helper, `snapl_orchestrator.remediation.select_remediation`.

Results-versus-exceptions follows spec 005's design note. `DriftResponse` returns a `DriftResponseResult` for every network outcome, and raises only when its scan child fails outright (SoT unreachable), so the run shows as failed in the schedule's recent runs.

---

## Workflow: DriftResponse

```python
@workflow.defn(name="DriftResponse")
class DriftResponseWorkflow:
    """One scheduled run: scan a use case, then apply the schedule's response."""

    @workflow.run
    async def run(self, request: DriftResponseRequest) -> DriftResponseResult:
        ...
```

**Started by**: a drift schedule's `ScheduleActionStartWorkflow` (never by the CLI directly; `snapl schedule trigger` goes through the schedule). Tests start it directly.

**Workflow id**: set by the schedule as `drift-response-{use_case_id}`. Temporal appends the scheduled time per run.

**Sequence**:

1. Audit `WORKFLOW_STARTED` (`target_id=use_case_id`, `actor=f"schedule:{schedule_id}"`, payload `{"mode": ...}`).
2. `scan = await workflow.execute_child_workflow(ScanDriftWorkflow.run, use_case_id, id=f"{wf_id}-scan")`.
   - On a non-cancellation `ChildWorkflowError`, audit `WORKFLOW_TERMINATED` (outcome `failure`, reason `INTENT_UNAVAILABLE`), then re-raise. The schedule is not paused (`pause_on_failure=False`).
3. If `mode == REPORT`: audit `DRIFT_RESPONSE` (use-case level), audit `WORKFLOW_TERMINATED` (success), and return. **No other activity or child is started.**
4. If `mode == REMEDIATE`:
   1. `remediated, excluded = select_remediation(scan)` (pure, inline).
   2. Audit `DRIFT_RESPONSE` per excluded device (device level).
   3. If `remediated` is non-empty: `reconcile = await workflow.execute_child_workflow(ReconcileDevicesWorkflow.run, remediated, id=f"{wf_id}-reconcile")`. Audit `DRIFT_RESPONSE` per remediated device (device level, with `reconcile_workflow_id`).
   4. If `reconcile.failed > 0`: `await workflow.execute_activity(stand_down_schedule, args=[schedule_id, reason])` and set `stood_down=True`. The reason names this run's workflow id and the failed device ids.
   5. Audit `DRIFT_RESPONSE` (use-case level) and `WORKFLOW_TERMINATED` (success), then return.

**Returns**: `DriftResponseResult` (see data-model.md, invariants included).

**Cancellation**: As in `ScanDrift` and `ReconcileDevices`. Catch a cancelled child, audit `WORKFLOW_CANCELLED`, and re-raise. A cancelled run never calls `stand_down_schedule`.

**Determinism**: No clock, I/O or randomness outside `workflow.now()`, `workflow.uuid4()`, activities and children. `select_remediation` is pure. Imports go under `workflow.unsafe.imports_passed_through()`.

**Vendor neutrality**: Imports only `snapl_orchestrator.models`, `snapl_orchestrator.remediation`, `snapl_orchestrator.activities.audit`, `snapl_orchestrator.activities.schedule`, and the two child workflows. `test_vendor_seams.py` covers the new file automatically.

---

## Activity: stand_down_schedule

```python
@activity.defn(name="stand_down_schedule")
async def stand_down_schedule(schedule_id: str, reason: str) -> bool:
    """Switch a drift schedule's response to REPORT. Returns True if it changed it,
    False if it was already REPORT (idempotent on retry)."""
```

- Uses `temporalio.activity.client()`, the worker's client, which has the pydantic converter.
- `handle.update(updater)`. The updater rebuilds the action with `mode=REPORT` (same use case, task queue and id) and sets `state.note = reason`. It preserves `state.paused`, `spec` and `policy`.
- Appends `SCHEDULE_CHANGED` (`change="stood_down"`, `actor="snapl:stand-down"`) to the `AuditLog` from `get_activities()`, **only when it changed the mode**. A retried activity therefore writes one record.
- Retry: 5 attempts, 1s initial interval (as for audit: durability-critical). A schedule deleted mid-run (`RPCError` NOT_FOUND) is non-retryable and returns `False`. There is nothing left to stand down.

---

## Module: snapl_orchestrator.schedules

All functions are `async`, take a connected `temporalio.client.Client` built by `worker.client.build_client` (so `DriftResponseRequest` is encoded by the pydantic converter), and raise `ScheduleNotFoundError` (new, in `exceptions.py`, subclass of `OrchestratorError`) when the use case has no schedule. Every mutating function takes `audit_log: AuditLog` and `actor: str`, and appends exactly one `SCHEDULE_CHANGED` event after the Temporal call succeeds.

```python
DEFAULT_SCAN_INTERVAL = timedelta(minutes=5)
MIN_SCAN_INTERVAL = timedelta(minutes=1)

def schedule_id_for(use_case_id: str) -> str:
    """'drift-response-{use_case_id}'"""

def build_drift_schedule(
    *, use_case_id: str, interval: timedelta, task_queue: str,
    mode: DriftResponseMode = DriftResponseMode.REPORT,
) -> Schedule:
    """Pure. Interval spec; SKIP overlap; catchup_window=interval;
    pause_on_failure=False; action = DriftResponse(DriftResponseRequest(...)),
    id=schedule_id_for(use_case_id), static_summary='drift response: <mode>'.
    Raises ValueError below MIN_SCAN_INTERVAL."""

async def ensure_drift_schedule(
    client: Client, *, use_case_id: str, task_queue: str, audit_log: AuditLog, actor: str,
    interval: timedelta = DEFAULT_SCAN_INTERVAL,
) -> DriftScheduleView:
    """Create in REPORT; or, if it exists, change only the interval (and the
    catchup window). Response, paused and note are preserved (FR-004).
    Records 'created' or 'interval_changed'; records nothing if the interval
    is unchanged."""

async def set_drift_response(
    client: Client, *, use_case_id: str, mode: DriftResponseMode, audit_log: AuditLog, actor: str,
) -> DriftScheduleView:
    """Rebuild the action with the new mode; preserve spec, policy, state.
    Records 'response_changed' (from/to); no-op and no record if unchanged."""

async def pause_drift_schedule(
    client: Client, *, use_case_id: str, reason: str, audit_log: AuditLog, actor: str,
) -> None:
    """Suppress. reason must be non-empty. Records 'paused'."""

async def resume_drift_schedule(
    client: Client, *, use_case_id: str, audit_log: AuditLog, actor: str, note: str | None = None,
) -> None:
    """Records 'resumed'."""

async def trigger_drift_schedule(
    client: Client, *, use_case_id: str, audit_log: AuditLog, actor: str,
) -> DriftResponseMode:
    """One run now under the current response and the schedule's overlap
    policy (a run in progress makes it a no-op). Returns the mode it will run
    with, so the caller can confirm before calling for REMEDIATE.
    Records 'triggered'."""

async def delete_drift_schedule(
    client: Client, *, use_case_id: str, audit_log: AuditLog, actor: str,
) -> None:
    """Records 'deleted'. A run in progress is not cancelled."""

async def describe_drift_schedule(client: Client, *, use_case_id: str) -> DriftScheduleView:
    """Read-only. Decodes the action argument back to DriftResponseRequest via
    client.data_converter; fetches status for the last 5 runs."""

async def find_drift_schedule(client: Client, *, use_case_id: str) -> DriftScheduleView | None:
    """describe_drift_schedule, but None instead of ScheduleNotFoundError — for
    `snapl status`."""
```

**Not provided, on purpose**: no function creates a schedule in `REMEDIATE` (FR-003). `build_drift_schedule(mode=...)` exists for the updaters and for tests, and `ensure_drift_schedule` always passes `REPORT` when it creates.

**Confirmation is the caller's job.** These functions do not prompt. The CLI asks for confirmation before `set_drift_response(mode=REMEDIATE)` and before `trigger_drift_schedule` on a remediate schedule (spec 006 FR-012, this spec FR-012 and FR-025). `trigger` needs the mode first, so the CLI calls `describe_drift_schedule`, confirms, then triggers.

---

## Pure helper: snapl_orchestrator.remediation

```python
def select_remediation(scan: DriftScanResult) -> tuple[list[UUID], dict[UUID, ExclusionReason]]:
    """Forward-drift devices to reconcile, and drifted/errored devices excluded
    with their reason. Deterministic, scan order preserved (research R5)."""
```

---

## Worker registration

`worker/run.py` adds `DriftResponseWorkflow` to `workflows=[...]` and `stand_down_schedule` to `activities=[...]`. No new environment variable, no change to the `Activities` container, and no schedule is created at boot (research R7).
