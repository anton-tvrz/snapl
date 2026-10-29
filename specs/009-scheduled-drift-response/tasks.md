# Tasks: Scheduled Drift Scans and Opt-In Remediation

**Input**: Design documents from `/specs/009-scheduled-drift-response/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/orchestrator-schedules.md, contracts/cli-schedule.md, quickstart.md

**Tests**: TDD is mandatory per CLAUDE.md and the constitution. Every phase lists its tests first, and each test is run and seen to fail before the code it covers is written.

**Organization**: Tasks are grouped by user story (US1 to US3). The stand-down story is deferred to #136 and has no tasks here.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1, US2 or US3 from spec.md
- Exact file paths in every description

---

## Phase 1: Setup

**Purpose**: Dependency floor. There is no new package and no new directory.

- [x] T001 Raise `temporalio>=1.7` to `temporalio>=1.25` in packages/orchestrator/pyproject.toml (research R1), then run `uv lock` and confirm uv.lock still resolves 1.25.0

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Models, the not-found exception, the pure eligibility rule and the pure schedule builder. Every story depends on these, and none of them needs Temporal.

### Tests (write first, verify they fail)

- [x] T002 [P] Extend tests/unit/test_orchestrator/test_models.py with:
  - `DriftResponseMode` (`report`, `remediate`), `ExclusionReason` (`undesired_only`, `errored`) and `ScheduleChange` (`created`, `interval_changed`, `response_changed`, `paused`, `resumed`, `triggered`, `deleted`) values;
  - `AuditEventType.SCHEDULE_CHANGED` and `DRIFT_RESPONSE` exist;
  - a `SCHEDULE_CHANGED` event without `actor`, or with a `payload["change"]` that isn't a `ScheduleChange` value, is rejected;
  - `drift_schedule_id("dcfabric") == "drift-response-dcfabric"`;
  - `DriftResponseRequest` rejects a `schedule_id` that doesn't match `drift_schedule_id(use_case_id)` and rejects an empty use case;
  - `DriftResponseResult` invariants from data-model.md: REPORT ⇒ nothing remediated, excluded or reconciled; `reconcile is None` ⇔ `remediated == []`; `reconcile.total == len(remediated)`; remediated and excluded are disjoint and ⊆ `scan.reports`; remediated ids are DRIFTED with at least one forward item; `ended_at >= started_at`;
  - `RunSummary` (`wrote` false for a report run; true when reconcile succeeded or failed > 0; false when everything was skipped), `ScheduledRunInfo` and `DriftScheduleView` are frozen and forbid extra fields.
- [x] T003 [P] Write tests/unit/test_orchestrator/test_remediation.py for `select_remediation(scan)`. Cover:
  - CLEAN devices are in neither result;
  - an ERROR device is excluded as `ERRORED`;
  - a DRIFTED device with only `undesired=True` items is excluded as `UNDESIRED_ONLY`;
  - a DRIFTED device with mixed items is remediated;
  - remediated ids keep scan order;
  - an empty scan gives `([], {})`.
- [x] T004 [P] Write the pure half of tests/unit/test_orchestrator/test_schedules.py for `build_drift_schedule`. Assert:
  - the interval spec equals the given interval;
  - overlap is `ScheduleOverlapPolicy.SKIP`;
  - `catchup_window` equals the interval (not the SDK's 365-day default);
  - `pause_on_failure` is False;
  - the action is `ScheduleActionStartWorkflow` for `"DriftResponse"`, with id `drift-response-<uc>`, the given task queue and a single `DriftResponseRequest` argument in the given mode;
  - `static_summary` is `"drift response: report"`;
  - `state.paused` is False;
  - an interval below `MIN_SCAN_INTERVAL` (1 minute) raises `ValueError`;
  - `DEFAULT_SCAN_INTERVAL` is 5 minutes.

### Implementation

- [x] T005 Add the new types and audit invariant to packages/orchestrator/snapl_orchestrator/models.py:
  - `DriftResponseMode`, `ExclusionReason`, `ScheduleChange`;
  - the two `AuditEventType` members, and the `SCHEDULE_CHANGED` rule in `AuditEvent._check_invariants`;
  - `drift_schedule_id()`;
  - `DriftResponseRequest`, `DriftResponseResult`, `RunSummary`, `ScheduledRunInfo`, `DriftScheduleView`, frozen with `extra="forbid"` and runtime type imports marked `# noqa: TC003` (research R12).
