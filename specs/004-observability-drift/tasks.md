# Tasks: NAF Observability — Drift Detection & Audit

**Input**: Design documents from `/specs/004-observability-drift/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/observer.md, quickstart.md

**Tests**: TDD is mandatory per CLAUDE.md — "Always produce the test file first. This is a hard rule, not a suggestion." Test tasks are included for all phases.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Exact file paths included in all descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Verify package config, scaffold source/test directories, add shared fixtures

- [X] T001 Verify and update packages/observability/pyproject.toml (name=snapl-observability, python>=3.12, dependencies: pydantic>=2.5, snapl-intent (workspace dep), snapl-collector (workspace dep) — REMOVE pyyaml dependency: not used per plan; verify packages/observability is included in root pyproject.toml workspace members and that snapl-intent + snapl-collector are listed as workspace deps)
- [X] T002 [P] Create source directory structure: packages/observability/snapl_observability/structural/ with empty __init__.py (the top-level snapl_observability/__init__.py already exists; structural/ is new)
- [X] T003 [P] Create test directory scaffolding: tests/unit/test_observability/__init__.py (the directory already exists per ls — only the empty __init__.py is needed)
- [X] T004 [P] Add shared observability fixtures to tests/conftest.py — `make_desired_state` factory returning a `snapl_intent.models.DesiredState` with a Device, two Interfaces, and one BGPSession populated; `make_collect_result` factory returning a `snapl_collector.models.CollectResult` with success=True and a configurable `data` dict keyed by YANG path

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Domain models, ABC, exceptions, and infrastructure services (AuditLog, EventBus) that ALL user stories depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

### Tests (TDD — write first, verify they fail)

- [X] T005 [P] Write unit tests for all Pydantic models in packages/observability covering: DriftStatus / EventType / AuditOperation / AuditOutcome enums (string values match spec); DriftItem (frozen, extra=forbid, ValueError when desired==actual); DriftReport invariants (CLEAN ⇒ items==[] and error is None; DRIFTED ⇒ len(items)≥1 and error is None; ERROR ⇒ items==[] and error is not None); BatchDriftReport (clean+drifted+errored==total); ObservabilityEvent (event_type matches report.status mapping; ValueError on mismatch); AuditEntry (frozen, default detail={}, timestamp UTC) — in tests/unit/test_observability/test_models.py
- [X] T006 [P] Write unit tests for Observer ABC contract enforcement (cannot instantiate Observer directly raises TypeError; concrete subclass missing any of detect_drift / detect_drift_batch / emit_event / log_audit raises TypeError on instantiation; subclass implementing all four can be instantiated) in tests/unit/test_observability/test_abc.py
- [X] T007 [P] Write unit tests for AuditLog (append increases len by 1; query_by_device returns chronological list filtered by device_id; query_by_device returns empty list for unknown UUID without error; all() returns chronological list of every entry; returned lists are copies — mutating them does not affect internal storage; concurrent appends from multiple threads produce all entries with no loss) in tests/unit/test_observability/test_audit.py
- [X] T008 [P] Write unit tests for EventBus (register adds handler; emit with no handlers succeeds silently; emit invokes every registered handler in registration order; one handler raising does not prevent subsequent handlers from being invoked; handler exceptions are logged at WARNING via caplog; handlers property returns a tuple — read-only view) in tests/unit/test_observability/test_events.py

### Implementation

- [X] T009 [P] Implement exception module: ObserverError base class (raised only for programming errors per contract — mismatched device IDs, empty batch, non-callable handler) in packages/observability/snapl_observability/exceptions.py
- [X] T010 [P] Implement all Pydantic models per data-model.md and contracts/observer.md (DriftStatus, EventType, AuditOperation, AuditOutcome enums; DriftItem with desired!=actual validator; DriftReport with status-vs-items-vs-error model_validator; BatchDriftReport with sum-equals-total validator; ObservabilityEvent with event_type-matches-status validator; AuditEntry with default detail={} and frozen config) in packages/observability/snapl_observability/models.py
- [X] T011 [P] Implement AuditLog (in-memory list[AuditEntry] guarded by threading.Lock; append acquires lock and appends; query_by_device returns list copy filtered by device_id sorted by timestamp; all() returns list copy of all entries sorted by timestamp; __len__ returns count) in packages/observability/snapl_observability/audit.py
- [X] T012 [P] Implement EventBus (handler list as private attribute; register validates handler is callable else raises ObserverError; emit iterates handlers wrapping each in try/except logging exceptions at WARNING with handler.__qualname__; handlers property returns tuple(self._handlers)) in packages/observability/snapl_observability/events.py
- [X] T013 Implement Observer ABC per contracts/observer.md (detect_drift, detect_drift_batch, emit_event, log_audit as @abstractmethod async def with full docstrings; imports DesiredState, CollectResult, all observability models) in packages/observability/snapl_observability/abc.py
- [X] T014 Update packages/observability/snapl_observability/__init__.py with placeholder exports (Observer, all models, all enums, ObserverError, AuditLog, EventBus) — full StructuralObserver export added in T030

**Checkpoint**: Foundation ready — ABC, models, exceptions, AuditLog, EventBus all tested and passing. User story implementation can begin.

---

## Phase 3: User Story 1 — Detect Configuration Drift on a Device (Priority: P1) 🎯 MVP

**Goal**: `StructuralObserver.detect_drift(desired, actual)` walks the intent entity field map, compares each enumerated field against the corresponding entry in `actual.data`, and returns a `DriftReport` with status CLEAN / DRIFTED / ERROR. `detect_drift_batch()` processes a list of pairs concurrently in API shape (sequentially internally) and returns a `BatchDriftReport`. Every call appends an `AuditEntry` to the configured `AuditLog`.

**Independent Test**: Construct a DesiredState with one Interface (mtu=9000), construct a CollectResult with `data["/interface[name=eth0]"] = {"mtu": 1500}` — verify `report.status == DRIFTED`, `len(report.items) == 1`, item path contains `/interface[name=eth0]/mtu`, item.desired==9000, item.actual==1500. Construct matching DesiredState/CollectResult — verify `status == CLEAN`. Construct CollectResult with success=False — verify `status == ERROR`, items empty, error string preserved.

### Tests (TDD — write first, verify they fail)

- [X] T015 [P] [US1] Write unit tests for the pure structural diff function in tests/unit/test_observability/test_diff.py covering: ENTITY_FIELD_MAP exists with entries for "interface", "bgp_session", "device"; diff_desired_vs_actual with empty interfaces and matching empty actual returns []; Interface mtu mismatch produces one DriftItem with path "/interface[name=<name>]/mtu" entity_kind="interface"; Interface description mismatch produces correct DriftItem; missing key in actual (interface absent from /interface) produces DriftItem with actual=None; BGPSession peer_asn mismatch produces DriftItem with path containing peer-address key; Device description mismatch produces DriftItem with entity_kind="device"; multiple discrepancies across entity types produce multiple items with correct entity_kinds
- [X] T016 [P] [US1] Write unit tests for StructuralObserver.detect_drift in tests/unit/test_observability/test_observer.py covering: matching desired vs actual → status=CLEAN, items=[], error is None; one Interface field mismatch → status=DRIFTED, len(items)==1; CollectResult(success=False, error="connectivity") → status=ERROR, items=[], error="connectivity"; mismatched device IDs (desired.device.id != actual.device_id) raises ValueError before diff; AuditEntry appended to provided AuditLog with operation=DETECT_DRIFT, device_id set, outcome=SUCCESS; ERROR-status drift still records AuditEntry with outcome=SUCCESS (the operation succeeded — error is the upstream collector's, not ours)
- [X] T017 [P] [US1] Write unit tests for StructuralObserver.detect_drift_batch in tests/unit/test_observability/test_observer.py covering: 3 pairs all clean → BatchDriftReport total=3 clean=3 drifted=0 errored=0; 1 drifted + 1 clean + 1 errored → counts add to 3; empty pairs list raises ValueError; pair with desired.device.id != actual.device_id raises ValueError before any diff; one AuditEntry appended per pair (3 entries total for a 3-pair batch); reports dict keyed by device UUID

### Implementation

- [X] T018 [P] [US1] Implement structural/diff.py: ENTITY_FIELD_MAP module constant per data-model.md ("interface", "bgp_session", "device" with their fields, path_template, key_field); diff_desired_vs_actual(desired, actual_data) pure function — for each entity in desired (interfaces, bgp_sessions, device), format its path from path_template with key_field substitution, look up in actual_data dict, compare each field listed in ENTITY_FIELD_MAP; return list[DriftItem] with path "<entity_path>/<field>" for each mismatch; missing actual entry → DriftItems for every compared field with actual=None
- [X] T019 [US1] Implement structural/observer.py StructuralObserver class with constructor (event_bus=None defaulting to fresh EventBus, audit_log=None defaulting to fresh AuditLog, component_name="StructuralObserver"); detect_drift(desired, actual) — validates device IDs match (raise ValueError on mismatch); on actual.success=False returns DriftReport(status=ERROR, items=[], error=actual.error); else calls diff_desired_vs_actual and returns DriftReport(status=CLEAN if no items else DRIFTED, items=items); appends AuditEntry(operation=DETECT_DRIFT, device_id, component=component_name, outcome=SUCCESS, detail={"item_count": len(items)})
- [X] T020 [US1] Extend structural/observer.py with detect_drift_batch(pairs) — validates non-empty pairs and matching device IDs in every pair (raise ValueError); iterates pairs sequentially in async loop calling self.detect_drift; aggregates reports into BatchDriftReport with clean/drifted/errored counts derived from each report.status

**Checkpoint**: US1 unit tests pass. detect_drift and detect_drift_batch return DriftReport / BatchDriftReport for all three status outcomes and append audit entries. SC-001 (<100 ms per device) and SC-005 (10-device batch produces a result for each) verifiable.

---

## Phase 4: User Story 2 — Emit a Drift Event for Downstream Consumers (Priority: P2)

**Goal**: `StructuralObserver.emit_event(report)` constructs an `ObservabilityEvent` with event_type mapped 1:1 from `report.status`, dispatches it to every handler registered on the configured `EventBus`, and returns the event. One handler raising does not prevent dispatch to subsequent handlers. Audit entry appended for the emit operation.

**Independent Test**: Build an EventBus, register two handlers that record events into a list. Construct StructuralObserver(event_bus=bus). Call `await observer.emit_event(report)` with a DRIFTED report — verify both handlers received an ObservabilityEvent with event_type=DRIFT_DETECTED and report attached. Repeat with a handler that raises — verify the next handler still received the event and a WARNING was logged.

### Tests (TDD — write first, verify they fail)

- [X] T021 [P] [US2] Extend tests/unit/test_observability/test_observer.py with emit_event coverage: DRIFTED report → returned event has event_type=DRIFT_DETECTED; CLEAN report → STATE_CLEAN; ERROR report → DRIFT_ERROR; event.device_id and device_name match report; event.report is the same DriftReport instance; event.timestamp is UTC; emit dispatches to every registered handler on observer.event_bus in registration order; one handler raising does not block the next (both observed in handler-record list); AuditEntry appended with operation=EMIT_EVENT, device_id matching report, outcome=SUCCESS

### Implementation

- [X] T022 [US2] Add emit_event method to structural/observer.py StructuralObserver — maps report.status to EventType (DRIFTED→DRIFT_DETECTED, CLEAN→STATE_CLEAN, ERROR→DRIFT_ERROR); constructs ObservabilityEvent; calls self.event_bus.emit(event); appends AuditEntry(operation=EMIT_EVENT, device_id=report.device_id, component=component_name, outcome=SUCCESS, detail={"event_type": event.event_type.value}); returns the event

**Checkpoint**: US2 complete. emit_event returns the constructed event and dispatches it to every EventBus handler with per-handler exception isolation. The Orchestrator can register a Temporal-signal handler in a future iteration without changes to this block.

---

## Phase 5: User Story 3 — Record an Audit Entry for Every Automation Operation (Priority: P3)

**Goal**: `StructuralObserver.log_audit(entry)` exposes the explicit caller-driven audit recording surface (in addition to the automatic side-effects in detect_drift / emit_event). Callers (Orchestrator, Presentation) can construct an AuditEntry for any operation type and append it. The AuditLog query interface (`query_by_device`, `all`) — implemented in Foundational T011 — is now exercised end-to-end through the Observer.

**Independent Test**: Construct an AuditLog and StructuralObserver(audit_log=log). Call `await observer.log_audit(AuditEntry(operation=DETECT_DRIFT, device_id=uuid, component="orchestrator.workflow", outcome=SUCCESS, timestamp=UTC now))`. Call `log.query_by_device(uuid)` — verify the entry is returned. Construct AuditEntry directly from another component name and call log_audit — verify both entries are present in chronological order.

### Tests (TDD — write first, verify they fail)

- [X] T023 [P] [US3] Extend tests/unit/test_observability/test_observer.py with log_audit coverage: log_audit appends the provided entry verbatim to observer.audit_log (no wrapping); after multiple log_audit calls, observer.audit_log.query_by_device returns entries in chronological order; log_audit accepts entries from arbitrary component names (not just StructuralObserver); audit log queryable from outside the observer (audit_log.all() includes both side-effect entries from detect_drift/emit_event AND explicit log_audit entries — proves single-log scope); detect_drift + emit_event + log_audit on same device produce 3 entries in audit log

### Implementation

- [X] T024 [US3] Add log_audit method to structural/observer.py StructuralObserver — single line implementation: `self.audit_log.append(entry)`; method is async to satisfy ABC contract but body is synchronous (no I/O)

**Checkpoint**: US3 complete. The Observer's full surface (detect_drift, detect_drift_batch, emit_event, log_audit) is implemented. AuditLog accumulates entries from all three sources and is queryable by device.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Performance assertions, export completeness, lint/format, coverage validation

- [X] T025 [P] Add SC-001 performance assertion to test_observer.py (detect_drift completes in <100 ms for a DesiredState with 10 interfaces and 5 BGP sessions; measure with time.perf_counter; assert duration_ms < 100)
- [X] T026 [P] Add SC-005 batch assertion to test_observer.py (detect_drift_batch with 10 pairs — 4 clean, 4 drifted, 2 errored — returns BatchDriftReport with total=10 and per-status counts matching expected; every device UUID present in reports dict)
- [X] T027 [P] Add SC-003 import-isolation assertion to test_observability/test_models.py (importing snapl_observability and snapl_observability.structural makes no network connections — uses pytest-socket disable_socket fixture or simple import-then-no-exception verification)
- [X] T028 [P] Verify lint and format clean: uv run invoke lint && uv run invoke format — zero errors across packages/observability/ and tests/unit/test_observability/
- [X] T029 Verify all unit tests pass with ≥80% coverage on snapl_observability: uv run pytest tests/unit/test_observability/ -m unit -v --cov=snapl_observability --cov-report=term-missing
- [X] T030 Finalise package exports in packages/observability/snapl_observability/__init__.py (Observer, StructuralObserver, EventBus, AuditLog, ObserverError, all models — DriftItem, DriftReport, BatchDriftReport, ObservabilityEvent, AuditEntry, all enums — DriftStatus, EventType, AuditOperation, AuditOutcome)
- [X] T031 Run quickstart.md walkthroughs end-to-end against fixture data (single-device drift check, event emission with handler, audit log query, batch drift) — all examples execute without errors and produce expected output shapes

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2. The structural/diff.py and structural/observer.py constructor are the foundation for US2 and US3.
- **US2 (Phase 4)**: Depends on Phase 3 (StructuralObserver class must exist to add emit_event method)
- **US3 (Phase 5)**: Depends on Phase 3 (StructuralObserver class must exist to add log_audit method)
- **US2 and US3 are independent of each other** — can run in parallel after US1
- **Polish (Phase 6)**: Depends on all user stories complete

### User Story Dependencies

- **US1 (P1)**: The MVP — drift detection with audit side effect. Implements the constructor and the two drift methods.
- **US2 (P2)**: Adds emit_event method on the same class. Independent of US3.
- **US3 (P3)**: Adds log_audit method on the same class. Independent of US2.

US2 and US3 each modify structural/observer.py to add one method — they touch the same file, so are NOT marked [P] relative to each other. With one developer, US2 first then US3 is the natural order. With two developers, the changes are small enough that a quick rebase is trivial.

### Within Each User Story

1. Tests MUST be written and FAIL before implementation (TDD mandate)
2. Pure-function diff.py before observer.py orchestration (US1)
3. Observer constructor before any method extension (US1 → US2/US3)
4. Story complete and tested before moving to next priority

### Parallel Opportunities

**Phase 1**: T002, T003, T004 in parallel after T001
**Phase 2**: T005, T006, T007, T008 all in parallel (tests, different files); T009, T010, T011, T012 all in parallel (impl, different files); T013 → T014 sequential (T014 needs ABC name for placeholder export)
**Phase 3**: T015, T016, T017 in parallel (tests, different concerns within two files); T018 in parallel with the test writing; T019 → T020 sequential (T020 extends the same observer.py written in T019)
**Phase 4**: T021 (test) → T022 (impl) sequential
**Phase 5**: T023 (test) → T024 (impl) sequential
**Phase 6**: T025, T026, T027, T028 in parallel; T029 → T030 → T031 sequential

---

## Parallel Example: Phase 2 Foundational

```bash
# Launch all foundational tests together (TDD — must fail first):
Task: T005 "Pydantic model tests in test_models.py"
Task: T006 "Observer ABC contract tests in test_abc.py"
Task: T007 "AuditLog tests in test_audit.py"
Task: T008 "EventBus tests in test_events.py"

