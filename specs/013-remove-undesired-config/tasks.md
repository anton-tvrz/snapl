# Tasks: Remove Undesired Config

**Input**: Design documents from `/specs/013-remove-undesired-config/`
**Prerequisites**: plan.md, spec.md, captures.md, research.md, data-model.md, contracts/blocks.md, contracts/orchestrator-removal.md, contracts/cli-remove.md, quickstart.md

**Tests**: TDD is mandatory per CLAUDE.md and the constitution. Every phase lists its tests first. Each test is run and seen to fail before the code it covers is written.

**Organization**: Tasks are grouped by the spec's user stories: US1 preview (P1), US2 remove (P1), US3 the record (P2). Each story is one implementation PR, and each closes with its live check from quickstart.md.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1, US2 or US3 from spec.md
- Exact file paths in every description

---

## Phase 1: Setup

No setup. There is no new package, dependency or directory: pygnmi's `set(delete=…)` and temporalio 1.25.0's `WorkflowIDConflictPolicy.FAIL` and `activity.client()` are already in the lock (plan, Technical Context).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The models, the pure planner and the pure executor guard. Every story depends on these. None of them needs Temporal or a device.

### Tests (write first, verify they fail)

- [x] T001 [P] Extend tests/unit/test_observability/test_models.py with the removal models from data-model.md:
  - `RemovalRefusal` values `protected` and `not_removable`;
  - `PlannedDelete` rejects empty `answers`;
  - `RefusedEntity` rejects empty `paths`;
  - `RemovalPlan` rejects a path in both a delete's `answers` and a refusal's `paths`, a `forward_drift` path in either, unsorted or duplicate delete paths, and unsorted refusals;
  - `delete_paths` is the sorted list;
  - `is_empty` is true with no deletes, refusals or not;
  - every model is frozen and forbids extra fields.
- [x] T002 [P] Write tests/unit/test_observability/test_removal_plan.py for `structural/removal.py::plan_removal(report)`. It covers every row of contracts/blocks.md's table, using hand-built `DriftReport`s and the existing diff fixtures in tests/unit/test_observability/. It also asserts:
  - every undesired path lands in exactly one delete or refusal;
  - forward paths land only in `forward_drift`;
  - two calls on one report give equal plans;
  - an `ERROR` report raises `ValueError`;
  - a `CLEAN` report gives an empty plan;
  - a port with an address and its `routed_interface` binding give two deletes;
  - `system0.0` and `mgmt0.0` bindings are refused as `protected`, read from `ENTITY_FIELD_MAP["interface"]["protected"]`;
  - the item's path is used verbatim for an extra address whose prefix contains `/` (C3).
- [x] T003 [P] Extend tests/unit/test_executor/test_models.py with `RemoveResult`'s invariants:
  - `success=True` means `error` is None and `refused` is empty;
  - `success=False` means `error` is set;
  - `dry_run=True` means `device_response` is None;
  - it is frozen.
- [x] T004 [P] Write the guard half of tests/unit/test_executor/test_gnmi_remove.py, for a pure `check_delete_paths(paths, protected) -> dict[str, str]` in packages/executor/snapl_executor/gnmi/executor.py. It returns refused paths with their reasons:
  - **Accepted**: the four shapes in contracts/blocks.md, with `X` containing `/` (`ethernet-1/7`), `P` containing `/` (`192.0.2.1/31`), and `X.N` bindings.
  - **Refused**: `/`, `/interface`, `/network-instance[name=default]`, `/network-instance[name=default]/protocols/bgp`, `/network-instance[name=default]/interface`, `/routing-policy`, a policy entry, leaf paths (`/interface[name=X]/description`, `…/neighbor[peer-address=A]/peer-as`), another network instance, and a path with trailing text.
  - **Protected**: `mgmt0`, `system0` and `system0.0` are refused with the reason `protected`.
  - **Fail closed**: `protected=None` refuses everything.
- [x] T005 [P] Extend tests/unit/test_orchestrator/test_models.py:
  - `WorkflowReason` gains `plan_changed`, `deploy_in_flight` and `remove_failed`;
  - `RemovalRequest` rejects an empty actor, empty `confirmed_paths` and duplicates;
  - `RemovalVerification.passed` and `gone ∪ remaining`;
  - `RemovalResult` invariants from data-model.md, including the `collect_failed`-after-write exception;
  - an `AuditEvent` of `workflow_type="RemoveConfig"` and `WORKFLOW_STARTED` without `actor`, or without `payload["confirmed_paths"]`, is rejected;
  - other workflow types are unaffected.
