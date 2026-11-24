# Implementation Plan: Remove Undesired Config

**Branch**: `013-remove-undesired-config` | **Date**: 2026-11-24 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/013-remove-undesired-config/spec.md`, live captures in [captures.md](captures.md)

## Summary

Give the operator a confirmed, verified way to delete config that intent doesn't hold (#65). Detection already exists from spec 007; this adds removal.

The design:

- **`snapl remove <device>`** previews by default and removes only with `--apply` (FR-013). The preview is a read-only workflow, `PlanRemoval`. It scans the one device, plans the deletes and checks them with the Executor's dry run. It writes nothing.
- **The plan** comes from the Observer's new `plan_removal(report)`, in the drift field map's seam (`structural/removal.py`). Each undesired entity gets one delete of its entry:
  - an interface outside intent;
  - a BGP neighbour outside intent;
  - a `routed_interface` binding (#315);
  - an extra address on an interface intent manages (#193).

  `routing_policy` and protected names are listed as refused, with the reason (research R2, R3).
- **`RemoveConfig`** runs the confirmed removal, in order:
  1. refuse if a deploy to the device is in flight;
  2. re-scan and re-plan;
  3. refuse if the delete paths differ from the confirmed ones;
  4. dry-run;
  5. send **one gNMI Set of deletes only**;
  6. re-scan to verify.

  C1 shows a Set is atomic, so a rejection leaves nothing half-done (FR-007 to FR-010).
- **The Executor** gains `dry_run_remove` and `remove`. `GnmiExecutor` checks every path against four allowed entity shapes and the field map's protected set, which the worker bootstrap injects. Without that set, the executor refuses every path (research R5).
- **The record**: both workflows write the usual audit events. A removal's start requires an actor. The dashboard's episodes can now be closed by a removal (research R10).
- **Never automatic**: nothing in `DriftResponse`, `ReconcileDevices` or `DeployIntent` changes or imports removal, and a structural test enforces this (FR-012).

Two decisions were settled with the maintainer on 2026-11-24 and come from the captures:
1. `routed_interface` joins the removable kinds; `routing_policy` stays out (R3).
2. An empty peer group and a subinterface's `ipv4 admin-state` stay on the device after a removal, as known leftovers the scan doesn't see (R4).

The spec's FR-014 and Out of Scope are amended to match.

Decisions are in [research.md](research.md), types in [data-model.md](data-model.md), interfaces in [contracts/](contracts/), and usage and the live-evidence run in [quickstart.md](quickstart.md).

## Technical Context

**Language/Version**: Python 3.12+. TypeScript 5.9 for one dashboard label.

**Primary Dependencies**:
- pygnmi (`gNMIclient.set(delete=…)`, already used by the captures);
- temporalio 1.25.0 (`WorkflowIDConflictPolicy.FAIL`, `activity.client()`);
- pydantic>=2.5, typer and rich.

No new dependency.

**Storage**: the existing SQLite `AuditLog`. No new event type and no DDL change.

**Testing**: pytest with `unit` and `live` markers. Workflows run on `WorkflowEnvironment.start_time_skipping()` with mocked activities; pygnmi is mocked in the executor tests; the UI uses Vitest. Live evidence follows [quickstart.md](quickstart.md).

**Target Platform**: macOS with OrbStack, and the dcfabric Containerlab lab (SR Linux v26.3.1-410).

**Project Type**: the uv workspace's library packages (observability, executor, orchestrator, presentation), the worker and the CLI.

**Performance Goals**:
- A preview takes one device's intent fetch, collect and detect, about as long as a one-device scan.
- A removal is two of those plus one Set (C5: a Set takes about 250 ms).

**Constraints**:
- No delete at `/` or at a container (FR-005).
- One Set per device (FR-008).
- No vendor names in workflows or models (Principle VI).
- The `DeployIntent`, `ScanDrift`, `ReconcileDevices` and `DriftResponse` signatures and code are left unchanged.

**Scale/Scope**: one device per removal, exercised on leaf-04.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Evidence |
|---|---|---|
| I. NAF Alignment | PASS | Each block keeps its role. Planning is Observability (the drift field map), the delete Set is the Executor, the workflows are the Orchestrator and the command is Presentation. Dependency arrows are unchanged: the protected set reaches the Executor through the worker bootstrap, never through an import (R5). No new ADR deviation. |
| II. Spec-Driven | PASS | This is the first write that deletes, specified in spec 013 and clarified on 2026-11-15. C1 to C5 were answered on the lab on 2026-11-24, before this plan. |
| III. TDD | PASS | Every new source file has its test file listed first (below). The feature touches the gNMI boundary and the drift diff's output, so the implementation PR carries quickstart's live run, and its captured responses become fixtures. |
| IV. Modularity | PASS | No new package. The planner is a pure module, tested without Temporal or a device. |
| V. Contract-First | PASS | The Observer and Executor ABC additions are in contracts/blocks.md, written before the code. The workflows compose those contracts through activities. |
| VI. Vendor seams | PASS | Path shapes live only in `structural/removal.py` (the field map) and `GnmiExecutor` (the guard). The workflows import neither, and `test_vendor_seams.py` covers `workflows/removal.py` automatically. |
| VII. Simplicity | PASS | No new store, event type or environment variable. Serialization uses an id family and a describe call, not a lock mechanism (R8). Leftovers are documented, not detected (R4). `routing_policy` removal is deferred (R3). |
| VIII.1 Authority split | PASS | Removal moves the device toward intent and never writes intent. |
| VIII.4 Idempotency, dry run first | PASS | `dry_run_remove` runs in every preview and immediately before every Set. A repeated delete is a no-op (C1). |
| VIII.5 Undesired config | PASS | Never automatic: only `snapl remove --apply`, confirmed, and never from a schedule (structural test). Always preceded by a dry run. Never applied to the protected set: the planner refuses it and the executor guard fails closed. |

**Gate result (pre-research)**: PASS. The spec's open questions were the five captures, all answered (captures.md). The two questions the captures raised were decided by the maintainer on 2026-11-24.

## Project Structure

### Documentation (this feature)

```text
specs/013-remove-undesired-config/
├── spec.md                     # Feature specification (FR-014, Out of Scope amended 2026-11-24)
├── captures.md                 # Live captures C1 to C5
├── plan.md                     # This file
├── research.md                 # Phase 0
├── data-model.md               # Phase 1
├── quickstart.md               # Phase 1, with the live-evidence run
├── contracts/
│   ├── blocks.md               # Observer.plan_removal, Executor.dry_run_remove / remove
│   ├── orchestrator-removal.md # PlanRemoval, RemoveConfig, activities, never-scheduled test
│   └── cli-remove.md           # snapl remove
├── checklists/
│   └── requirements.md
└── tasks.md                    # Phase 2 (/speckit-tasks; not created here)
```

### Source Code (repository root)

Each pair lists the test file first, because CLAUDE.md requires the test before the source.

```text
tests/unit/test_observability/
├── test_models.py                      # EXTEND: RemovalPlan, PlannedDelete, RefusedEntity invariants
├── test_removal_plan.py                # NEW: plan_removal over hand-built and fixture reports (contracts/blocks.md table)
└── test_observer.py                    # EXTEND: StructuralObserver.plan_removal delegates; ERROR report raises