- [x] T006 [P] Add `ScheduleNotFoundError(OrchestratorError)` to packages/orchestrator/snapl_orchestrator/exceptions.py
- [x] T007 [P] Implement `select_remediation` (pure, no Temporal import) in packages/orchestrator/snapl_orchestrator/remediation.py
- [x] T008 Create packages/orchestrator/snapl_orchestrator/schedules.py with `DEFAULT_SCAN_INTERVAL`, `MIN_SCAN_INTERVAL`, `schedule_id_for` (re-export of `models.drift_schedule_id`) and `build_drift_schedule` per contracts/orchestrator-schedules.md

**Checkpoint**: `uv run pytest tests/unit/test_orchestrator/test_models.py test_remediation.py test_schedules.py` is green, with no Temporal server.

---

## Phase 3: User Story 1: The Fabric Is Watched Without Anyone Asking (P1) 🎯 MVP

**Goal**: A report-mode schedule scans the use case on an interval, records findings attributed to the schedule, and writes nothing. `invoke demo.up` creates it.

**Independent Test**: With a report schedule (created by `demo.up` or `snapl schedule create`), drift a spine by hand, wait one interval, and check:
- `snapl schedule show` lists the run with its summary and `wrote: no`;
- `snapl audit --device spine-01` shows a `drift_response` event from `schedule:drift-response-dcfabric`;
- the device is still drifted.

### Tests (write first, verify they fail)

- [x] T009 [P] [US1] Write tests/unit/test_orchestrator/test_workflow_drift_response.py covering the report path. Use `WorkflowEnvironment.start_time_skipping`, `build_workflow_runner()`, the real `ScanDriftWorkflow` child with mocked activities and a recording `record_audit_event`, as in test_workflow_scan_drift.py. Cover:
  - a REPORT request runs exactly one ScanDrift child (`<wf_id>-scan`) and no ReconcileDevices child;
  - the result has the scan, empty remediated and excluded, and `reconcile=None`;
  - the audit gets `WORKFLOW_STARTED` and `WORKFLOW_TERMINATED` with `actor="schedule:drift-response-<uc>"`, plus one use-case-level `DRIFT_RESPONSE` with `mode`, `scan_workflow_id` and the clean, drifted and errored counts;
  - when the scan child fails outright, `WORKFLOW_TERMINATED` is recorded with failure and `INTENT_UNAVAILABLE` and the run re-raises;
  - a cancelled run records `WORKFLOW_CANCELLED`.
- [x] T010 [P] [US1] Extend tests/unit/test_orchestrator/test_schedules.py with a fake `Client` and `ScheduleHandle`, covering:
  - `ensure_drift_schedule` creates in REPORT and records one `SCHEDULE_CHANGED` `created` event (`interval_seconds`, `mode`, actor);
  - on `ScheduleAlreadyRunningError` it updates only `spec` and `policy.catchup_window`, keeping the existing action (mode) and `state` (paused, note) byte for byte, and records `interval_changed` (`from_seconds`, `to_seconds`);
  - an unchanged interval records nothing;
  - `describe_drift_schedule` decodes the mode from the action args through `client.data_converter`;
  - it maps interval, paused and note, the next three runs, the last five recent runs and `skipped_overlap`;
  - it gives completed runs a `RunSummary` built from the fetched `DriftResponseResult`;
  - a run whose result fetch fails keeps its status with `summary=None`;
  - a missing schedule (RPC NOT_FOUND) raises `ScheduleNotFoundError`;
  - `find_drift_schedule` returns None for a missing schedule.
