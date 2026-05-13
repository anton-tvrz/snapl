# Tasks: NAF Collector — gNMI Live Data Retrieval

**Input**: Design documents from `/specs/003-collector-gnmi/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/collector.md, quickstart.md

**Tests**: TDD is mandatory per CLAUDE.md — "Always produce the test file first. This is a hard rule, not a suggestion." Test tasks are included for all phases.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Exact file paths included in all descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Package directory structure, pyproject.toml, and test scaffolding

- [ ] T001 Create packages/collector/pyproject.toml (name=snapl-collector, python>=3.12, deps: pygnmi>=0.8, grpcio>=1.60, pydantic>=2.5; workspace dep: snapl-intent; add snapl-collector to workspace members in root pyproject.toml)
- [ ] T002 [P] Create package source directory structure: packages/collector/snapl_collector/ and packages/collector/snapl_collector/gnmi/ with empty __init__.py files in each
- [ ] T003 [P] Create test directory scaffolding with __init__.py: tests/unit/test_collector/__init__.py and tests/integration/test_collector/__init__.py
- [ ] T004 [P] Add shared collector fixture to tests/conftest.py — `make_device` factory fixture returning a `snapl_intent.models.Device` with management_address, name, id, role="spine", use_case="dcfabric", platform="nokia-srlinux"

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core domain types, ABC, exceptions, and package exports that ALL user stories depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

### Tests (TDD — write first, verify they fail)

- [ ] T005 [P] Write unit tests for CollectResult and BatchCollectResult models (CollectResult: success=True implies error=None; success=False implies error set and data={}; duration_ms and timestamp fields present; frozen dataclass; BatchCollectResult: succeeded+failed==total; results is dict[UUID, CollectResult]) in tests/unit/test_collector/test_models.py
- [ ] T006 [P] Write unit tests for Collector ABC contract enforcement (cannot instantiate Collector directly; concrete subclass missing any abstract method raises TypeError; subclass implementing all three abstract methods can be instantiated) in tests/unit/test_collector/test_abc.py

### Implementation

- [ ] T007 [P] Implement exception hierarchy (CollectorError base class, CollectorConfigError for invalid constructor args — e.g., missing host, bad port) in packages/collector/snapl_collector/exceptions.py
- [ ] T008 [P] Implement result models (CollectResult, BatchCollectResult) as frozen dataclasses with field invariants per data-model.md — CollectResult.data defaults to empty dict, timestamp defaults to UTC now, CollectResult.paths defaults to empty list in packages/collector/snapl_collector/models.py
- [ ] T009 Implement Collector ABC (collect, get_running_config, collect_batch as @abstractmethod async def with full docstrings) per contracts/collector.md in packages/collector/snapl_collector/abc.py
- [ ] T010 Update packages/collector/snapl_collector/__init__.py with placeholder exports (Collector ABC, result models, exceptions) — full exports finalised in T026

**Checkpoint**: Foundation ready — ABC, models, exceptions all tested and passing. User story implementation can begin.

---

## Phase 3: User Story 1 — Retrieve Running Configuration (Priority: P1) 🎯 MVP

**Goal**: `GnmiCollector.get_running_config(device)` issues a gNMI GET at root path `/`, parses the response into a dict, and returns a `CollectResult` — never raising for device-side errors.

**Independent Test**: Call `get_running_config()` with a mocked gNMIclient that returns a synthetic notification dict — verify `CollectResult.success=True` and `data["/"]` is populated. Simulate `OSError` — verify `CollectResult.success=False` with error containing "connectivity", no exception propagated.

### Tests (TDD — write first, verify they fail)

- [ ] T011 [P] [US1] Write unit tests for gnmi_get client wrapper (mock gNMIclient as context manager: successful gc.get() returns expected response dict; OSError side_effect → propagated as-is; gRPC RpcError side_effect → propagated as-is; correct path list and datatype="all" forwarded to gc.get()) in tests/unit/test_collector/test_client.py
- [ ] T012 [P] [US1] Write unit tests for GnmiCollector.get_running_config() (success: mock returns notification dict → CollectResult success=True data non-empty dict timestamp set duration_ms>0; unreachable device OSError → success=False error contains "connectivity" no exception raised; auth error gRPC UNAUTHENTICATED → success=False error contains "auth"; timeout gRPC DEADLINE_EXCEEDED → success=False error contains "timeout"; malformed response KeyError → success=False error contains "parse") in tests/unit/test_collector/test_collector.py

### Implementation

- [ ] T013 [US1] Implement gnmi_get client wrapper (gNMIclient as per-call context manager with host/port/username/password/insecure/timeout params; wraps blocking gc.get(path=paths, datatype="all") call; propagates all exceptions — GnmiCollector handles classification; return raw response dict) in packages/collector/snapl_collector/gnmi/client.py
- [ ] T014 [US1] Implement GnmiCollector scaffolding + _parse_response() + collect() + get_running_config() (constructor accepts host, port=57400, username="admin", password, insecure=True, timeout=30; collect() wraps asyncio.to_thread(_blocking_get), records wall-clock duration_ms, sets UTC timestamp, classifies OSError→"connectivity error", gRPC UNAUTHENTICATED→"auth error", DEADLINE_EXCEEDED→"timeout after Ns", KeyError/TypeError→"parse error" into CollectResult.error; never raises for device-side errors; get_running_config() delegates to collect(device, paths=["/"])) in packages/collector/snapl_collector/gnmi/collector.py

### Integration Tests (require running Containerlab SR Linux node)

- [ ] T015 [US1] Create integration conftest.py for collector tests (SRLINUX_HOST/PORT/USERNAME/PASSWORD env vars with Containerlab defaults clab-dcfabric-spine-01/57400/admin; skip_if_unreachable session-scoped fixture probing TCP socket; build_collector() fixture returning GnmiCollector from env vars; build_device() fixture returning Device with management_address from env vars) in tests/integration/test_collector/conftest.py
- [ ] T016 [US1] Write integration test for get_running_config() against live SR Linux node (call get_running_config() with spine Device; assert result.success=True; assert result.data["/"] is non-empty dict; assert result.duration_ms > 0; assert result.timestamp is not None; assert result.error is None) in tests/integration/test_collector/test_gnmi_collect.py

**Checkpoint**: US1 unit tests pass. get_running_config() returns structured CollectResult for both success and failure paths. Integration test validates against real SR Linux node.

---

## Phase 4: User Story 2 — Collect Specific Configuration Paths (Priority: P2)

**Goal**: `GnmiCollector.collect(device, paths)` retrieves data at one or more caller-supplied YANG paths and returns a `CollectResult` with `data` keyed by path. Empty path list is rejected before any gNMI connection. An empty subtree at a valid path returns success with empty data for that path.

**Independent Test**: Call `collect()` with `paths=["/interface", "/network-instance[name=default]/protocols/bgp/neighbor"]` against a mocked gNMIclient — verify both paths present as keys in `result.data`. Call with `paths=[]` — verify `ValueError` raised before any gNMI connection.

### Tests (TDD — write first, verify they fail)

- [ ] T017 [P] [US2] Write unit tests extending GnmiCollector.collect() coverage (paths=["/interface"]: mock returns single-path notification → data keyed by "/interface"; paths=["/a", "/b"]: mock returns two-path notification → data has both keys; path returns empty update list → data[path]={} success=True; paths=[] → raises ValueError before gNMI connection made; response with unexpected structure triggers parse error → success=False error contains "parse") in tests/unit/test_collector/test_collector.py (append)

### Implementation

- [ ] T018 [US2] Extend GnmiCollector.collect() with empty-path validation (raise ValueError if paths is empty before opening gNMI connection) and verify _parse_response() correctly keys multi-path notification response by normalised path string (update existing collect() in packages/collector/snapl_collector/gnmi/collector.py)

### Integration Tests

- [ ] T019 [US2] Add targeted collect() integration test (collect with paths=["/interface", "/network-instance[name=default]/protocols/bgp/neighbor"]; assert result.success=True; assert both path strings are keys in result.data; assert result.paths == input paths; assert each value is a list or dict) in tests/integration/test_collector/test_gnmi_collect.py (append)

**Checkpoint**: US2 complete. collect() handles multi-path retrieval, empty path validation, and partial response parsing. Targeted collection satisfies SC-002.

---

## Phase 5: User Story 3 — Collect from Multiple Devices (Priority: P3)

**Goal**: `GnmiCollector.collect_batch(devices, paths)` dispatches concurrent `collect()` calls across all devices, captures per-device results into a `BatchCollectResult`, and never raises for per-device failures.

**Independent Test**: Call `collect_batch()` with 3 mocked devices — verify 3 GET calls made and `BatchCollectResult.total=3`. Make 1 of 3 fail with a connectivity error — verify `succeeded=2, failed=1`, failure captured in `results`, no exception raised.

### Tests (TDD — write first, verify they fail)

- [ ] T020 [P] [US3] Write unit tests for GnmiCollector.collect_batch() (3 devices all succeed → BatchCollectResult total=3 succeeded=3 failed=0 results has 3 entries; 1 of 3 fails with OSError → succeeded=2 failed=1 failure in results not raised; empty devices list raises ValueError; duplicate device IDs raises ValueError; empty paths raises ValueError) in tests/unit/test_collector/test_collector.py (append)

### Implementation

- [ ] T021 [US3] Implement GnmiCollector.collect_batch() (validate non-empty devices, non-empty paths, no duplicate device UUIDs — raise ValueError for any violation; dispatch asyncio.gather() across per-device async helpers each calling self.collect() with per-device GnmiCollector params; collect all results into BatchCollectResult with succeeded/failed counts; never raises for per-device failures) in packages/collector/snapl_collector/gnmi/collector.py (append)

### Integration Tests

- [ ] T022 [US3] Add collect_batch() integration test (build Device list for 2 Containerlab spine nodes from env vars, call collect_batch() with paths=["/interface"]; assert BatchCollectResult.total=2 succeeded=2 failed=0; assert both device UUIDs present in results; assert each result.success=True) in tests/integration/test_collector/test_gnmi_collect.py (append)

**Checkpoint**: US3 complete. collect_batch() dispatches GETs across all devices concurrently, captures per-device failures, satisfying SC-004 (12-device fabric within 2 minutes).

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Performance assertions, export completeness, lint/format, final validation

- [ ] T023 [P] Add SC-001 performance assertion to integration tests (get_running_config() completes in <30s for reachable device; measure with time.monotonic(); assert duration within threshold) in tests/integration/test_collector/test_gnmi_collect.py (append)
- [ ] T024 [P] Add SC-002 performance assertion (targeted collect() for a single YANG path completes in <5s on reachable device; measure with time.monotonic()) in tests/integration/test_collector/test_gnmi_collect.py (append)
- [ ] T025 [P] Add SC-003 assertion to unit tests (all four gNMI error categories — connectivity, auth, timeout, parse — return CollectResult success=False; verify no exception escapes) in tests/unit/test_collector/test_collector.py (append)
- [ ] T026 Finalise package exports in packages/collector/snapl_collector/__init__.py (Collector, GnmiCollector, CollectResult, BatchCollectResult, CollectorError, CollectorConfigError)
- [ ] T027 [P] Verify lint and format clean: uv run invoke lint && uv run invoke format — zero errors
- [ ] T028 Verify all unit tests pass with ≥80% coverage: uv run pytest tests/unit/test_collector/ -m unit -v --cov=snapl_collector
- [ ] T029 Run quickstart.md validation end-to-end against live SR Linux node (get_running_config → targeted collect → collect_batch; all operations return expected result types; no exceptions from device-side errors; compare collected data keys against Executor's rendered payload keys to validate SC-006)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2. gnmi_get client (T013) and GnmiCollector (T014) are the core. get_running_config() delegates to collect() so both are implemented together.
- **US2 (Phase 4)**: Depends on Phase 3. Extends the existing collect() — the base implementation is already in place; US2 adds multi-path validation and edge-case test coverage.
- **US3 (Phase 5)**: Depends on Phase 3. collect_batch() dispatches collect() per device — collect() must be complete first.
- **Polish (Phase 6)**: Depends on all user stories complete.

### User Story Dependencies

- **US1 (P1)**: The core — gnmi_get, collect(), get_running_config() are the foundation all others build on.
- **US2 (P2)**: Extends collect() from US1 with multi-path validation and edge-case handling. No new source files — extends existing collector.py with validation logic.
- **US3 (P3)**: Adds collect_batch() as concurrent dispatch of collect(). No new source files — extends existing collector.py.

### Within Each User Story

1. Tests MUST be written and FAIL before implementation (TDD mandate)
2. Unit tests before integration tests
3. Core logic (client.py, collector.py) before integration test fixtures
4. Story complete and tested before moving to next priority

### Parallel Opportunities

**Phase 1**: T002, T003, T004 in parallel after T001
**Phase 2**: T005, T006 in parallel (tests); T007, T008 in parallel (impl); T009 → T010 sequential
**Phase 3**: T011, T012 in parallel (tests); T013 → T014 sequential; T015 → T016 sequential
**Phase 4**: T017 (test) → T018 (impl) → T019 (integration) sequential
**Phase 5**: T020 (test) → T021 (impl) → T022 (integration) sequential
**Phase 6**: T023, T024, T025, T027 all in parallel; T026 → T028 → T029 sequential

---

## Parallel Example: User Story 1

```bash
# Launch all US1 tests together (TDD — must fail first):
Task: T011 "gnmi_get unit tests in test_client.py"
Task: T012 "GnmiCollector.get_running_config() unit tests in test_collector.py"