- [x] T006 Write tests/unit/test_orchestrator/test_removal_paths.py (SC-003, FR-004). For every report in T002's set, plus adversarial reports naming `mgmt0`, `system0` and `system0.0`:
  - every planned delete path passes `check_delete_paths(paths, ENTITY_FIELD_MAP["interface"]["protected"])`;
  - every refused entity's path would also be refused by it;
  - every protected name in the field map is refused.

### Implementation

- [x] T007 Add `RemovalRefusal`, `PlannedDelete`, `RefusedEntity` and `RemovalPlan` to packages/observability/snapl_observability/models.py, and export them from the package's `__init__.py` (T001)
- [x] T008 Implement packages/observability/snapl_observability/structural/removal.py with `plan_removal(report) -> RemovalPlan`, following research R2. Reuse `diff._key_pattern` (or move it to a shared helper in `diff.py`) instead of writing new regexes. (T002)
- [x] T009 [P] Add `RemoveResult` to packages/executor/snapl_executor/models.py, with a `__post_init__` that enforces T003's invariants (T003)
- [x] T010 [P] Add `check_delete_paths` to packages/executor/snapl_executor/gnmi/executor.py, as module-level compiled patterns for the four shapes plus the protected check (T004)
- [x] T011 [P] Add the `WorkflowReason` values, `RemovalRequest`, `RemovalVerification`, `RemovalResult` and the `AuditEvent` validator branch to packages/orchestrator/snapl_orchestrator/models.py (T005)
- [x] T012 Run T006 and see it pass with T008 and T010 in place. Break the planner's protected check on purpose and see T006 fail, then restore it.

**Checkpoint**: `uv run invoke test-unit` is green. The planner and the guard agree on every path. Nothing touches a device yet.

---

## Phase 3: User Story 1: Preview What Would Be Removed (P1) 🎯 MVP

**Goal**: `snapl remove <device>` shows the deletes it would issue, the drift paths each one answers, and what it refuses and why. It writes nothing (FR-001 to FR-005, FR-013).

**Independent Test**: quickstart steps 1 and 2. Three deletes are listed for the hand-added config, the exit code is 2, and the running config is identical before and after (SC-002).

### Tests (write first, verify they fail)

- [x] T013 [P] [US1] Extend tests/unit/test_observability/test_abc.py: an `Observer` subclass without `plan_removal` can't be instantiated, and the `Complete` subclass gains it. Extend tests/unit/test_observability/test_observer.py: `StructuralObserver.plan_removal` returns `structural.removal.plan_removal`'s result, and raises on an `ERROR` report.
- [x] T014 [P] [US1] Extend tests/unit/test_executor/test_abc.py: an `Executor` subclass without `dry_run_remove` can't be instantiated, and `GoodExecutor` gains it. Extend tests/unit/test_executor/test_gnmi_remove.py for `GnmiExecutor.dry_run_remove`:
  - it never constructs `gNMIclient` (patched to fail if called);
  - it returns `dry_run=True`, `success=True` for allowed paths;
  - a refused path gives `success=False` with `refused` and `error` set;
  - the constructor without `protected_interfaces` refuses every path;
  - it raises `ValueError` on an empty list or a duplicate.
- [x] T015 [P] [US1] Extend tests/unit/test_orchestrator/test_activity_observability.py (`plan_removal` calls `observer.plan_removal`) and tests/unit/test_orchestrator/test_activity_executor.py (`dry_run_remove` calls `executor.dry_run_remove` and records no gNMI call metric, since nothing dials)
- [x] T016 [P] [US1] Write the `PlanRemoval` half of tests/unit/test_orchestrator/test_workflow_removal.py, using `WorkflowEnvironment.start_time_skipping()` with mocked activities. Cover:
  - the call order intent → collect → detect → `plan_removal` → `dry_run_remove`;
  - `dry_run_remove` is skipped for an empty plan;
  - the plan is returned;
  - the audit events (start, one `ACTIVITY_COMPLETED` per step, terminal with `plan_paths` and `refused_paths`), with `target_id` set to the device;
  - intent not found, intent unavailable and collect failure each end with the matching reason and raise `ApplicationError`;
  - a dry-run refusal ends `remove_failed` and raises;
  - no executor write activity is ever scheduled.