- [x] T011 [P] [US1] Extend tests/unit/test_orchestrator/test_worker_run.py: `DriftResponseWorkflow` is registered, the activity list is unchanged, and starting the worker makes no schedule call
- [ ] T012 [P] [US1] Write tests/unit/test_presentation/test_cli_schedule.py for `snapl schedule create` and `show`, with mocked `schedules` functions, covering:
  - `--every` parses `90s`, `5m` and `1h`, and rejects `abc` and `30s` with one line and exit 1;
  - a SoT that identifies as FOREIGN is refused before any Temporal call (FR-031), and UNKNOWN is not refused;
  - a missing audit DB is refused before any Temporal call, with the `SNAPL_AUDIT_DB` hint;
  - the actor is `getpass.getuser()`;
  - with no poller, a warning goes to stderr and the exit code is still 0;
  - `show --json` puts only valid JSON on stdout;
  - `show` for a missing schedule exits 1 with one line;
  - no schedule command calls `_require_worker`.
- [ ] T013 [P] [US1] Extend tests/unit/test_presentation/test_render.py for the schedule view in human form (contract layout, a completed run line `6 devices: 5 clean, 1 drifted, 0 errored   wrote: no`, a running run line shows the workflow id) and JSON form (interval in seconds, ISO 8601 UTC datetimes)
- [ ] T014 [P] [US1] Extend tests/unit/test_tasks/test_demo.py, covering:
  - `up` calls `ensure_drift_schedule` after `seed` and before `check`, with the seeded use case, `DEFAULT_SCAN_INTERVAL` and actor `invoke:demo.up (<user>)`;
  - `up` opens the audit log at `SNAPL_AUDIT_DB` (default `./snapl-audit.sqlite`) and may create it;
  - a foreign SoT stops `up` at `seed`, with no schedule call;
  - `check` adds a `drift schedule` row: it passes when the schedule is active, fails with "none — run: uv run invoke demo.up" when missing, fails with the reason when paused, and is skipped when `temporal reachable` failed.

### Implementation

- [x] T015 [US1] Implement `DriftResponseWorkflow` (report path, lifecycle audit, scan-failure and cancellation handling; imports under `workflow.unsafe.imports_passed_through()`) in packages/orchestrator/snapl_orchestrator/workflows/drift_response.py
- [x] T016 [US1] Register `DriftResponseWorkflow` in packages/orchestrator/snapl_orchestrator/worker/run.py
- [x] T017 [US1] Implement `ensure_drift_schedule`, `describe_drift_schedule` (including the `RunSummary` fetch) and `find_drift_schedule` in packages/orchestrator/snapl_orchestrator/schedules.py
- [ ] T018 [US1] Render `DriftScheduleView` (human and JSON) in packages/presentation/snapl_presentation/render.py
- [ ] T019 [US1] Add the `schedule` Typer sub-app with `create` and `show` in packages/presentation/snapl_presentation/schedule_cli.py, and mount it in packages/presentation/snapl_presentation/cli.py. Factor the audit-DB open-and-refuse logic out of `audit()` into a shared helper, so `audit` and every mutating schedule command use one refusal.
- [ ] T020 [US1] In tasks/demo.py, make `up` call `ensure_drift_schedule` after `seed`, and add the `drift schedule` row to `check`

**Checkpoint**: US1 is shippable. Live evidence item 1 (quickstart.md): 3 or more report runs against a drifted spine, with the device unchanged and no `apply_config` in the worker log.

---

## Phase 4: User Story 2: Control the Schedule From the Command Line (P2)

**Goal**: Pause with a reason (suppress), resume, trigger, delete, and see the schedule in `snapl status`. Every change is audited with its actor.

**Independent Test**: Against mocked schedule functions, each command requests the right change, prints one line, exits 0 or 1, and leaves exactly one audit record.