# After tests fail, implement sequentially:
Task: T013 "gnmi/client.py gnmi_get wrapper"
Task: T014 "gnmi/collector.py GnmiCollector with collect() + get_running_config()"

# Then integration:
Task: T015 "tests/integration/test_collector/conftest.py"
Task: T016 "tests/integration/test_collector/test_gnmi_collect.py (US1)"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001–T004)
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1 (unit tests first, then impl, then integration)
4. **STOP and VALIDATE**: get_running_config() works end-to-end against mock and live SR Linux
5. Proceed to US2 targeted collect

### Incremental Delivery

1. Setup + Foundational → ABC, models, exceptions ready
2. US1 → get_running_config() working (MVP: SR Linux → Collector → structured dict)
3. US2 → targeted collect() available for high-frequency Observability polling
4. US3 → collect_batch() completes the fabric-scale read pattern (mirrors apply_batch in Executor)
5. Polish → perf assertions, exports finalised, SC-006 drift-comparison validated

### Single-Developer Strategy (Recommended)

1. Phase 1 (T001–T004) — package setup and scaffolding
2. Phase 2 (T005–T010) — write T005, T006 tests first, verify fail; then T007–T010 impl
3. Phase 3 US1 — write T011, T012 tests first, verify fail; then T013–T014 impl; then T015–T016 integration
4. Phase 4 US2 — write T017 test; then T018 impl; then T019 integration
5. Phase 5 US3 — write T020 test; then T021 impl; then T022 integration
6. Phase 6 Polish — T023–T029

---

## Notes

- TDD is mandatory: test file before source file, Red-Green-Refactor
- [P] tasks = different files, no dependencies on incomplete tasks
- pygnmi is synchronous — all blocking calls must go through asyncio.to_thread (see research R1)
- Result objects, not exceptions, for all device-side outcomes (see research R4 and contracts/collector.md design note)
- gNMI GET uses `gc.get(path=paths, datatype="all")` — path is a list of YANG path strings (see research R1)
- collect() is the core method; get_running_config() is a thin wrapper calling collect(device, paths=["/"]) (see data-model.md)
- collect_batch() creates per-device GnmiCollector instances to dispatch concurrent GETs (see research R5)
- The Collector and Executor share the same env vars for integration tests — same Containerlab lab node (see research R6)
- `pytestmark = pytest.mark.unit` must appear after all imports in every unit test file (learned from 002-executor-gnmi)
- Secret scanner: add `# pragma: allowlist secret` on any line with a test password value
- Integration tests: add `# pragma: allowlist secret` on password fixture lines
- Commit after each task or logical group
- test_collector.py is a single file that grows across phases (US1 → US2 → US3 → Polish)
