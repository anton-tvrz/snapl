# Tasks: NAF Executor — gNMI Config Deployment

**Input**: Design documents from `/specs/002-executor-gnmi/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/executor.md, quickstart.md

**Tests**: TDD is mandatory per CLAUDE.md — "Always produce the test file first. This is a hard rule, not a suggestion." Test tasks are included for all phases.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- Exact file paths included in all descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Package directory structure and test scaffolding

- [ ] T001 Create package directory structure (gnmi/, templates/dcfabric/) inside packages/executor/snapl_executor/ and create empty __init__.py files for snapl_executor/gnmi/
- [ ] T002 [P] Create test directory scaffolding with __init__.py (tests/unit/test_executor/__init__.py, tests/integration/test_executor/__init__.py)
- [ ] T003 [P] Add shared executor test fixtures to tests/conftest.py — mock_gnmi_client (MagicMock with set method), dcfabric_desired_state (DesiredState with 2 interfaces and 1 BGP session from snapl_intent.models)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core domain types, ABC, exceptions, and package exports that ALL user stories depend on

**CRITICAL**: No user story work can begin until this phase is complete

### Tests (TDD — write first, verify they fail)

- [ ] T004 [P] Write unit tests for result models (ApplyResult: success=True implies error=None, success=False implies error set; DryRunResult: no device change; BatchResult: succeeded+failed==total; all are frozen dataclasses/immutable) in tests/unit/test_executor/test_models.py
- [ ] T005 [P] Write unit tests for Executor ABC contract enforcement (cannot instantiate Executor directly, concrete subclass missing any abstract method raises TypeError) in tests/unit/test_executor/test_abc.py

### Implementation

- [ ] T006 [P] Implement exception hierarchy (ExecutorError base, ExecutorRenderError for fatal j2 syntax errors, ExecutorConfigError for invalid constructor args) in packages/executor/snapl_executor/exceptions.py
- [ ] T007 [P] Implement result models (ApplyResult, DryRunResult, BatchResult) as frozen dataclasses with field invariants per data-model.md in packages/executor/snapl_executor/models.py
- [ ] T008 Implement Executor ABC (apply, rollback, dry_run, apply_batch as @abstractmethod async def) per contracts/executor.md in packages/executor/snapl_executor/abc.py
- [ ] T009 Update packages/executor/snapl_executor/__init__.py with placeholder exports (Executor ABC, result models, exceptions) — full exports finalised in T033

**Checkpoint**: Foundation ready — ABC, models, exceptions all tested and passing. User story implementation can begin.

---

## Phase 3: User Story 1 — Deploy Desired State to a Device (Priority: P1) MVP

**Goal**: GnmiExecutor.apply() renders a DesiredState into SR Linux YANG JSON via Jinja2 templates and issues a gNMI SET, returning an ApplyResult with success/failure detail.

**Independent Test**: Call apply() with a mock gNMIclient — verify gNMI SET called with rendered payload, result.success=True. Simulate gRPC error — verify result.success=False with error set, no exception raised.

### Tests (TDD — write first, verify they fail)

- [ ] T010 [P] [US1] Write unit tests for ConfigRenderer (load templates from templates/dcfabric/, render interfaces.j2 with 2-interface DesiredState produces correct SR Linux JSON keys, render system.j2 produces loopback address, missing required variable returns render_error string not exception) in tests/unit/test_executor/test_renderer.py
- [ ] T011 [P] [US1] Write unit tests for GnmiExecutor.apply() with mocked gNMIclient (success path: mock SET returns success → ApplyResult success=True with payload and duration_ms; connection error via side_effect → ApplyResult success=False error set; device rejects payload → ApplyResult success=False; is_rollback=False on apply) in tests/unit/test_executor/test_executor.py

### Implementation

- [ ] T012 [US1] Implement SR Linux interface template (renders Interface list into interface[]/subinterface[]/ipv4/address SR Linux YANG JSON structure for each enabled interface with ip_address set) in packages/executor/snapl_executor/templates/dcfabric/interfaces.j2
- [ ] T013 [US1] Implement SR Linux system template (renders device loopback lo0/subinterface[index=0]/ipv4/address from management_address field; sets system/name to device name) in packages/executor/snapl_executor/templates/dcfabric/system.j2
- [ ] T014 [US1] Implement gNMI client wrapper (gNMIclient as per-call context manager, wraps blocking set() call for asyncio.to_thread, enforces configurable timeout defaulting to 30s, maps gRPC exceptions to error strings) in packages/executor/snapl_executor/gnmi/client.py
- [ ] T015 [US1] Implement ConfigRenderer (discovers Jinja2 templates from package templates/<use_case>/ directory, loads Environment with autoescape=False, render(desired) merges rendered interfaces + system into one dict payload for gNMI root SET, catches UndefinedError as render_error string) in packages/executor/snapl_executor/gnmi/renderer.py
- [ ] T016 [US1] Implement GnmiExecutor scaffolding and apply() (constructor accepts host, port, username, password, insecure, timeout; apply() calls ConfigRenderer.render() then asyncio.to_thread(gnmi_set), records wall-clock duration_ms, returns ApplyResult; no exception raised for device-side errors) in packages/executor/snapl_executor/gnmi/executor.py

### Integration Tests (require running Containerlab SR Linux node)

- [ ] T017 [US1] Create integration conftest.py for executor tests (SRLINUX_HOST/PORT/USERNAME/PASSWORD env vars with Containerlab defaults; skip fixture if node unreachable via probe) in tests/integration/test_executor/conftest.py
- [ ] T018 [US1] Write integration test for apply() against live SR Linux node (build DesiredState from a seeded spine device, call apply(), assert result.success=True and payload non-empty) in tests/integration/test_executor/test_gnmi_apply.py

**Checkpoint**: US1 unit tests pass. apply() renders correctly and returns structured ApplyResult for both success and failure paths. Integration test validates against real SR Linux node.

---

## Phase 4: User Story 2 — Validate Without Applying (Priority: P2)

**Goal**: GnmiExecutor.dry_run() renders the DesiredState and returns the payload without making a gNMI connection. Render errors are caught and returned as DryRunResult.success=False.

**Independent Test**: Call dry_run() with valid DesiredState — verify DryRunResult.success=True and payload present, no gNMI call made. Supply DesiredState that triggers a missing-variable render error — verify DryRunResult.success=False with render_error set, no exception.

### Tests (TDD — write first, verify they fail)

- [ ] T019 [P] [US2] Write unit tests for GnmiExecutor.dry_run() (success: returns DryRunResult success=True payload=dict, mock confirms gNMIclient.set() never called; render error: returns DryRunResult success=False render_error set, no exception raised; result clearly not a committed change) in tests/unit/test_executor/test_executor.py (append to existing)
- [ ] T020 [P] [US2] Write unit tests for ConfigRenderer render-error path (DesiredState with required field None/missing → render() returns dict with render_error key rather than raising; valid DesiredState with empty interface list → renders empty interface list not error) in tests/unit/test_executor/test_renderer.py (append to existing)

### Implementation

- [ ] T021 [US2] Implement GnmiExecutor.dry_run() (call ConfigRenderer.render(), on render_error return DryRunResult success=False; on success return DryRunResult success=True payload=payload; no gNMI connection opened) in packages/executor/snapl_executor/gnmi/executor.py (append)

### Integration Tests

- [ ] T022 [US2] Add dry_run integration test (call dry_run() with live SR Linux node fixture, assert DryRunResult.success=True and payload non-empty; verify by calling GET after dry_run that running config unchanged) in tests/integration/test_executor/test_gnmi_apply.py (append)

**Checkpoint**: US2 complete. dry_run() is safe — catches render errors, never opens a gNMI connection. Satisfies SC-003 (100% render errors caught before gNMI).

---

## Phase 5: User Story 3 — Roll Back a Failed Deployment (Priority: P3)

**Goal**: GnmiExecutor.rollback() re-applies a prior desired state with ApplyResult.is_rollback=True, so callers and audit logs can distinguish rollback from normal apply.

**Independent Test**: Call rollback() with a valid DesiredState — verify ApplyResult.is_rollback=True and same success/failure semantics as apply(). Verify the result type is distinct from dry_run.

### Tests (TDD — write first, verify they fail)

- [ ] T023 [P] [US3] Write unit tests for GnmiExecutor.rollback() (success path: mock SET succeeds → ApplyResult is_rollback=True success=True; failure path: mock raises error → ApplyResult is_rollback=True success=False error set; is_rollback distinguishes from apply result) in tests/unit/test_executor/test_executor.py (append)

### Implementation

- [ ] T024 [US3] Implement GnmiExecutor.rollback() (identical to apply() except is_rollback=True in returned ApplyResult; reuses same gNMI SET and renderer path) in packages/executor/snapl_executor/gnmi/executor.py (append)

### Integration Tests

- [ ] T025 [US3] Add rollback integration test (apply a desired state to live SR Linux, then call rollback() with alternate desired state, assert rollback result.is_rollback=True and success=True) in tests/integration/test_executor/test_gnmi_apply.py (append)

**Checkpoint**: US3 complete. rollback() is semantically apply() + is_rollback=True flag. Callers and Orchestrator saga steps can distinguish rollback from normal apply in audit logs.

---

## Phase 6: User Story 4 — Deploy to Multiple Devices (Priority: P4)

**Goal**: GnmiExecutor.apply_batch() dispatches apply() to a list of devices in parallel, collects per-device ApplyResult into a BatchResult, and never raises an exception for per-device failures.

**Independent Test**: Call apply_batch() with 3 mocked devices — verify 3 SET calls made, BatchResult has 3 entries. Make 1 of 3 fail — verify BatchResult.failed=1, other 2 succeed, no exception raised.

### Tests (TDD — write first, verify they fail)

- [ ] T026 [P] [US4] Write unit tests for GnmiExecutor.apply_batch() (3 devices all succeed → BatchResult total=3 succeeded=3 failed=0; 1 of 3 fails → succeeded=2 failed=1 failure captured in results dict not raised; empty list raises ValueError; duplicate device IDs raises ValueError) in tests/unit/test_executor/test_executor.py (append)

### Implementation

- [ ] T027 [US4] Implement SR Linux BGP template (renders BGP neighbor list: network-instance[name=default]/protocols/bgp/autonomous-system and /neighbor entries from BGPSession list including peer-address, peer-as, enabled) in packages/executor/snapl_executor/templates/dcfabric/bgp.j2
- [ ] T028 [US4] Implement GnmiExecutor.apply_batch() (validate no duplicate device IDs; dispatch asyncio.gather() across per-device apply() calls; collect results into BatchResult with succeeded/failed counts; never raises for per-device failures) in packages/executor/snapl_executor/gnmi/executor.py (append)

### Integration Tests

- [ ] T029 [US4] Add batch apply integration test (build DesiredState list for 2 spine nodes from seeded dcfabric topology, call apply_batch(), assert BatchResult.total=2 succeeded=2) in tests/integration/test_executor/test_gnmi_apply.py (append)

**Checkpoint**: US4 complete. Batch apply dispatches across all devices in parallel, captures per-device failures without propagating them as exceptions, satisfying SC-004.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Performance assertions, export completeness, lint/format, final validation

- [ ] T030 [P] Add SC-001 performance assertion to integration tests (single apply() completes in <30s for a reachable device; measure with time.monotonic()) in tests/integration/test_executor/test_gnmi_apply.py (append)
- [ ] T031 [P] Add SC-007 timeout assertion to integration test (apply() with host=127.0.0.1:19999 returns ApplyResult.success=False within 30s; no hanging) in tests/integration/test_executor/test_gnmi_apply.py (append)
- [ ] T032 [P] Add SC-002 dry_run render performance assertion (<1s for any valid DesiredState) to unit tests in tests/unit/test_executor/test_renderer.py (append)
- [ ] T033 Finalise package exports in packages/executor/snapl_executor/__init__.py (Executor, GnmiExecutor, ApplyResult, DryRunResult, BatchResult, ExecutorError, ExecutorRenderError, ExecutorConfigError)
- [ ] T034 [P] Verify lint and format clean: uv run invoke lint && uv run invoke format — zero errors
- [ ] T035 Verify all unit tests pass with ≥80% coverage: uv run pytest tests/unit/test_executor/ -m unit -v --cov=snapl_executor
- [ ] T036 Run quickstart.md validation end-to-end against live SR Linux node (dry_run → apply → rollback → batch apply; all operations return expected result types; no exceptions from device-side errors)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2. Templates (T012, T013) can start in parallel with US2 foundational tasks.
- **US2 (Phase 4)**: Depends on Phase 3 (dry_run reuses renderer from US1).
- **US3 (Phase 5)**: Depends on Phase 3 (rollback reuses apply() plumbing from US1).
- **US4 (Phase 6)**: Depends on Phase 3 (apply_batch() dispatches apply()); T027 (bgp.j2) can start after T015 (renderer exists).
- **Polish (Phase 7)**: Depends on all user stories complete.

### User Story Dependencies

- **US1 (P1)**: The core — renderer, client, executor.apply() are the foundation all others build on.
- **US2 (P2)**: Reuses ConfigRenderer from US1. dry_run() adds a no-connection code path.
- **US3 (P3)**: Reuses apply() from US1. rollback() is a thin wrapper setting is_rollback=True.
- **US4 (P4)**: Reuses apply() from US1. apply_batch() adds asyncio.gather() coordination and the BGP template.

### Within Each User Story

1. Tests MUST be written and FAIL before implementation (TDD mandate)
2. Templates before renderer; renderer before executor method
3. Unit tests before integration tests
4. Core logic before integration wiring
5. Story complete and tested before moving to next priority

### Parallel Opportunities

**Phase 1**: T002, T003 in parallel after T001
**Phase 2**: T004, T005 in parallel (tests); T006, T007 in parallel (impl)
**Phase 3**: T010, T011 in parallel (tests); T012, T013 in parallel (templates); T014 before T015 before T016
**Phase 4**: T019, T020 in parallel (tests); T021 then T022
**Phase 5**: T023 (test) before T024 (impl) before T025 (integration)
**Phase 6**: T026 (test) before T027+T028 in parallel, then T029
**Phase 7**: T030, T031, T032, T034 all in parallel

---

## Parallel Example: User Story 1

```bash
# Launch all tests for US1 together (TDD — must fail first):
Task: T010 "ConfigRenderer unit tests in test_renderer.py"
Task: T011 "GnmiExecutor.apply() unit tests in test_executor.py"