### Tests (write first, verify they fail)

- [ ] T021 [P] [US2] Extend tests/unit/test_orchestrator/test_schedules.py, covering:
  - `pause_drift_schedule` rejects an empty or whitespace reason, calls `pause(note=reason)` and records `paused` with the reason;
  - `resume_drift_schedule` records `resumed`, with the optional note;
  - `trigger_drift_schedule` returns the current mode and records `triggered`;
  - `delete_drift_schedule` records `deleted`;
  - each of them raises `ScheduleNotFoundError` for a missing schedule;
  - no audit record is written when the Temporal call raises.
- [ ] T022 [P] [US2] Extend tests/unit/test_presentation/test_cli_schedule.py, covering:
  - `pause` requires `--reason`;
  - `resume` takes an optional `--note`;
  - `trigger` on a report schedule doesn't prompt and prints the workflow-id prefix;
  - `delete` prompts unless `--yes`, and an aborted prompt exits 1 with no call;
  - a missing schedule exits 1 for every command.
- [ ] T023 [P] [US2] Extend tests/unit/test_presentation/test_cli.py: `snapl status` shows `drift schedule  dcfabric: every 5m, report, active`, `none — snapl schedule create`, or `could not inspect: <first line>`. The row is always ok, and the exit code is unchanged in all three cases.
- [ ] T024 [P] [US2] Extend tests/unit/test_presentation/test_render.py: in the human audit timeline, `schedule_changed` events show `payload["change"]` (with from→to where present) and `drift_response` events show `payload["decision"]` in the outcome column

### Implementation

- [ ] T025 [US2] Implement `pause_drift_schedule`, `resume_drift_schedule`, `trigger_drift_schedule` and `delete_drift_schedule` in packages/orchestrator/snapl_orchestrator/schedules.py
- [ ] T026 [US2] Add the `pause`, `resume`, `trigger` (report path) and `delete` commands in packages/presentation/snapl_presentation/schedule_cli.py
- [ ] T027 [US2] Add the `drift schedule` status row using `find_drift_schedule` in packages/presentation/snapl_presentation/cli.py
- [ ] T028 [US2] Render the new audit event payloads in packages/presentation/snapl_presentation/render.py

**Checkpoint**: Quickstart step 2 works end to end against the dev stack.

---

## Phase 5: User Story 3: Opt In to Self-Healing for a Use Case (P2)

**Goal**: `snapl schedule mode remediate`, explicitly confirmed and audited, makes each run reconcile only the devices with forward drift.

**Independent Test**: In a remediate run against a mocked fabric with one forward-drifted device, one undesired-only device and one errored device, only the first is reconciled, and the other two are recorded as excluded with their reasons.

### Tests (write first, verify they fail)

