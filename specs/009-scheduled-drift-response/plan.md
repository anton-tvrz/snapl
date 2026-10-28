# Implementation Plan: Scheduled Drift Scans and Opt-In Remediation

**Branch**: `009-scheduled-drift-response` | **Date**: 2026-10-28 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/009-scheduled-drift-response/spec.md`

## Summary

Close the NAF loop without a human in the arrow back to the Executor, while keeping Principle VIII.2's rule that auto-remediation is never the silent default.

The design:

- Each use case gets at most one **Temporal Schedule** (`drift-response-{use_case}`, 5-minute default interval, overlap `SKIP`, catch-up window of one interval).
- The schedule starts a new **`DriftResponse`** workflow. It runs the existing `ScanDrift` as a child, then applies the response carried in its own arguments:
  - **`report`**: record and stop. Every schedule is born in this mode.
  - **`remediate`**: run the existing `ReconcileDevices` as a child for the devices with drift a deploy can fix. Undesired-only and errored devices are excluded.
- A failed repair is attempted again on the next run. Automatic stand-down to `report` is deferred to a follow-up (research R8).
- Suppress is a pause with a required reason.
- Operators steer schedules through a new `snapl schedule` command group, a thin client over a new `snapl_orchestrator.schedules` module. Every change lands in the durable audit log with its actor.
- Nothing in `DeployIntent`, `ScanDrift`, `ReconcileDevices`, any block ABC, or the existing CLI commands changes. No schedule ever removes config (VIII.5, #65).

Research decisions are in [research.md](research.md), types in [data-model.md](data-model.md), interfaces in [contracts/](contracts/), and usage and live-evidence steps in [quickstart.md](quickstart.md).

**Scope decisions**, settled with the maintainer on 2026-10-28 (research R10):

1. Remediate opt-in is per use case.
2. Suppress is pause-only, with no calendar windows.
3. Schedules are created by `snapl schedule create` or `invoke demo.up`, never by the worker. `demo.check` fails on a missing or paused schedule.
4. Stand-down (the former US4) is deferred.
5. Carried over from the 008 draft (#129): the SoT identity refusal on create (FR-031), a one-line summary per recent run (FR-024), and the 30-second trigger target (SC-012).

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**:
- temporalio, locked at 1.25.0. It provides `Client.create_schedule`, `ScheduleHandle`, `SchedulePolicy`, and `ScheduleOverlapPolicy`. The floor in `packages/orchestrator/pyproject.toml` rises from `>=1.7` to the locked version.
- pydantic>=2.5, typer and rich (presentation, already declared).
- No new third-party dependency.

**Storage**:
- Schedules are cluster state in Temporal. They persist on the `temporal_data` volume (#112).
- Audit events go to the existing SQLite `AuditLog`. Two new `AuditEventType` values are stored as text, so there is no DDL change.

**Testing**: pytest with markers `unit` and `integration`.
- `WorkflowEnvironment.start_time_skipping()` for `DriftResponse`. It has no schedule support.
- The existing `tests/unit/test_tasks/test_demo.py` pattern for the `demo.up` and `demo.check` changes.
- A fake `Client`/`ScheduleHandle` for `schedules.py`.
- `WorkflowEnvironment.start_local()` for one integration round trip (research R11).

**Target Platform**: macOS with OrbStack/Docker. The dev Temporal runs in `development/docker-compose.yml` on `localhost:18033`, with the Web UI on `:18034`.
**Project Type**: uv-workspace library packages (orchestrator, presentation) plus the existing worker entry point and CLI.
**Performance Goals**:
- A report-mode run on the six-device fabric adds no measurable time over `ScanDrift` itself.
- The time from drift to its audit record is ≤ interval + scan duration (SC-001).
- Drift is healed within 2 intervals (SC-003).

**Constraints**:
- Deterministic workflow code.
- Schedule control works with the worker down (FR-022).
- No vendor names in workflows or models (Principle VI, enforced by `test_vendor_seams.py`).
- Existing workflow signatures are frozen (FR-028).

**Scale/Scope**: One schedule per use case. Only `dcfabric` (6 devices) is exercised.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. NAF Alignment | PASS | Scheduling and response are coordination, which the Orchestrator owns. The CLI surface is Presentation. Dependency direction is unchanged: `presentation → orchestrator → {…}`. Nothing new is placed outside its owning block, and no ADR deviation is added. |
| II. Spec-Driven Development | PASS | This feature adds a device write nobody types, which is the "new kind of write" trigger. It is specified before implementation. |
| III. TDD | PASS | Every new source file has its test file listed first (Project Structure). The live-evidence obligation is spelled out in quickstart.md, because the feature starts device writes and consumes the drift diff's `undesired` flag. |
| IV. Modularity | PASS | No new package. The new modules (`schedules.py`, `remediation.py`, one workflow) are unit-testable in isolation. |
| V. Contract-First | PASS | `contracts/orchestrator-schedules.md` and `contracts/cli-schedule.md` are defined before code. No block ABC changes. `DriftResponse` composes existing workflows only. |
| VI. Single Vendor, Named Seams | PASS | `DriftResponse` imports only orchestrator models, activities and workflows. `test_vendor_seams.py` picks up the new workflow file automatically. No template, adapter or field-map change. |
| VII. Simplicity | PASS | Temporal-native Schedules are used instead of a hand-rolled loop, along with native pause, trigger and overlap. There is one new workflow, no new activity, no new environment variable, and no new store. Stand-down was deferred to keep it that way. |
| VIII.1 Authority split | PASS | The response policy is operational policy on the schedule, not intent in the SoT. Nothing promotes device state into intent. |
| VIII.2 Drift response | PASS | `report` is the default and is the only mode a schedule can be created in (FR-003). `remediate` takes an explicit, confirmed, audited command (FR-012). `suppress` is a paused schedule with a required reason (FR-017). Every run records its response (FR-010). |
| VIII.4 Executor idempotency | PASS | Repairs go through `ReconcileDevices` → `DeployIntent`, which are idempotent merge applies. Repeated runs against a converged device write the same state. |
| VIII.5 Undesired config | PASS | Undesired-only devices are excluded from repair (R5, FR-013). No code path removes config (FR-015). Removal (#65) stays out of scope, and out of any schedule once it exists. |

**Gate result (pre-research)**: PASS. All three spec clarifications are settled (research R10).

## Project Structure

### Documentation (this feature)

```text
specs/009-scheduled-drift-response/
├── spec.md                         # Feature specification
├── plan.md                         # This file
├── research.md                     # Phase 0
├── data-model.md                   # Phase 1
├── quickstart.md                   # Phase 1 (incl. live-evidence checklist)
├── contracts/
│   ├── orchestrator-schedules.md   # DriftResponse workflow, schedules module
│   └── cli-schedule.md             # `snapl schedule` command group, status row
├── checklists/
│   └── requirements.md             # Spec quality checklist
└── tasks.md                        # Phase 2 (/speckit-tasks — not created here)
```

### Source Code (repository root)

Test files are listed first in each pair, because CLAUDE.md requires the test before the source.

```text
tests/unit/test_orchestrator/
├── test_models.py                      # EXTEND: DriftResponseMode/Request/Result invariants,
│                                       #   AuditEventType additions, SCHEDULE_CHANGED requires actor+change
├── test_remediation.py                 # NEW: select_remediation — forward/undesired-only/errored/clean, order
├── test_schedules.py                   # NEW: build_drift_schedule (pure); ensure/set/pause/resume/trigger/
│                                       #   delete/describe against a fake client; state preservation; audit
├── test_workflow_drift_response.py     # NEW: WorkflowEnvironment.start_time_skipping + mocked activities
│                                       #   (report writes nothing; remediate selects; repair failure; cancel)
├── test_worker_run.py                  # EXTEND: new workflow registered
└── test_vendor_seams.py                # UNCHANGED — covers workflows/drift_response.py automatically

