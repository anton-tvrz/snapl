# Tasks: NAF Orchestrator — Temporal Workflows

**Input**: Design documents from `/specs/005-orchestrator-temporal/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/orchestrator.md, quickstart.md

**Tests**: TDD is mandatory per CLAUDE.md and the constitution — "Always produce the test file first. This is a hard rule, not a suggestion." Test tasks are included for every phase.

**Organization**: Tasks are grouped by user story so each story can be implemented, tested, and delivered independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- Exact file paths included in all descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Package skeleton, dependencies, and test scaffolding

- [ ] T001 Update packages/orchestrator/pyproject.toml (name=snapl-orchestrator, python>=3.12, deps: temporalio>=1.7, pydantic>=2.5, aiosqlite>=0.19; workspace deps: snapl-intent, snapl-executor, snapl-collector, snapl-observability; verify snapl-orchestrator is already a workspace member in root pyproject.toml)
- [ ] T002 [P] Create package source directory structure with empty __init__.py files: packages/orchestrator/snapl_orchestrator/activities/, packages/orchestrator/snapl_orchestrator/workflows/, packages/orchestrator/snapl_orchestrator/audit/, packages/orchestrator/snapl_orchestrator/worker/
- [ ] T003 [P] Create test directory scaffolding with __init__.py: tests/unit/test_orchestrator/__init__.py and tests/integration/test_orchestrator/__init__.py
- [ ] T004 [P] Add shared orchestrator fixtures to tests/conftest.py — `make_audit_event` factory fixture returning an `AuditEvent` with sensible defaults; reuse existing `make_device` fixture from collector tests

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Models, exceptions, AuditLog ABC, and an in-memory AuditLog used by every workflow test. All user stories depend on this phase.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Tests (TDD — write first, verify they fail)

- [ ] T005 [P] Write unit tests for orchestrator Pydantic models (WorkflowResult: success==True ⇔ reason==SUCCEEDED; drift_items populated only for VERIFICATION_FAILED; ended_at >= started_at; frozen + extra="forbid"; DriftScanResult: clean+drifted+errored==total, len(reports)==total; ReconcileResult: succeeded+failed+skipped==total, len(device_results)+skipped==total; AuditEvent: activity_name required for ACTIVITY_* event types, reason required for WORKFLOW_TERMINATED, outcome ∈ {success,failure,cancelled,None}) in tests/unit/test_orchestrator/test_models.py
- [ ] T006 [P] Write unit tests for AuditLog ABC contract enforcement (cannot instantiate AuditLog directly; concrete subclass missing any abstract method raises TypeError; subclass implementing append + query_by_workflow + query_by_device + query_by_time_range can be instantiated) in tests/unit/test_orchestrator/test_audit_abc.py
- [ ] T007 [P] Write unit tests for InMemoryAuditLog (append stores the event; query_by_workflow filters by workflow_id and returns chronological order; query_by_device filters by target_id when target is UUID and returns chronological order; query_by_time_range filters by [start, end); list-copy semantics — caller mutations do not affect stored state; concurrent appends from multiple coroutines are serialised) in tests/unit/test_orchestrator/test_audit_inmemory.py

### Implementation

- [ ] T008 [P] Implement exception hierarchy (OrchestratorError base; OrchestratorConfigError for invalid worker config — missing TEMPORAL_HOST, bad SNAPL_AUDIT_DB; AuditLogError for persistence/query failures after retries) in packages/orchestrator/snapl_orchestrator/exceptions.py
- [ ] T009 [P] Implement orchestrator models (WorkflowReason and AuditEventType StrEnums; WorkflowResult / DriftScanResult / ReconcileResult / AuditEvent as Pydantic v2 frozen + extra="forbid" with model_validators enforcing every invariant in data-model.md) in packages/orchestrator/snapl_orchestrator/models.py
- [ ] T010 Implement AuditLog ABC (async append + query_by_workflow + query_by_device + query_by_time_range as @abstractmethod async def with docstrings exactly matching contracts/orchestrator.md) in packages/orchestrator/snapl_orchestrator/audit/abc.py
- [ ] T011 Implement InMemoryAuditLog (asyncio.Lock-guarded list[AuditEvent]; query methods return sorted copies by timestamp; conforms to the ABC; used by unit tests as the default test double) in packages/orchestrator/snapl_orchestrator/audit/memory.py

**Checkpoint**: Foundation ready — models, exceptions, AuditLog ABC, and InMemoryAuditLog tested and passing. Workflow + activity work can begin.

---

## Phase 3: User Story 1 — Deploy Intended State End-to-End (Priority: P1) 🎯 MVP

**Goal**: `DeployIntentWorkflow.run(device_id)` durably executes fetch_intent → apply → collect → verify → audit, returns a `WorkflowResult` with a specific `WorkflowReason`, survives worker restart, and retries transient activity failures.

**Independent Test**: Run the workflow against `WorkflowEnvironment.start_time_skipping()` with all five activities mocked. Verify the workflow calls them in order, records audit events at every step, returns SUCCEEDED on the happy path, returns VERIFICATION_FAILED when post-apply drift is non-CLEAN, returns APPLY_FAILED when the apply activity exhausts retries, and survives a `worker.shutdown()` mid-workflow without losing progress.

### Tests (TDD — write first, verify they fail)

- [ ] T012 [P] [US1] Write unit tests for each activity wrapping a downstream block — fetch_desired_state (mocked IntentStore returns DesiredState → activity returns same; IntentStore raises IntentStoreNotFound → activity surfaces it as non-retryable error type), apply_config (mocked Executor.apply returns ApplyResult → activity returns same), collect_running_state (paths empty → calls get_running_config; paths non-empty → calls collect; mocked Collector returns CollectResult → activity returns same), detect_drift (mocked Observer.detect_drift returns DriftReport → activity returns same), record_audit_event (calls InMemoryAuditLog.append; AuditLogError surfaces from append) — one test file per activity in tests/unit/test_orchestrator/test_activity_intent.py, test_activity_executor.py, test_activity_collector.py, test_activity_observability.py, test_activity_audit.py
- [ ] T013 [P] [US1] Write unit tests for DeployIntentWorkflow (happy path: all activities succeed, DriftReport.status=CLEAN → WorkflowResult success=True reason=SUCCEEDED; intent fetch fails non-retryable → reason=INTENT_UNAVAILABLE no later activity runs; apply fails after retries → reason=APPLY_FAILED no collect/verify runs; verification yields DRIFTED → reason=VERIFICATION_FAILED drift_items populated; verification yields ERROR → reason=COLLECT_FAILED; audit event recorded at workflow_started, each activity_started/completed/failed, workflow_terminated; ended_at >= started_at) — all using `WorkflowEnvironment.start_time_skipping()` and mocked activities — in tests/unit/test_orchestrator/test_workflow_deploy_intent.py

### Implementation

- [ ] T014 [P] [US1] Implement Activities dependency container (@dataclass with intent_store: IntentStore, executor: Executor, collector: Collector, observer: Observer, audit_log: AuditLog; module-level `_activities` reference set by worker bootstrap; getter raises OrchestratorConfigError if not initialised) in packages/orchestrator/snapl_orchestrator/activities/__init__.py
- [ ] T015 [P] [US1] Implement fetch_desired_state activity (@activity.defn; resolves activities container; calls intent_store; raises wrapped exception with non_retryable types declared via temporalio) in packages/orchestrator/snapl_orchestrator/activities/intent.py
- [ ] T016 [P] [US1] Implement apply_config activity (@activity.defn; resolves activities container; calls executor.apply; returns ApplyResult) in packages/orchestrator/snapl_orchestrator/activities/executor.py
- [ ] T017 [P] [US1] Implement collect_running_state activity (@activity.defn; if paths empty → executor.get_running_config; else → collector.collect(device, paths); returns CollectResult) in packages/orchestrator/snapl_orchestrator/activities/collector.py
- [ ] T018 [P] [US1] Implement detect_drift activity (@activity.defn; calls observer.detect_drift(desired, collected); returns DriftReport; single-attempt retry policy applied at workflow boundary) in packages/orchestrator/snapl_orchestrator/activities/observability.py
- [ ] T019 [P] [US1] Implement record_audit_event activity (@activity.defn; calls audit_log.append(event); retries up to 5 with 1s backoff applied at workflow boundary) in packages/orchestrator/snapl_orchestrator/activities/audit.py
- [ ] T020 [US1] Implement DeployIntentWorkflow (@workflow.defn name="DeployIntent"; @workflow.run async def run(self, device_id) → WorkflowResult; record WORKFLOW_STARTED audit event then run fetch_desired_state → apply_config → collect_running_state(device, paths=apply.paths) → detect_drift; map DriftReport.status to terminal reason; on each activity outcome record ACTIVITY_* audit event; on cancellation catch CancelledError, write WORKFLOW_CANCELLED audit event, return WorkflowResult(reason=CANCELLED); always record WORKFLOW_TERMINATED with terminal reason; use start_to_close_timeout=30s and per-activity retry policies from research R4 with non_retryable_error_types where applicable) in packages/orchestrator/snapl_orchestrator/workflows/deploy_intent.py
- [ ] T021 [US1] Update packages/orchestrator/snapl_orchestrator/__init__.py with placeholder public exports (WorkflowResult, WorkflowReason, AuditEvent, AuditEventType, AuditLog ABC) — full exports finalised in T037

**Checkpoint**: US1 unit tests pass. DeployIntentWorkflow runs against `WorkflowEnvironment` with mocked activities, terminates with the correct WorkflowReason for every code path in the spec, records audit events at every boundary, and resumes correctly across `worker.shutdown()` (SC-003).

---

## Phase 4: User Story 2 — Scan Drift Across a Fabric and Trigger Reconciliation (Priority: P2)

**Goal**: `ScanDriftWorkflow.run(use_case_id)` evaluates drift across every device in a use case and returns a `DriftScanResult`. `ReconcileDevicesWorkflow.run(device_ids)` composes per-device `DeployIntentWorkflow` as child workflows and returns a `ReconcileResult`. Drift findings never trigger automatic remediation — reconciliation is a separate explicit invocation.

**Independent Test**: ScanDrift — three mocked devices, one drifted → DriftScanResult total=3 clean=2 drifted=1 errored=0 with the drifted device's report carrying the diverging path. ReconcileDevices — two device IDs, one missing in SoT → ReconcileResult total=2 succeeded=1 skipped=1.

### Tests (TDD — write first, verify they fail)

- [ ] T022 [P] [US2] Write unit tests for ScanDriftWorkflow (three devices, all clean → DriftScanResult clean=3 drifted=0 errored=0; one device drifted → drifted=1 and report identifies the path; one device collection ERROR → errored=1; per-device evaluation fan-out is concurrent — assert via mock call ordering not being strictly sequential; WORKFLOW_TERMINATED audit event recorded with reason=SUCCEEDED) in tests/unit/test_orchestrator/test_workflow_scan_drift.py
- [ ] T023 [P] [US2] Write unit tests for ReconcileDevicesWorkflow (two device IDs both succeed → ReconcileResult succeeded=2 failed=0 skipped=0; one device DeployIntent returns VERIFICATION_FAILED → failed=1; one device_id missing from SoT — fetch_desired_state raises non-retryable → skipped=1 device_results excludes that ID; empty device_ids list → ValueError raised at workflow start before any child workflow scheduled; child workflows use deterministic ID `deploy-intent-{device_id}` with USE_EXISTING) in tests/unit/test_orchestrator/test_workflow_reconcile_devices.py

### Implementation

- [ ] T024 [US2] Implement ScanDriftWorkflow (@workflow.defn name="ScanDrift"; activity to fetch devices for use_case_id from intent_store; asyncio.gather fan-out of per-device fetch_desired_state + collect_running_state + detect_drift activities; aggregate DriftReport[] into DriftScanResult; record WORKFLOW_STARTED and WORKFLOW_TERMINATED audit events) in packages/orchestrator/snapl_orchestrator/workflows/scan_drift.py
- [ ] T025 [US2] Implement ReconcileDevicesWorkflow (@workflow.defn name="ReconcileDevices"; validate non-empty device_ids and raise ValueError if empty at @workflow.run entry; for each device_id execute_child_workflow(DeployIntentWorkflow.run, device_id, id=f"deploy-intent-{device_id}", id_conflict_policy=USE_EXISTING) concurrently via asyncio.gather; classify per-device WorkflowResult.success into succeeded/failed; device_not_found path increments skipped without a device_results entry; record WORKFLOW_STARTED and WORKFLOW_TERMINATED audit events linking back to scan if invoked from a scan workflow) in packages/orchestrator/snapl_orchestrator/workflows/reconcile_devices.py
- [ ] T026 [US2] Extend the fetch activity layer with a `fetch_devices_for_use_case` activity (@activity.defn; calls intent_store.list_devices(use_case_id)) in packages/orchestrator/snapl_orchestrator/activities/intent.py (append)

**Checkpoint**: US2 complete. ScanDrift evaluates fabrics, ReconcileDevices re-applies intent to chosen devices via child workflows with per-device serialization. No automatic remediation is triggered by scan findings (FR-005 verified).

---

## Phase 5: User Story 3 — Durable Audit Log (Priority: P2)

**Goal**: `SqliteAuditLog` provides a file-backed, append-only, queryable audit store that survives worker restarts. Replaces InMemoryAuditLog in the worker bootstrap; existing workflow code unchanged because both implement the same ABC.

**Independent Test**: Append five events across two workflow IDs and one device ID; close the connection; re-open against the same database file; assert all five events are retrievable via every query method and chronological order is preserved. Attempt to mutate a retrieved event — assert it is frozen.

### Tests (TDD — write first, verify they fail)

- [ ] T027 [P] [US3] Write unit tests for SqliteAuditLog (in-memory SQLite via ":memory:"; initialize creates table + indexes; append persists event; query_by_workflow returns events for matching workflow_id chronological; query_by_device returns events whose target_id matches UUID across all workflows; query_by_time_range returns events with start <= timestamp < end; UNIQUE on event_id rejects duplicate inserts; appending then re-opening the connection against a file-backed database returns previously persisted events — durability across "process restart") in tests/unit/test_orchestrator/test_audit_sqlite.py

### Implementation

- [ ] T028 [US3] Create SQLite DDL (CREATE TABLE IF NOT EXISTS audit_events with id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE, workflow_id, workflow_type, target_id, event_type, activity_name, outcome, reason, payload_json, timestamp; indexes on workflow_id, target_id, timestamp) in packages/orchestrator/snapl_orchestrator/audit/schema.sql
- [ ] T029 [US3] Implement SqliteAuditLog (aiosqlite async connection in WAL journal mode; initialize() reads schema.sql and executes; append() with asyncio.Lock around the single write connection; query_*() methods open short-lived read connections; payload serialised via json.dumps(model.payload); rows deserialised back into frozen AuditEvent pydantic models; no UPDATE or DELETE methods exposed; SQLite errors wrapped as AuditLogError after retry) in packages/orchestrator/snapl_orchestrator/audit/sqlite.py

**Checkpoint**: US3 complete. Durable audit log is persistent across process restarts and supports all three query patterns required by FR-007. Workflow code is unchanged — the swap is at worker bootstrap.

---

## Phase 6: User Story 4 — Inspect and Manage In-Flight Workflows (Priority: P3)

**Goal**: Operators can list running workflows and cancel them. Cancellation records a `WORKFLOW_CANCELLED` audit event and the workflow returns `WorkflowResult(reason=CANCELLED)` after a brief, bounded cleanup window.

**Independent Test**: Start a DeployIntentWorkflow with an apply activity that awaits a slow signal. Query `WorkflowEnvironment.client.list_workflows()` and assert the workflow appears with status `RUNNING`. Invoke `handle.cancel()`; await the workflow result; assert `WorkflowResult.success=False reason=CANCELLED` and the durable audit log contains a `WORKFLOW_CANCELLED` event.

### Tests (TDD — write first, verify they fail)

- [ ] T030 [P] [US4] Write unit tests for workflow cancellation behaviour (start DeployIntentWorkflow with a long-running apply activity mock; invoke handle.cancel(); workflow returns WorkflowResult reason=CANCELLED; WORKFLOW_CANCELLED audit event was recorded before the workflow returned; apply activity received CancelledError and cleaned up; activity cancellation type is WAIT_CANCELLATION_COMPLETED) in tests/unit/test_orchestrator/test_workflow_cancellation.py
- [ ] T031 [P] [US4] Write unit tests for the workflow-list helper (orchestrator.client.list_running_workflows(client, task_queue) → returns workflows whose ExecutionStatus is RUNNING with workflow_id, workflow_type, start_time; empty when none running) in tests/unit/test_orchestrator/test_worker_client.py

### Implementation

- [ ] T032 [US4] Implement Temporal client helpers (build_client(temporal_host, namespace) factory; list_running_workflows(client, task_queue) wrapper around client.list_workflows() with status filter) in packages/orchestrator/snapl_orchestrator/worker/client.py
- [ ] T033 [US4] Extend each workflow with cancellation handling (top-level try/except CancelledError that records WORKFLOW_CANCELLED audit event before re-raising or returning WorkflowResult(reason=CANCELLED); apply same pattern to DeployIntent, ScanDrift, ReconcileDevices) in packages/orchestrator/snapl_orchestrator/workflows/deploy_intent.py (extend) and similarly in scan_drift.py and reconcile_devices.py

**Checkpoint**: US4 complete. Workflows can be listed and cancelled cleanly; cancellation is recorded durably.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Worker entry point, invoke task, integration tests, performance assertions, exports, lint, validation.

- [ ] T034 Implement worker entry point (worker/run.py: build Activities from env-configured concrete InfrahubIntentStore, GnmiExecutor, GnmiCollector, StructuralObserver, SqliteAuditLog; await SqliteAuditLog.initialize(); register all three workflows and all six activities on a single Worker bound to TEMPORAL_TASK_QUEUE; run forever; surface OrchestratorConfigError on missing env vars) in packages/orchestrator/snapl_orchestrator/worker/run.py
- [ ] T035 [P] Add `invoke orchestrator.start` task that imports and runs worker.run.run_worker() with env vars (TEMPORAL_HOST, TEMPORAL_NAMESPACE, TEMPORAL_TASK_QUEUE, SNAPL_AUDIT_DB, plus downstream block env vars) in tasks/orchestrator.py (and register module in tasks/__init__.py or tasks.py at repo root — match existing pattern)
- [ ] T036 [P] Create integration conftest.py for orchestrator tests (TEMPORAL_HOST/TEMPORAL_NAMESPACE/TEMPORAL_TASK_QUEUE env vars with localhost defaults; skip_if_temporal_unreachable session-scoped fixture probing TCP socket on temporal frontend; reuse SRLINUX_* env vars and skip_if_unreachable from collector tests; build_temporal_client() fixture; tmp-file SqliteAuditLog fixture) in tests/integration/test_orchestrator/conftest.py
- [ ] T037 [P] Write end-to-end integration test deploy_intent_live (live Temporal + live SR Linux + live Infrahub: build all concrete dependencies, start a worker in a background task, invoke DeployIntentWorkflow.run(spine_device_id) via client.execute_workflow with id=f"deploy-intent-{spine_device_id}" and id_conflict_policy=USE_EXISTING; assert WorkflowResult.success=True; query SqliteAuditLog by workflow_id and assert all four activity events plus WORKFLOW_STARTED + WORKFLOW_TERMINATED present; assert ended_at - started_at < 60s for SC-001) in tests/integration/test_orchestrator/test_deploy_intent_live.py
- [ ] T038 [P] Add SC-002 performance assertion to integration tests (scan_drift across 12-device dcfabric completes in <3 minutes; measure via time.monotonic()) in tests/integration/test_orchestrator/test_deploy_intent_live.py (append) or new test_scan_drift_live.py
- [ ] T039 [P] Add SC-003 durability assertion (start a DeployIntentWorkflow, simulate worker shutdown mid-workflow via `worker.shutdown()` cooperative cancellation or environment-level kill, restart the worker, assert workflow resumes from last completed activity by inspecting audit events — no duplicate apply event in the SqliteAuditLog) in tests/integration/test_orchestrator/test_deploy_intent_live.py (append)
- [ ] T040 Finalise packages/orchestrator/snapl_orchestrator/__init__.py exports (DeployIntentWorkflow, ScanDriftWorkflow, ReconcileDevicesWorkflow, WorkflowResult, DriftScanResult, ReconcileResult, AuditEvent, AuditEventType, WorkflowReason, AuditLog, InMemoryAuditLog, SqliteAuditLog, OrchestratorError, OrchestratorConfigError, AuditLogError) in packages/orchestrator/snapl_orchestrator/__init__.py
- [ ] T041 [P] Verify lint and format clean: uv run invoke lint && uv run invoke format — zero errors
- [ ] T042 Verify all unit tests pass with ≥80% coverage: uv run pytest tests/unit/test_orchestrator/ -m unit -v --cov=snapl_orchestrator
- [ ] T043 Add changelog fragment in changelog/ (e.g., `9.added.md`: "NAF Orchestrator block — Temporal workflows for durable, retryable, auditable deploy / drift-scan / reconcile with append-only SQLite audit log")
- [ ] T044 Run quickstart.md validation end-to-end against live stack (deploy → scan → reconcile → audit query → cancel; verify every snippet works as written; update quickstart.md with any drift from the implemented API surface)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately.
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories.
- **US1 (Phase 3)**: Depends on Phase 2. Activities + DeployIntentWorkflow are the core; everything else composes them.
- **US2 (Phase 4)**: Depends on Phase 3. ReconcileDevicesWorkflow uses DeployIntentWorkflow as a child; ScanDrift reuses the same activities.
- **US3 (Phase 5)**: Depends on Phase 2 (AuditLog ABC). Can run in parallel with Phase 3 — the workflow code is unchanged whether the AuditLog is in-memory or SQLite.
- **US4 (Phase 6)**: Depends on Phase 3. Cancellation must hook into existing workflow code.
- **Polish (Phase 7)**: Depends on all user stories. Integration tests exercise the live stack end-to-end.

### User Story Dependencies

- **US1 (P1)** is the foundation — DeployIntent is the workflow that US2 and US4 build on.
- **US2 (P2)** uses DeployIntent as a child workflow for reconcile; ScanDrift reuses the per-device activities but is read-only.
- **US3 (P2)** is fully orthogonal to the workflow phases — swaps InMemoryAuditLog for SqliteAuditLog at the worker bootstrap. Same ABC, no workflow-code changes.
- **US4 (P3)** layers cancellation onto existing workflows from US1 and US2.

### Within Each User Story

1. Tests MUST be written and FAIL before implementation (TDD mandate).
2. Activities before workflows (workflow tests call activity mocks).
3. Workflow code is deterministic — no direct IO; only `workflow.execute_activity` / `workflow.execute_child_workflow`.
4. Cancellation hooks are added to existing workflows in US4 — not retrofitted as a parallel refactor.

### Parallel Opportunities

**Phase 1**: T002, T003, T004 in parallel after T001.

**Phase 2**: T005, T006, T007 in parallel (tests for different modules). T008, T009, T010, T011 mostly sequential within the audit/ module but T008 and T009 can run in parallel.

**Phase 3**: T012, T013 in parallel (tests). T014–T019 in parallel (each activity is a separate file). T020 depends on T014–T019. T021 is the final exports update.

**Phase 4**: T022, T023 in parallel (tests). T024, T025 sequential because reconcile uses DeployIntent; T026 can run in parallel with T024.

**Phase 5**: T027 (test) → T028 (DDL) → T029 (impl).

**Phase 6**: T030, T031 in parallel (tests). T032, T033 in parallel (different files).

**Phase 7**: T035, T036, T037, T038, T039, T041, T043 in parallel where they touch different files. T034 → T040 → T042 → T044 sequential.

---

## Parallel Example: User Story 1

```bash
# All US1 unit tests in parallel (TDD — must fail first):
Task: T012 "Activity unit tests across five files in tests/unit/test_orchestrator/test_activity_*.py"
Task: T013 "DeployIntentWorkflow unit tests in test_workflow_deploy_intent.py"