# After tests fail, launch templates in parallel:
Task: T012 "interfaces.j2 template"
Task: T013 "system.j2 template"

# Then sequentially:
Task: T014 "gnmi/client.py wrapper"
Task: T015 "gnmi/renderer.py ConfigRenderer"
Task: T016 "gnmi/executor.py GnmiExecutor.apply()"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1 (unit tests first, then impl, then integration)
4. **STOP and VALIDATE**: apply() works end-to-end against mock and live SR Linux
5. Proceed to US2 dry_run

### Incremental Delivery

1. Setup + Foundational → ABC, models, exceptions ready
2. US1 → apply() working (MVP: Intent → Executor → SR Linux)
3. US2 → dry_run() safety gate available
4. US3 → rollback() saga compensation available for Orchestrator
5. US4 → batch apply completes the datacenter fabric deployment pattern
6. Polish → perf assertions, exports finalised, quickstart validated

### Single-Developer Strategy (Recommended)

1. Phase 1 + 2 sequentially (foundation)
2. Phase 3 US1 (write T010, T011 tests first, verify fail; then T012-T016; then T017-T018 integration)
3. Phase 4 US2 (T019, T020 tests; T021 impl; T022 integration)
4. Phase 5 US3 (T023 test; T024 impl; T025 integration)
5. Phase 6 US4 (T026 test; T027 bgp.j2; T028 impl; T029 integration)
6. Phase 7 Polish

---

## Notes

- TDD is mandatory: test file before source file, Red-Green-Refactor
- [P] tasks = different files, no dependencies on incomplete tasks
- pygnmi is synchronous — all blocking calls must go through asyncio.to_thread (see research R1)
- Result objects, not exceptions, for all device-side outcomes (see research R5 and contracts/executor.md design note)
- gNMI SET uses update semantics (not replace) at root path "/" with merged YANG JSON (see research R3)
- Integration tests require a running Containerlab SR Linux node: `cd containerlab && sudo containerlab deploy -t dcfabric.yml`
- test_executor.py is a single file that grows across phases (US1 → US2 → US3 → US4 → Polish)
- SR Linux YANG JSON uses kebab-case keys (admin-state, not admin_state; ip-prefix not ip_address)
- Commit after each task or logical group