tests/unit/test_presentation/
├── test_cli_schedule.py                # NEW: each command against mocked schedules functions; prompts,
│                                       #   --yes, --json refusal, exit codes, interval parsing
├── test_cli.py                         # EXTEND: status shows the drift-schedule row, exit code unaffected
└── test_render.py                      # EXTEND: schedule view (human + JSON); new audit event rendering

tests/unit/test_tasks/
└── test_demo.py                        # EXTEND: demo.up ensures the schedule after seeding and refuses a
                                        #   foreign SoT; demo.check fails on a missing or paused schedule

tests/integration/test_orchestrator/
└── test_schedules_local.py             # NEW (integration): create→describe→pause→unpause→trigger→
                                        #   set response→describe→delete on WorkflowEnvironment.start_local()

packages/orchestrator/snapl_orchestrator/
├── models.py                           # EXTEND: DriftResponseMode, ExclusionReason, ScheduleChange,
│                                       #   DriftResponseRequest, DriftResponseResult, DriftScheduleView,
│                                       #   ScheduledRunInfo, drift_schedule_id(); AuditEventType += SCHEDULE_CHANGED, DRIFT_RESPONSE
├── exceptions.py                       # EXTEND: ScheduleNotFoundError(OrchestratorError)
├── remediation.py                      # NEW: select_remediation (pure)
├── schedules.py                        # NEW: schedule_id_for (re-export), build_drift_schedule, ensure/set/pause/
│                                       #   resume/trigger/delete/describe/find
├── workflows/
│   └── drift_response.py               # NEW: DriftResponseWorkflow
└── worker/
    └── run.py                          # EXTEND: register DriftResponseWorkflow