# Then activities in parallel (separate files, no cross-dependencies):
Task: T014 "Activities container in activities/__init__.py"
Task: T015 "fetch_desired_state activity in activities/intent.py"
Task: T016 "apply_config activity in activities/executor.py"
Task: T017 "collect_running_state activity in activities/collector.py"
Task: T018 "detect_drift activity in activities/observability.py"
Task: T019 "record_audit_event activity in activities/audit.py"

# Finally the workflow that composes them all:
Task: T020 "DeployIntentWorkflow in workflows/deploy_intent.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001–T004).
2. Complete Phase 2: Foundational (T005–T011) — models, ABC, InMemoryAuditLog.
3. Complete Phase 3: US1 (T012–T021) — DeployIntentWorkflow with all five activities, exercised via `WorkflowEnvironment`.
4. **STOP and VALIDATE**: deploy_intent works end-to-end against mocked activities and survives `worker.shutdown()` mid-workflow.
5. Optionally bring in T036–T037 to validate against the live stack before proceeding.

### Incremental Delivery

1. Setup + Foundational → models, ABC, InMemoryAuditLog ready.
2. US1 → DeployIntent end-to-end with in-memory audit (MVP — first measurable value).
3. US3 → durable SqliteAuditLog (swappable via the ABC; no workflow-code changes).
4. US2 → ScanDrift + ReconcileDevices (closes the operational loop: detect, then remediate).
5. US4 → introspect/cancel (operability).
6. Polish → worker entry, invoke task, integration tests, performance assertions, lint.