# After all four test files fail, implement in parallel:
Task: T009 "exceptions.py — ObserverError"
Task: T010 "models.py — all Pydantic models and enums"
Task: T011 "audit.py — AuditLog"
Task: T012 "events.py — EventBus"

# Then sequentially:
Task: T013 "abc.py — Observer ABC"
Task: T014 "__init__.py — placeholder exports"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001–T004)
2. Complete Phase 2: Foundational (T005–T014) — CRITICAL, blocks all stories
3. Complete Phase 3: User Story 1 (T015–T020) — write tests first, verify fail; implement diff.py then observer.py
4. **STOP and VALIDATE**: detect_drift returns correct DriftReport for clean / drifted / error cases; detect_drift_batch handles all three status outcomes
5. Proceed to US2 emit_event

### Incremental Delivery

1. Setup + Foundational → ABC, models, exceptions, AuditLog, EventBus ready
2. US1 → detect_drift / detect_drift_batch (MVP: produce DriftReport and BatchDriftReport)
3. US2 → emit_event (handlers can subscribe to drift events)
4. US3 → log_audit (callers can record their own audit entries)
5. Polish → perf assertions, exports finalised, coverage verified

### Single-Developer Strategy (Recommended)

1. Phase 1 (T001–T004) — package config and scaffolding
2. Phase 2 (T005–T014) — write T005–T008 tests in parallel, verify fail; then T009–T012 impl in parallel; T013 → T014 sequential
3. Phase 3 US1 (T015–T020) — write T015–T017 tests in parallel; T018 (diff.py) in parallel with tests; T019 → T020 sequential
4. Phase 4 US2 (T021–T022) — write T021 test; then T022 impl
5. Phase 5 US3 (T023–T024) — write T023 test; then T024 impl
6. Phase 6 Polish — T025–T028 in parallel; T029 → T030 → T031 sequential

---

## Notes

- TDD is mandatory: test file before source file, Red-Green-Refactor
- [P] tasks = different files, no dependencies on incomplete tasks
- The Observability block has NO integration tests — it has no external services to integrate with (see research R7). E2E coverage lives in the Orchestrator block.
- Pydantic v2 with `frozen=True, extra="forbid"` for every model (see research R5)
- AuditLog uses an internal threading.Lock — protects against future multi-threaded Orchestrator activities (see research R4)
- EventBus dispatches synchronously; per-handler try/except isolates failures (see research R3)
- detect_drift_batch is `async` for API uniformity but loops sequentially internally — pure CPU, no I/O to overlap (see research R6)
- No metrics export and no durable audit storage in this iteration (see research R4 and R8) — both deferred until a real consumer needs them
- `pytestmark = pytest.mark.unit` must appear after all imports in every unit test file (learned from 002-executor-gnmi)
- Commit after each task or logical group
- structural/observer.py is a single file extended across phases (US1 → US2 → US3) — natural sequencing