packages/presentation/snapl_presentation/
├── cli.py                              # EXTEND: `schedule` Typer sub-app; status row
├── schedule_cli.py                     # NEW (optional split if cli.py grows past readability): the sub-app
└── render.py                           # EXTEND: schedule view + new audit event payloads

tasks/demo.py                           # EXTEND: up → ensure_drift_schedule (report); check → schedule row

docs/demo-scenarios.md                  # EXTEND (implementation PR): "The loop closes itself" scenario
changelog/<issue>.added.md              # implementation PR
```

Unchanged by design: `workflows/deploy_intent.py`, `workflows/scan_drift.py`, `workflows/reconcile_devices.py`, every package's `abc.py`, `adapters/`, the executor and collector, templates, `audit/schema.sql`, `worker/sandbox.py`, and every `tasks/` module except `demo.py`.

**Structure Decision**: Follows the 005 layout. The workflow goes under `workflows/`. No activity is added. Schedule control sits at the package top level next to `models.py`, because the CLI calls it outside any workflow, just as it calls `worker.client`. `remediation.py` is a separate pure module, so eligibility is tested exhaustively without Temporal, and the workflow imports it under passthrough.

## Implementation Order (for /speckit-tasks)

Each step is red → green → refactor, and each user story is shippable on its own:

1. **Foundation**: models, exceptions, `select_remediation`, `build_drift_schedule` (pure). No Temporal needed.
2. **US1 (P1)**: `DriftResponse` with the report path. `ensure_drift_schedule`, `describe_drift_schedule`. Worker registration. `snapl schedule create|show`. The integration round trip. Live evidence item 1.
3. **US2 (P2)**: pause, resume, trigger and delete in `schedules.py` and the CLI. Audit for every change. The `snapl status` row.
4. **US3 (P2)**: the remediate path in `DriftResponse` (eligibility, reconcile child, device-level audit). `set_drift_response`. `snapl schedule mode`, with the confirmation contract and confirm-on-trigger. Live evidence items 2 and 3.
5. **Bootstrap**: `demo.up` calls `ensure_drift_schedule` after seeding, with the SoT identity refusal. `demo.check` gets the schedule row. Tests first, in `test_demo.py`.
6. **Docs**: the demo scenario, README limitations, and the changelog. Live evidence items 4 and 5.

## Post-Design Constitution Re-Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. NAF Alignment | PASS | `schedules.py` is Orchestrator code, and the CLI reaches it along the existing arrow. Audit writes from the CLI process go through the Orchestrator's `AuditLog`, which the CLI already opens for reads. This is not a new deviation: the code is Orchestrator code, called in-process. |
| III. TDD | PASS | 1:1 test files are listed. The time-skipping limitation (no schedules) is designed around, not ignored (R11). |
| V. Contract-First | PASS | Contracts are written. `DriftResponseRequest` is the only new workflow input, and it is versioned with the schedule (rebuilt on every update). |
| VI. Vendor seams | PASS | The new workflow's imports are listed in the contract, and none is a seam. |
| VII. Simplicity | PASS | No environment variable, no new store, no new package. The overlap and pause semantics are Temporal's. |
| VIII.2 / VIII.5 | PASS | Report is the only create mode. Remediate is explicit, confirmed and audited, and never removes config. Undesired-only devices are never written. |

## Complexity Tracking

| Addition | Why needed | Simpler alternative rejected because |
|-----------|------------|-------------------------------------|
| `DriftResponse` wrapper workflow instead of scheduling `ScanDrift` directly | One workflow type across both responses; every run records its response (VIII.2); overlap covers scan and repair together | Scheduling `ScanDrift` directly would mean changing its signature (FR-028), or switching workflow types on a response change (R2) |
| `describe_drift_schedule` makes one describe call per shown run, plus a result fetch for completed ones | `ScheduleActionResult` in SDK 1.25.0 carries no run status or result, and FR-024 asks for each run's status and summary | Showing only ids would push the operator to the Web UI, against SC-010 |