packages/observability/snapl_observability/
├── abc.py                              # EXTEND: Observer.plan_removal
├── models.py                           # EXTEND: RemovalRefusal, PlannedDelete, RefusedEntity, RemovalPlan
└── structural/
    ├── removal.py                      # NEW: plan_removal (reads ENTITY_FIELD_MAP)
    └── observer.py                     # EXTEND: plan_removal

tests/unit/test_executor/
├── test_models.py                      # EXTEND: RemoveResult invariants
└── test_gnmi_remove.py                 # NEW: guard shapes, protected, fail-closed, one delete-only Set, errors

packages/executor/snapl_executor/
├── abc.py                              # EXTEND: dry_run_remove, remove
├── models.py                           # EXTEND: RemoveResult
└── gnmi/executor.py                    # EXTEND: protected_interfaces, guard, dry_run_remove, remove

tests/unit/test_orchestrator/
├── test_models.py                      # EXTEND: WorkflowReason additions, RemovalRequest/Verification/Result,
│                                       #   RemoveConfig start requires actor + confirmed_paths
├── test_activity_executor.py           # EXTEND: dry_run_remove, remove_config (+ gNMI call metric)
├── test_activity_observability.py      # EXTEND: plan_removal
├── test_activity_workflows.py          # NEW: deploy_in_flight (running, closed, not found)
├── test_workflow_removal.py            # NEW: PlanRemoval and RemoveConfig (contracts/orchestrator-removal.md)
├── test_removal_paths.py               # NEW: SC-003; planner and executor guard agree; protected set is one list
├── test_removal_never_scheduled.py     # NEW: FR-012
├── test_worker_run.py                  # EXTEND: both workflows and four activities registered; executor gets the
│                                       #   field map's protected set
└── test_vendor_seams.py                # UNCHANGED: covers workflows/removal.py

packages/orchestrator/snapl_orchestrator/
├── models.py                           # EXTEND: WorkflowReason += plan_changed, deploy_in_flight, remove_failed;
│                                       #   RemovalRequest, RemovalVerification, RemovalResult; AuditEvent validator
├── activities/executor.py              # EXTEND: dry_run_remove, remove_config
├── activities/observability.py         # EXTEND: plan_removal
├── activities/workflows.py             # NEW: deploy_in_flight
├── workflows/removal.py                # NEW: PlanRemovalWorkflow, RemoveConfigWorkflow
└── worker/run.py                       # EXTEND: register; GnmiExecutor(protected_interfaces=…)

