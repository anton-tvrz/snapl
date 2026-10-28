# Contract: Orchestrator — Drift Schedules and the DriftResponse Workflow

**Feature**: 009-scheduled-drift-response
**Date**: 2026-10-28
**Type**: Temporal workflow + activity + schedule-control functions (Python)

## Overview

This feature adds two things to `snapl-orchestrator`. Nothing in an existing workflow, activity, model field or block ABC changes.

1. **Workflow `DriftResponse`**, started by a drift schedule. It scans a use case through a child `ScanDrift` and applies the schedule's response.
2. **Module `snapl_orchestrator.schedules`**, the plain async functions that create, inspect and steer drift schedules. The CLI calls them. They work without a worker (FR-022).

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
   4. Audit `DRIFT_RESPONSE` (use-case level) and `WORKFLOW_TERMINATED` (success), then return.

**Returns**: `DriftResponseResult` (see data-model.md, invariants included).

**Cancellation**: As in `ScanDrift` and `ReconcileDevices`. Catch a cancelled child, audit `WORKFLOW_CANCELLED`, and re-raise.

**Determinism**: No clock, I/O or randomness outside `workflow.now()`, `workflow.uuid4()`, activities and children. `select_remediation` is pure. Imports go under `workflow.unsafe.imports_passed_through()`.

**Vendor neutrality**: Imports only `snapl_orchestrator.models`, `snapl_orchestrator.remediation`, `snapl_orchestrator.activities.audit`, and the two child workflows. `test_vendor_seams.py` covers the new file automatically.

---

## Deferred: stand_down_schedule

The stand-down activity (former US4, FR-018) is deferred to a follow-up issue. Research R8 records its design. In this cut, `DriftResponse` returns a failed reconcile in its result and audit trail, and the schedule stays in `remediate`.

---

## Module: snapl_orchestrator.schedules

All functions are `async`, take a connected `temporalio.client.Client` built by `worker.client.build_client` (so `DriftResponseRequest` is encoded by the pydantic converter), and raise `ScheduleNotFoundError` (new, in `exceptions.py`, subclass of `OrchestratorError`) when the use case has no schedule. Every mutating function takes `audit_log: AuditLog` and `actor: str`, and appends exactly one `SCHEDULE_CHANGED` event after the Temporal call succeeds.

```python
DEFAULT_SCAN_INTERVAL = timedelta(minutes=5)
MIN_SCAN_INTERVAL = timedelta(minutes=1)

from snapl_orchestrator.models import drift_schedule_id as schedule_id_for
    # 'drift-response-{use_case_id}'; defined in models.py so DriftResponseRequest
    # can validate against it without importing this module (no cycle, no
    # temporalio.client in the sandbox-passthrough models module).

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
    is unchanged. Called by `snapl schedule create` and by `invoke demo.up`.
    The SoT identity check (FR-031) is the caller's job, before this call:
    this module does not depend on the intent block's Infrahub client."""

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
    client.data_converter. For the last 5 runs, fetches status with one
    describe each; for completed runs, also fetches the DriftResponseResult
    (handle.result(), which returns at once for a closed workflow) to build
    the summary and `wrote` flag (FR-024). A run whose result cannot be
    fetched shows its status without a summary; describe never fails on it."""

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

`worker/run.py` adds `DriftResponseWorkflow` to `workflows=[...]`. No new activity, no new environment variable, no change to the `Activities` container, and no schedule is created at boot (research R7).