- [x] T017 [P] [US1] Extend tests/unit/test_orchestrator/test_worker_run.py: `PlanRemovalWorkflow`, `plan_removal` and `dry_run_remove` are registered, and `_build_executor()` passes `ENTITY_FIELD_MAP["interface"]["protected"]` as `protected_interfaces`
- [x] T018 [P] [US1] Write the preview half of tests/unit/test_presentation/test_cli_remove.py, from contracts/cli-remove.md:
  - with deletes: exit 2 and the human text;
  - nothing to remove: exit 0;
  - refusals only: exit 0, and the refusals are listed;
  - `--json` gives `{"plan": …}`;
  - a device not in intent is refused before any workflow starts;
  - no worker: exit 1 with the hint;
  - the workflow id has the shape `plan-removal-{id}-…`.
- [x] T019 [P] [US1] Extend tests/unit/test_presentation/test_render.py with the plan view, human and JSON. The human view shows deletes with their answers, the neighbour note (session torn down, group stays, research R4), refusals with reasons, the forward-drift line naming `snapl reconcile`, and "Nothing was written."

### Implementation

- [x] T020 [US1] Add `Observer.plan_removal` to packages/observability/snapl_observability/abc.py and implement it in packages/observability/snapl_observability/structural/observer.py, delegating to `structural.removal` (T013)
- [x] T021 [US1] Add `Executor.dry_run_remove` to packages/executor/snapl_executor/abc.py. In packages/executor/snapl_executor/gnmi/executor.py, add the `protected_interfaces` constructor argument and `dry_run_remove` over `check_delete_paths` (T014)
- [x] T022 [P] [US1] Add the `plan_removal` activity to packages/orchestrator/snapl_orchestrator/activities/observability.py and the `dry_run_remove` activity to packages/orchestrator/snapl_orchestrator/activities/executor.py (T015)
- [x] T023 [US1] Create packages/orchestrator/snapl_orchestrator/workflows/removal.py with the shared scan helper (intent → collect → detect, with `DeployIntent`'s retry policies and failure reasons) and `PlanRemovalWorkflow` (T016)
- [x] T024 [US1] Register the workflow and activities in packages/orchestrator/snapl_orchestrator/worker/run.py, and pass the field map's protected set to `GnmiExecutor` in `_build_executor` (T017)
- [x] T025 [US1] Add the `remove` command (preview only) to packages/presentation/snapl_presentation/cli.py and the plan view to packages/presentation/snapl_presentation/render.py (T018, T019)
- [x] T026 [US1] Live check (quickstart steps 1 and 2) on leaf-04, with the worker running from the branch and the drift schedule paused. Record the preview outputs and the identical config diff in specs/013-remove-undesired-config/evidence.md. Remove the probe config afterwards and resume the schedule.

**Checkpoint**: US1 ships on its own. Operators can see the exact deletes that would fix a device and apply them by hand. It ships as one PR (`feat(executor): preview removing undesired config`, `Closes` a slice issue under #65, with live evidence in the body).

---

## Phase 4: User Story 2: Remove It, Confirmed (P1)

**Goal**: `snapl remove <device> --apply` re-scans, refuses if anything changed or a deploy is in flight, sends one Set of deletes, and verifies with a fresh scan (FR-006 to FR-010, FR-012).

**Independent Test**: quickstart steps 3, 4 and 6. A device that changes at the prompt has nothing written to it (SC-004). The confirmed removal leaves the next scan with no undesired items while the fabric sessions stay up (SC-001). A port goes in the same Set as its binding.

### Tests (write first, verify they fail)

- [ ] T027 [P] [US2] Extend tests/unit/test_executor/test_abc.py (`remove` is abstract) and tests/unit/test_executor/test_gnmi_remove.py for `GnmiExecutor.remove`, with pygnmi mocked. Cover:
  - exactly one `set(delete=paths)`, with no `update` or `replace` keyword, inside the per-host lock;
  - a refused path means `gNMIclient` is never constructed;
  - a gRPC error gives `success=False` with the device text (use C1's `FailedPrecondition` text from captures.md as the fixture);
  - an empty `str(exc)` dial error gives the #194 message;
  - `device_response` is set on success;
  - it raises `ValueError` on an empty list or a duplicate.
- [ ] T028 [P] [US2] Extend tests/unit/test_orchestrator/test_activity_executor.py for `remove_config`: it calls `executor.remove` and records `snapl_gnmi_call_duration{operation="configure"}`, with outcome `ok` or `error`. Write tests/unit/test_orchestrator/test_activity_workflows.py for `deploy_in_flight`, using a fake `activity.client()`:
  - a RUNNING handle gives True;
  - COMPLETED, FAILED or TERMINATED gives False;
  - `RPCError` not-found gives False;
  - the id described is `deploy-intent-{id}`.
- [ ] T029 [P] [US2] Write the `RemoveConfig` half of tests/unit/test_orchestrator/test_workflow_removal.py, covering every case listed in contracts/orchestrator-removal.md. In particular:
  - `remove_config` is never scheduled when the plan changed, a deploy is in flight (first or second check), the dry run refused, or intent is missing;
  - its argument equals `request.confirmed_paths`;
  - the start event carries `actor` and `confirmed_paths`.
- [ ] T030 [P] [US2] Write tests/unit/test_orchestrator/test_removal_never_scheduled.py (FR-012): an AST check that `workflows/drift_response.py`, `workflows/reconcile_devices.py` and `workflows/deploy_intent.py` import none of `workflows.removal`, `remove_config`, `dry_run_remove` or `plan_removal`
- [ ] T031 [P] [US2] Extend tests/unit/test_orchestrator/test_worker_run.py: `RemoveConfigWorkflow`, `remove_config` and `deploy_in_flight` are registered
- [ ] T032 [P] [US2] Write the apply half of tests/unit/test_presentation/test_cli_remove.py, from contracts/cli-remove.md:
  - `--apply --yes` starts `RemoveConfig` with id `remove-config-{id}`, `WorkflowIDConflictPolicy.FAIL`, `actor=_actor()` and `confirmed_paths` equal to the previewed plan's `delete_paths`;
  - a prompt answered no starts nothing;
  - non-TTY stdin without `--yes` refuses before connecting;
  - `--json` without `--yes` is refused;
  - an empty plan exits 0 without starting `RemoveConfig`;
  - each reason's text and exit code;
  - `WorkflowAlreadyStartedError` maps to "a removal of <device> is already running", exit 1.
- [ ] T033 [P] [US2] Extend tests/unit/test_presentation/test_render.py with the result view, human and JSON, for each reason. `plan_changed` renders the new plan.

### Implementation

- [ ] T034 [US2] Add `Executor.remove` to packages/executor/snapl_executor/abc.py and implement `GnmiExecutor.remove` (guard, then `_blocking_delete` under `_set_locks` in `asyncio.to_thread`) in packages/executor/snapl_executor/gnmi/executor.py (T027)
- [ ] T035 [P] [US2] Add the `remove_config` activity to packages/orchestrator/snapl_orchestrator/activities/executor.py, and create packages/orchestrator/snapl_orchestrator/activities/workflows.py with `deploy_in_flight` (T028)
- [ ] T036 [US2] Add `RemoveConfigWorkflow` to packages/orchestrator/snapl_orchestrator/workflows/removal.py. It follows the state diagram in data-model.md and the verification in research R9 (T029, T030)
- [ ] T037 [US2] Register the workflow and activities in packages/orchestrator/snapl_orchestrator/worker/run.py (T031)
- [ ] T038 [US2] Add `--apply` and `--yes`, the TTY check and the result view to `remove` in packages/presentation/snapl_presentation/cli.py and packages/presentation/snapl_presentation/render.py (T032, T033)
- [ ] T039 [US2] Live check on leaf-04: quickstart steps 3 (SC-004), 4 (SC-001) and 6 (a port and its binding in one Set). Record them in specs/013-remove-undesired-config/evidence.md. Add the captured `remove` response and rejection text as fixtures under tests/unit/test_executor/fixtures/ (new directory) and use them in T027's tests (constitution III). Leave leaf-04 clean, with the schedule resumed.

**Checkpoint**: #65's capability exists: undesired config can be removed, operator-confirmed, dry-run first, in one atomic Set, and verified. Scheduled runs still never remove.

---

## Phase 5: User Story 3: See Removals in the Record (P2)

**Goal**: Removals appear in `snapl audit`, the run list and the device's history, told apart from deploys. An undesired-only episode is closed by its removal, not left without a cause (FR-011, SC-005). The dashboard shows the command to run, each removal's detail and each device's latest preview, and never starts either workflow (FR-016, US3 scenarios 3 to 5, amended 2026-11-24).

**Independent Test**: quickstart step 5. `snapl audit --device leaf-04` shows the removal with its actor, deletes and verification. After the next scheduled scan, the dashboard's timeline shows leaf-04's episode closed by a removal. The removal's run detail shows its deletes and verification, and leaf-04's drawer shows its latest preview.

### Tests (write first, verify they fail)

- [ ] T040 [P] [US3] Extend tests/unit/test_presentation/test_render.py: the `RemoveConfig` terminal audit line (`removed N: <paths>; verified`, or the reason) with the actor from the start event; `PlanRemoval` rows render through the existing table
- [ ] T041 [P] [US3] Extend tests/unit/test_presentation/test_dashboard_timeline.py:
  - an undesired-only episode whose last drifted scan is followed by a successful `RemoveConfig` targeting the device, then a clean `ScanDrift`, closes with `closed_by.kind == "removal"` and the removal's workflow id and trigger `operator`;
  - a failed `RemoveConfig` closes nothing;
  - a `RemoveConfig` for another device closes nothing;
  - when a deploy and a removal both qualify, the later end wins, as for two deploys.
- [ ] T042 [P] [US3] Extend tests/unit/test_presentation/test_dashboard_projections.py: `project_runs` lists `RemoveConfig` and `PlanRemoval` runs with trigger `operator`, and a workflow-type filter for `RemoveConfig` selects only removals
- [ ] T043 [P] [US3] Extend tests/unit/test_presentation/test_dashboard_projections.py for the command (FR-016):
  - `remove_command` is `snapl remove <name>` for an `UNDESIRED` and a `MISSING_AND_UNDESIRED` device, and None otherwise;
  - `suggested_command` is still `snapl reconcile <name>` for `MISSING_AND_UNDESIRED`, so both show;
  - `UNDESIRED_NOTE` no longer says removal is unavailable.
- [ ] T044 [P] [US3] Extend tests/unit/test_presentation/test_dashboard_projections.py for a removal's detail: a `RemoveConfig` run's `removal` holds the actor and `confirmed_paths` from its start event, and `deleted_paths`, `device_response`, the four verification lists and the reason from its terminal event. Cover a verified removal, `plan_changed` (no deletes, the new plan's paths), `remove_failed` (the device's text) and a run still in flight (no terminal event). Other workflow types have `removal` None.
- [ ] T045 [P] [US3] Extend tests/unit/test_presentation/test_dashboard_projections.py for the latest preview: `last_removal_preview` is the device's latest successful `PlanRemoval` (time, workflow id, `plan_paths`, `refused_paths`); a failed preview and another device's preview are ignored; a device never previewed has None. It respects the `at=` time like the rest of `project_devices`.
- [ ] T046 [P] [US3] Extend the UI tests:
  - packages/presentation/ui/src/lib/episodes.test.ts ("closed by a removal") and packages/presentation/ui/src/components/timeline/Lanes.test.tsx for the new kind's key entry;
  - packages/presentation/ui/src/components/panels/Undesired.test.tsx and DeviceDrawer.test.tsx: the remove command beside reconcile, the latest preview line and its refused entities, and no button or form anywhere (FR-016);
  - packages/presentation/ui/src/components/panels/Runs.test.tsx: a removal's detail, verified and not;
  - the plain/designed parity test packages/presentation/ui/src/parity.test.ts with a removal-closed episode, the remove command and a removal's detail.

### Implementation

- [ ] T047 [US3] Add the `RemoveConfig` audit line to packages/presentation/snapl_presentation/render.py (T040)
- [ ] T048 [US3] Add `"removal"` to `EpisodeClose.kind` in packages/presentation/snapl_presentation/dashboard/models.py. In packages/presentation/snapl_presentation/dashboard/projections.py, make `project_timeline`'s closers include successful `RemoveConfig` runs, and have `_close_kind` return `"removal"` for them (T041, T042)
- [ ] T049 [US3] Add `DeviceView.remove_command` and `DeviceView.last_removal_preview` to dashboard/models.py, set them in `_drift_fields` and `project_devices`, and reword `UNDESIRED_NOTE` (T043, T045)
- [ ] T050 [US3] Add `RunView.removal` to dashboard/models.py and fill it in `project_runs` from the run's start and terminal events (T044)
- [ ] T051 [US3] Regenerate tests/unit/test_presentation/fixtures/openapi_v1.json and packages/presentation/ui/src/api/schema.d.ts. Add the label to packages/presentation/ui/src/lib/episodes.ts and Lanes.tsx; the command and the latest preview to panels/Undesired.tsx and DeviceDrawer.tsx; the removal detail to panels/Runs.tsx; and all of them to packages/presentation/snapl_presentation/dashboard/static/index.html. Rebuild with `uv run invoke dashboard.build` (T046)
- [ ] T052 [US3] Render the page from fixtures with `e2e/look.mjs` and check the new views keep the look rules (dev/skills/dashboard/SKILL.md, "Look")
- [ ] T053 [US3] Live check: quickstart step 5 on leaf-04, recorded in specs/013-remove-undesired-config/evidence.md with screenshots of the closed episode, the removal's run detail and leaf-04's drawer

**Checkpoint**: every removal is accounted for in the CLI and on both dashboard pages, and the dashboard offers no way to start one.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T054 [P] Add the "Remove what intent never asked for" scenario to docs/demo-scenarios.md (quickstart steps 1, 2 and 4), and change the scenarios that say undesired config can't be removed
- [ ] T055 [P] Update README.md's known limitations:
  - removal is operator-only and never scheduled;
  - routing policies are detected, not removed (research R3);
  - an empty peer group and a subinterface's `ipv4 admin-state` remain after a removal (research R4).
- [ ] T056 [P] Add changelog/65.added.md in the PR that closes #65 (the US2 PR), and a `Recent Changes` entry for 013 in CLAUDE.md, written by hand: `update-agent-context.sh` deletes older entries (see PR #395)
- [ ] T057 Check that the new modules have at least 80% coverage, that `test_vendor_seams.py` covers workflows/removal.py unchanged, and that `uv run invoke lint`, `uv run invoke format` and `uv run invoke test-unit` pass
- [ ] T058 Mark spec.md's status shipped and its success criteria met, each with a pointer to evidence.md

---

## Dependencies & Execution Order

- **Foundational (T001 to T012)** → **US1 (T013 to T026)** → **US2 (T027 to T039)** → **US3 (T040 to T053)** → **Polish**.
- US2 depends on US1: `RemoveConfig` reuses the scan helper and the dry run, and `--apply` previews first.
- US3 needs US2's `RemoveConfig` runs to exist in the trail. Its projection tests use hand-built events, so they can be written as early as Phase 2, but they ship after US2.
- Within each phase, every test task comes before the implementation task that satisfies it (the T-numbers in brackets).
- **Merge order**: one PR per story (US1, US2, US3), each with its live check in the body. #65 closes with the US2 PR.

## Parallel Opportunities

- Foundational tests T001 to T005 are five independent files. The implementations T009, T010 and T011 are independent of each other; T007 and T008 come before T012.
- US1 tests T013 to T019 are seven independent files.
- US2 tests T027 to T033 are independent files, except T029, which extends US1's workflow test file.
- US3 tests T040 to T046 cover Python and TypeScript, and are independent.
- Polish T054 to T056 are independent.

## Implementation Strategy

1. **MVP = Phases 2 and 3.** The preview is useful on its own: it turns "undesired config exists" into exact deletes an operator can run by hand. It carries no write risk.
2. **US2** adds the write, which is what closes #65. It ships only after US1's guard and planner have been seen agreeing on the live device.
3. **US3** makes removals visible in history.
4. Each story's PR carries its own lab evidence (constitution III), recorded in evidence.md.