tests/unit/test_presentation/
├── test_cli_remove.py                  # NEW: contracts/cli-remove.md
├── test_render.py                      # EXTEND: plan, result, audit line (human + JSON)
├── test_dashboard_timeline.py          # EXTEND: a RemoveConfig run closes an undesired episode, kind "removal";
│                                       #   a failed one does not
└── (UI) src/lib/episodes.test.ts, parity.test.ts   # EXTEND: "closed by a removal" on both pages

packages/presentation/snapl_presentation/
├── cli.py                              # EXTEND: `remove` command, TTY check
├── render.py                           # EXTEND: plan, result, RemoveConfig audit line
├── dashboard/models.py                 # EXTEND: EpisodeClose.kind += "removal"
├── dashboard/projections.py            # EXTEND: closers include RemoveConfig; _close_kind
├── dashboard/static/index.html         # EXTEND: the label
└── ui/src/{lib/episodes.ts, components/timeline/Lanes.tsx, api/schema.d.ts}   # EXTEND: the label; regenerate

docs/demo-scenarios.md                  # EXTEND (implementation PR): "Remove what intent never asked for"
README.md                               # EXTEND: limitations: removal is manual; R4 leftovers; routing_policy not removable
changelog/65.added.md                   # implementation PR
specs/013-remove-undesired-config/evidence.md   # implementation PR: the quickstart run
```

Left unchanged by design:
- `workflows/deploy_intent.py`, `scan_drift.py`, `reconcile_devices.py` and `drift_response.py`;
- `schedules.py`, `remediation.py` and `adapters/srlinux.py`;
- `diff.py`'s detection;
- the templates, the collector and `audit/schema.sql`.

**Structure Decision**: Spec 005's layout, extended in each block's existing modules. The planner is a new module beside `diff.py`, because it reads the same map and is that map's inverse. The two workflows share one file and one helper for the scan steps. The only new activity module is `activities/workflows.py`, because `deploy_in_flight` uses the Temporal client and belongs to no NAF block.

## Implementation Order (for /speckit-tasks)

Each step is red → green → refactor, and each user story ships on its own:

1. **Foundation**: observability models and `plan_removal` (pure); executor `RemoveResult` and the guard (pure); orchestrator models. SC-003's test goes here, before any workflow exists.
2. **US1, preview (P1)**:
   - `Observer.plan_removal`;
   - `Executor.dry_run_remove`;
   - the `plan_removal` and `dry_run_remove` activities;
   - `PlanRemoval`;
   - worker registration, with the protected set injected;
   - `snapl remove` (preview);
   - the render.

   Live check: quickstart steps 1 and 2 (SC-002).
3. **US2, remove (P1)**:
   - `Executor.remove`;
   - the `remove_config` and `deploy_in_flight` activities;
   - `RemoveConfig`;
   - `snapl remove --apply` with the TTY check;
   - the result render;
   - the never-scheduled test.

   Live check: quickstart steps 3, 4 and 6 (SC-001, SC-004).
4. **US3, the record (P2)**: the `AuditEvent` validator (it lands with the models in step 1; this is where its use is tested), the audit line, the episode closer and kind, the UI and plain-page labels, the regenerated schema. Live check: quickstart step 5 (SC-005).
5. **Docs**: the demo scenario, README's limitations, the changelog and `evidence.md`. `docs/demo-scenarios.md` and the README stop saying undesired config can't be removed.

## Post-Design Constitution Re-Check

| Principle | Status | Notes |
|---|---|---|
| I. NAF Alignment | PASS | `deploy_in_flight` is Orchestrator code. It reads Temporal, not a block. The protected set crosses from Observability to the Executor only in the worker bootstrap, which is the one place allowed to name concrete implementations (V). |
| III. TDD | PASS | Test files are listed one to one. The guard and the planner are tested against each other (SC-003), not only each against a mock. |
| V. Contract-First | PASS | The ABC additions are written down in contracts/blocks.md. `RemovalRequest` is the only new workflow input. |
| VI. Vendor seams | PASS | The four path shapes live in two seams, the planner (as templates) and the guard (as patterns). SC-003's test keeps them in agreement. |
| VII. Simplicity | PASS | There are two workflows rather than one with a mode, so the dashboard needs no new filter (R6). Nothing new is added for the deploy race, because the writes commute (R8). |
| VIII.4 / VIII.5 | PASS | A dry run before every Set; operator-only; protected refused twice; never scheduled, enforced by a test. |

## Complexity Tracking

| Addition | Why needed | Simpler alternative rejected because |
|---|---|---|
| The executor guard duplicates the planner's path shapes | FR-004 and FR-005 bind the Executor itself ("MUST NOT issue"), not only the planner. A planner bug must not reach a device. | Trusting the plan alone gives one layer of protection for the first destructive write. SC-003's test keeps the two in agreement. |
| `PlanRemoval` as a separate workflow type | A preview must never appear as a write in the run list or the audit trail (R6) | One type with a preview flag would need every projection that counts writes to read the flag |
| `deploy_in_flight` checked twice | FR-010: the re-scan takes seconds, and a deploy could start in between | A single check at the start leaves the re-scan window open. A lock workflow is a new mechanism (R8). |
