# Data Model: Scheduled Drift Scans and Opt-In Remediation

**Feature**: 009-scheduled-drift-response
**Date**: 2026-10-28

Every new model lives in `snapl_orchestrator/models.py` and follows that module's conventions: pydantic v2, `frozen=True`, `extra="forbid"`, invariants in a `model_validator(mode="after")`, and runtime type imports kept out of `TYPE_CHECKING` (`# noqa: TC003`, research R12). No model names a vendor. No model in another block changes.

## Enums

### DriftResponseMode

What a scheduled run does with what its scan found. (Principle VIII.2's third response, `suppress`, is a paused schedule. Nothing runs, so nothing carries a mode.)

| member | value | meaning |
| --- | --- | --- |
| `REPORT` | `"report"` | record findings, write nothing. **Default.** |
| `REMEDIATE` | `"remediate"` | after the scan, reconcile eligible devices |

### ExclusionReason

Why a drifted device was left alone by a `remediate` run.

| member | value | rule |
| --- | --- | --- |
| `UNDESIRED_ONLY` | `"undesired_only"` | `status == DRIFTED` and every `DriftItem.undesired is True`. Only a removal fixes it, and removal is never automatic (VIII.5). |
| `ERRORED` | `"errored"` | `status == ERROR`. The scan could not see the device. |

### ScheduleChange

The kind of change a `SCHEDULE_CHANGED` audit event records. It is stored as the `change` key of `AuditEvent.payload`.

`created`, `interval_changed`, `response_changed`, `paused`, `resumed`, `triggered`, `deleted`, `stood_down`

### AuditEventType (extended)

Two members are added to the existing enum. Existing values are unchanged, and SQLite stores the value as text, so no schema migration is needed.

| member | value | written by |
| --- | --- | --- |
| `SCHEDULE_CHANGED` | `"schedule_changed"` | `schedules.py` functions (operator) and `stand_down_schedule` (system) |
| `DRIFT_RESPONSE` | `"drift_response"` | `DriftResponseWorkflow`: one event per run at use-case level, plus one per repaired or excluded device at device level |

`AuditEvent._check_invariants` gains one rule: `SCHEDULE_CHANGED` requires `payload["change"]` to be a `ScheduleChange` value, and requires `actor` to be set. This is what makes FR-021 ("who") enforceable at the model.

## DriftResponseRequest

The single argument a drift schedule passes to every run it starts. It is stored in the schedule's action, so it is visible in the Web UI and decoded back by `describe_drift_schedule`.

| field | type | notes |
| --- | --- | --- |
| `use_case_id` | `str` | non-empty |
| `mode` | `DriftResponseMode` | default `REPORT` |
| `schedule_id` | `str` | `drift-response-{use_case_id}`. Used for attribution and for stand-down. |

**Invariant**: `schedule_id == schedule_id_for(use_case_id)`. A request whose schedule and use case disagree is rejected, so a run cannot stand down a different use case's schedule.

## DriftResponseResult

The terminal outcome of one scheduled run.

| field | type | notes |
| --- | --- | --- |
| `workflow_id` | `str` | the scheduled run's id (`drift-response-{use_case}-<scheduled time>`) |
| `schedule_id` | `str` | |
| `use_case_id` | `str` | |
| `mode` | `DriftResponseMode` | the mode the run **started** with (FR-016) |
| `scan` | `DriftScanResult` | the child scan's result, unchanged |
| `remediated` | `list[UUID]` | devices passed to reconcile, in scan order. Empty in `REPORT`. |
| `excluded` | `dict[UUID, ExclusionReason]` | drifted or errored devices deliberately not passed to reconcile. Empty in `REPORT`. |
| `reconcile` | `ReconcileResult \| None` | the child reconcile's result. `None` when nothing was eligible, and always in `REPORT`. |
| `stood_down` | `bool` | default `False`. `True` when this run switched its schedule to `REPORT` (US4). |
| `started_at` | `datetime` | |
| `ended_at` | `datetime` | `>= started_at` |

**Invariants**:
- `mode == REPORT` ⇒ `remediated == []`, `excluded == {}`, `reconcile is None`, `stood_down is False`. This makes FR-011 a property of the result type.
- `reconcile is None` ⇔ `remediated == []`.
- `reconcile is not None` ⇒ `reconcile.total == len(remediated)`.
- `remediated` and `excluded` are disjoint, and every id in either is a key of `scan.reports`.
- Every id in `remediated` has `scan.reports[id].status == DRIFTED` and at least one item with `undesired is False` (R5).
- `stood_down` ⇒ `reconcile is not None and reconcile.failed > 0`.

## Eligibility (pure function, no model)

`select_remediation(scan: DriftScanResult) -> tuple[list[UUID], dict[UUID, ExclusionReason]]` lives in `snapl_orchestrator/remediation.py`. It is pure and deterministic, so the workflow can call it inline without an activity (it is ordinary computation over its input).

```
for device_id, report in scan.reports.items():       # dict order = scan order
    CLEAN    -> neither list
    ERROR    -> excluded[device_id] = ERRORED
    DRIFTED  -> remediated if any(not i.undesired for i in report.items)
                else excluded[device_id] = UNDESIRED_ONLY
```

## DriftScheduleView

A read model the CLI renders. `describe_drift_schedule` projects Temporal's `ScheduleDescription` into it, so Presentation does not depend on `temporalio` client types. This is the same pattern as `worker.client.RunningWorkflowInfo`. It is frozen like the other models, but it is a value object for display, not persisted.

| field | type | source |
| --- | --- | --- |
| `schedule_id` | `str` | `ScheduleDescription.id` |
| `use_case_id` | `str` | decoded `DriftResponseRequest.use_case_id` |
| `interval` | `timedelta` | `schedule.spec.intervals[0].every` |
| `mode` | `DriftResponseMode` | decoded `DriftResponseRequest.mode` (action args via `client.data_converter`, R3) |
| `paused` | `bool` | `schedule.state.paused` |
| `note` | `str \| None` | `schedule.state.note`: the pause reason, or the stand-down reason |
| `next_runs` | `list[datetime]` | `info.next_action_times[:3]` |
| `recent_runs` | `list[ScheduledRunInfo]` | `info.recent_actions[-5:]`, newest last |
| `running` | `list[str]` | workflow ids from `info.running_actions` |
| `skipped_overlap` | `int` | `info.num_actions_skipped_overlap` |

### ScheduledRunInfo

| field | type | source |
| --- | --- | --- |
| `workflow_id` | `str` | `ScheduleActionResult.action.workflow_id` |
| `scheduled_at` | `datetime` | `ScheduleActionResult.scheduled_at` |
| `started_at` | `datetime` | `ScheduleActionResult.started_at` |
| `status` | `str` | `client.get_workflow_handle(workflow_id).describe().status.name.lower()` (`running`, `completed`, `failed`, …). In SDK 1.25.0, `ScheduleActionResult` carries no status, hence one describe call per shown run (at most 5). |

## Audit payloads

Every new audit event uses the existing `AuditEvent` model. These are the payload shapes.

**`SCHEDULE_CHANGED`**: `workflow_id = schedule_id`, `workflow_type = "DriftSchedule"`, `target_id = use_case_id`, `actor` required.

| `change` | other payload keys |
| --- | --- |
| `created` | `interval_seconds`, `mode` (always `"report"`) |
| `interval_changed` | `from_seconds`, `to_seconds` |
| `response_changed` | `from`, `to` |
| `paused` | `reason` |
| `resumed` | `note` (optional) |
| `triggered` | `mode` |
| `deleted` | none |
| `stood_down` | `from` (`"remediate"`), `to` (`"report"`), `run_workflow_id`, `failed_devices` (list of names) |

Using the schedule id as `workflow_id` means `snapl audit --workflow drift-response-dcfabric` returns the schedule's whole policy history, with no new query.

**`DRIFT_RESPONSE`** (written by `DriftResponseWorkflow`, `workflow_type = "DriftResponse"`, `actor = f"schedule:{schedule_id}"`):

- Use-case level, one per run, `target_id = use_case_id`: `mode`, `scan_workflow_id`, `clean`, `drifted`, `errored`, `remediated` (count), `excluded` (count by reason), `reconcile_workflow_id` (or `None`), `stood_down`.
- Device level, one per repaired or excluded device, `target_id = device_id`: `decision` (`remediated` or one of the `ExclusionReason` values), `scan_workflow_id`, `reconcile_workflow_id` (when remediated), `paths` (the drifted paths). This puts "repaired by schedule X from scan Y", or "left alone because undesired-only", into `snapl audit --device <name>` (FR-020).

The existing lifecycle events (`WORKFLOW_STARTED`, `WORKFLOW_TERMINATED`, `WORKFLOW_CANCELLED`) are written by `DriftResponse` exactly as by the other workflows. The child scan, reconcile and per-device deploys keep writing their own events unchanged.

## Lifecycle of a drift schedule

```
                    snapl schedule create
   (none) ───────────────────────────────────────▶ active / report
                                                   │   ▲
              snapl schedule mode remediate --yes  │   │ snapl schedule mode report
                                                   ▼   │ stand-down (failed repair)
                                                active / remediate

   active / <mode> ──snapl schedule pause --reason──▶ paused / <mode>    (suppress: no runs)
   paused / <mode> ──snapl schedule resume──────────▶ active / <mode>

   any ──snapl schedule delete──▶ (none)        create on an existing schedule: interval only, state kept
```

A response change while paused is allowed and takes effect on resume. A stand-down never changes `paused`.