### Single-Developer Strategy (Recommended)

1. Phase 1 (T001–T004) — package skeleton.
2. Phase 2 (T005–T011) — write tests first, verify fail; then impl. Run `uv run pytest tests/unit/test_orchestrator/test_models.py tests/unit/test_orchestrator/test_audit_abc.py tests/unit/test_orchestrator/test_audit_inmemory.py -m unit -v` after each tranche.
3. Phase 3 US1 (T012–T021) — activity tests + workflow test first; then activities; then workflow; then exports.
4. Phase 5 US3 (T027–T029) — durable audit log, no workflow change required.
5. Phase 4 US2 (T022–T026) — scan + reconcile.
6. Phase 6 US4 (T030–T033) — cancellation.
7. Phase 7 Polish (T034–T044) — worker entry, integration, lint, changelog, quickstart validation.

---

## Notes

- TDD is mandatory: test file before source file, Red-Green-Refactor. The constitution names this as NON-NEGOTIABLE.
- [P] tasks = different files, no dependencies on incomplete tasks.
- Workflow code is deterministic — no `datetime.now()`, no `httpx`, no `pygnmi`, no `random`. Only `workflow.execute_activity` / `workflow.now()` / `workflow.logger` / `await asyncio.gather` of activity calls. The temporalio runtime sandbox enforces this — non-determinism surfaces as a test failure.
- Activity functions resolve their concrete dependencies via the module-level `_activities` container set by the worker bootstrap, so workflow tests can inject mocks without monkey-patching imports.
- `WorkflowEnvironment.start_time_skipping()` is the unit-test harness — no Docker, no Temporal server required.
- The Orchestrator depends on the public ABCs of Intent / Executor / Collector / Observer — never on their concrete implementations. The concrete `GnmiExecutor`, `GnmiCollector`, `StructuralObserver`, `InfrahubIntentStore` are wired up only in `worker/run.py`.
- The 004-observability-drift branch is the source of the `Observer` ABC + `DriftReport` / `AuditEntry` models that this feature consumes. If 004 has not yet merged into `main` when implementation begins, branch off 004 or merge it first — coordinate with the active branch state.
- SQLite WAL journal mode is set on connection open, not in DDL. Set via `PRAGMA journal_mode=WAL;` before any writes.
- `pytestmark = pytest.mark.unit` must appear after all imports in every unit test file (learned from 002-executor-gnmi).
- Integration tests: add `# pragma: allowlist secret` on password fixture lines and any test that holds a secret literal.
- Commit after each task or logical group. The branch is `005-orchestrator-temporal`; PR to `main`.