- [ ] T029 [P] [US3] Extend tests/unit/test_orchestrator/test_workflow_drift_response.py with the remediate path, covering:
  - a mixed fabric runs ReconcileDevices (`<wf_id>-reconcile`) with exactly the forward-drifted ids, in scan order;
  - each excluded device gets one device-level `DRIFT_RESPONSE` with its `decision`, `scan_workflow_id` and `paths`;
  - each remediated device gets one with `reconcile_workflow_id`;
  - a clean, or only undesired and errored, fabric starts no reconcile child;
  - a reconcile with `failed > 0` is returned in the result and the run still terminates with success, with no schedule call (stand-down deferred, #136);
  - an in-flight skip is not a failure;
  - the use-case-level `DRIFT_RESPONSE` carries the remediated count, excluded counts by reason, and `reconcile_workflow_id`.
- [ ] T030 [P] [US3] Extend tests/unit/test_orchestrator/test_schedules.py, covering:
  - `set_drift_response` rebuilds the action with the new mode and `static_summary`, preserving `spec`, `policy` and `state`;
  - it records `response_changed` (`from`, `to`), and an unchanged mode is a no-op with no record;
  - `ensure_drift_schedule` on an existing REMEDIATE schedule keeps REMEDIATE;
  - no public function creates a schedule in REMEDIATE.
- [ ] T031 [P] [US3] Extend tests/unit/test_presentation/test_cli_schedule.py, covering:
  - `mode remediate` prints the contract's confirmation text verbatim, with the interval and the device count from `load_states`, or `Devices: unknown (SoT unreachable)`;
  - it proceeds on `y` or `--yes` and aborts on `N` with exit 1;
  - `--json` without `--yes` is refused;
  - `mode report` never prompts;
  - `trigger` on a remediate schedule asks the same confirmation;
  - `show` renders `remediate` in the warning colour on a TTY, and a run that wrote renders `wrote: 1 device (1 ok, 0 failed)`.

### Implementation

- [ ] T032 [US3] Add the remediate path (`select_remediation`, the ReconcileDevices child, device-level audit) to packages/orchestrator/snapl_orchestrator/workflows/drift_response.py
- [ ] T033 [US3] Implement `set_drift_response` in packages/orchestrator/snapl_orchestrator/schedules.py
- [ ] T034 [US3] Add the `mode` command and the remediate confirmation for `mode` and `trigger` in packages/presentation/snapl_presentation/schedule_cli.py, and the remediate rendering in packages/presentation/snapl_presentation/render.py

**Checkpoint**: Live evidence items 2 and 3. Forward drift heals within two intervals, with the audit chain scan → reconcile → deploy. The `ethernet-1/7` undesired-only case starts no DeployIntent across 3 or more runs.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T035 Write tests/integration/test_orchestrator/test_schedules_local.py (`@pytest.mark.integration`, `WorkflowEnvironment.start_local()` with the pydantic converter). It does one round trip:
  - `ensure` → `describe` (mode report, interval);
  - `pause` → `describe` (paused, note);
  - `resume` → `trigger`;
  - `set_drift_response(REMEDIATE)` → `describe` (mode decodes back);
  - `delete` → `find` returns None.
- [ ] T036 [P] Confirm tests/unit/test_orchestrator/test_vendor_seams.py covers workflows/drift_response.py with no change, and that the whole unit suite passes (`uv run invoke test-unit`)
- [ ] T037 [P] Add the "The loop closes itself" scenario (quickstart steps 1 to 3) and a demo-arc variant in docs/demo-scenarios.md
- [ ] T038 [P] In README.md's known limitations, state that remediate is opt-in per use case, that scheduled runs never remove config (#65), and that a failed repair retries every run until #136
- [ ] T039 [P] Add changelog/99.added.md
- [ ] T040 Check ≥80% coverage on the new modules, then run `uv run invoke lint` and `uv run invoke format`
- [ ] T041 Live evidence items 4 to 6 on the dcfabric lab (overlap after worker downtime, bootstrap idempotency, persistence across a Temporal restart), recorded in the implementation PR

---

## Dependencies & Execution Order

- **Setup (T001)** → **Foundational (T002 to T008)** → **US1** → **US2** and **US3**, in either order → **Polish**.
- US2 and US3 both extend `schedules.py`, `schedule_cli.py` and `render.py`, so they are sequential in practice, but each is shippable on its own.
- Within each phase, every test task precedes the implementation task that satisfies it.

## Parallel Opportunities

- Foundational tests T002, T003 and T004 are independent files. So are T006 and T007.
- US1 tests T009 to T014 are six independent files and can all be written first, in parallel.
- Polish tasks T036 to T039 are independent.

## Implementation Strategy

1. **MVP = Phases 1 to 3.** Report-mode detection, created by `demo.up`, visible through `snapl schedule show` and the audit trail. It has no write risk, and it already closes the detection half of the loop.
2. **US2** adds operator control.
3. **US3** adds self-healing, last, as VIII.2 asks.
4. One implementation PR closes #99, with the quickstart's live-evidence list filled in.
