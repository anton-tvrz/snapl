# Tasks: NAF Intent — Source of Truth Integration

**Input**: Design documents from `/specs/001-naf-intent-sot/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/intent-store.md, quickstart.md

**Tests**: TDD is mandatory per CLAUDE.md — "Always produce the test file first. This is a hard rule, not a suggestion." Test tasks are included for all phases.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- Exact file paths included in all descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project directory structure, Docker infrastructure, and test scaffolding

- [X] T001 Create package directory structure (infrahub/, schemas/base/, schemas/extensions/, seed/dcfabric/) in packages/intent/snapl_intent/
- [X] T002 [P] Create Docker Compose stack (Neo4j, Redis, RabbitMQ, Infrahub server) in development/docker-compose.yml
- [X] T003 [P] Create environment variable template (INFRAHUB_ADDRESS, INFRAHUB_API_TOKEN) in development/.env.example
- [X] T004 [P] Add shared test fixtures (mock_infrahub_client, spine_leaf_topology) to tests/conftest.py and create tests/integration/test_intent/__init__.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core domain types, ABC, exceptions, and client wrapper that ALL user stories depend on

**CRITICAL**: No user story work can begin until this phase is complete

### Tests (TDD — write first, verify they fail)

- [X] T005 [P] Write unit tests for Pydantic models (Device, Interface, BGPSession, DesiredState, Schema, ProvisionResult, SeedResult, DeleteResult) in tests/unit/test_intent/test_models.py
- [X] T006 [P] Write unit tests for IntentStore ABC contract enforcement (abstract methods, cannot instantiate) in tests/unit/test_intent/test_abc.py

### Implementation

- [X] T007 [P] Implement exception hierarchy (IntentError, IntentConnectionError, IntentNotFoundError, IntentValidationError, IntentSchemaError, IntentDeletionError) in packages/intent/snapl_intent/exceptions.py
- [X] T008 Implement Pydantic models per contract (Device, Interface, BGPSession, DesiredState, Schema, ProvisionResult, SeedResult, DeleteResult) in packages/intent/snapl_intent/models.py
- [X] T009 Implement IntentStore ABC with all async method signatures per contracts/intent-store.md in packages/intent/snapl_intent/abc.py
- [X] T010 Implement Infrahub async client wrapper (connection via env vars, auth, 10s timeout, error mapping to domain exceptions) in packages/intent/snapl_intent/infrahub/client.py
- [X] T011 Update package exports (models, ABC, exceptions) in packages/intent/snapl_intent/__init__.py and create empty packages/intent/snapl_intent/infrahub/__init__.py placeholder

**Checkpoint**: Foundation ready — models, ABC, exceptions, client wrapper all tested and passing. User story implementation can begin.

---

## Phase 3: User Story 1 — Retrieve Desired Network State (Priority: P1) MVP

**Goal**: Consumers can query the complete desired state for any device or set of devices, with filtering by use case, role, or name. Returns DesiredState objects with device, interfaces, and BGP sessions.

**Independent Test**: Query mock client for a specific leaf device — verify returned DesiredState includes correct interfaces and BGP sessions. Query by role=spine — verify all spines returned. Query non-existent device — verify empty list.

### Tests (TDD — write first, verify they fail)

- [X] T012 [P] [US1] Write unit tests for InfrahubIntentStore.get_desired_state() with mock client (single device by ID, filtered by role, filtered by use_case, combined filters, not found returns empty list, connection error raises IntentConnectionError) in tests/unit/test_intent/test_store.py

### Implementation

- [X] T013 [US1] Create InfrahubIntentStore class scaffolding with get_desired_state() implementation (GraphQL query, node-to-Pydantic mapping) in packages/intent/snapl_intent/infrahub/store.py
- [X] T014 [US1] Add InfrahubIntentStore export to packages/intent/snapl_intent/infrahub/__init__.py (placeholder created in T011)

**Checkpoint**: US1 unit tests pass. get_desired_state() returns correct DesiredState from mock Infrahub client. Integration tests deferred to Phase 4 (require seeded data).

---

## Phase 4: User Story 2 — Seed Network Intent Data (Priority: P2)

**Goal**: Two-phase seeding: (1) provision schema into Infrahub using 3 dependency-ordered batches (base -> extensions -> project-specific), (2) ingest declarative YAML seed data with dependency-ordered upsert (Org -> Location -> Manufacturer -> Platform -> DeviceType -> ASN -> VRF -> IPPrefixes -> Devices -> IPAddresses -> Interfaces -> BGPPeerGroups -> BGPSessions). Both phases are idempotent.

**Independent Test**: Provision dcfabric schema (verify 3-batch ordering), ingest seed dataset (2 spines, 4 leaves with interfaces and BGP), confirm all devices retrievable with correct relationships. Re-run both — no errors, no duplicates.

### Schema & Seed Data Files

- [X] T015 [P] [US2] Create Batch 1 base schema YAML files (dcim, ipam, location, organization from Infrahub schema-library) in packages/intent/snapl_intent/schemas/base/
- [X] T016 [P] [US2] Create Batch 2 extension schema YAML files (routing_bgp, vrf from Infrahub schema-library) in packages/intent/snapl_intent/schemas/extensions/
- [X] T017 [P] [US2] Create Batch 3 project-specific schema YAML for DcimDevice extensions (role attribute, use_case relationship) in packages/intent/snapl_intent/schemas/network_device.yml
- [X] T018 [P] [US2] Create Batch 3 project-specific schema YAML for InterfacePhysical extensions (peer_device, peer_interface attributes) in packages/intent/snapl_intent/schemas/network_interface.yml
- [X] T019 [P] [US2] Create Batch 3 business intent stub schema YAML (ApplicationService, ServiceEndpoint, ConnectivityIntent, InfrastructureBinding, FirewallRuleSet, OperationalOverride, OverrideWindow, OverrideAction) in packages/intent/snapl_intent/schemas/business_intent.yml
- [X] T020 [P] [US2] Create datacenter fabric seed data YAML (2 spines, 4 leaves, interfaces, IP addresses, BGP sessions per topology) in packages/intent/snapl_intent/seed/dcfabric/topology.yml

### Tests (TDD — write first, verify they fail)

- [X] T021 [P] [US2] Write unit tests for 3-batch schema provisioning logic (batch discovery, ordering, idempotent load, schema validation error) in tests/unit/test_intent/test_schema.py
- [X] T022 [P] [US2] Write unit tests for dependency-ordered data ingestion (YAML parsing, upsert semantics, dependency ordering, validation rejection, re-run produces no duplicates) in tests/unit/test_intent/test_seed.py
- [X] T023 [P] [US2] Write unit tests for InfrahubIntentStore.provision_schema() and seed() with mock client (success, schema not provisioned error, validation error, branch parameter) in tests/unit/test_intent/test_store.py (append to existing)

### Implementation

- [X] T024 [US2] Implement 3-batch schema provisioning logic (discover YAML files in schemas/base/ -> extensions/ -> project, load via SDK client.schema.load()) in packages/intent/snapl_intent/infrahub/schema.py
- [X] T025 [US2] Implement dependency-ordered data ingestion logic (parse seed YAML, resolve dependencies, upsert via SDK in correct order) in packages/intent/snapl_intent/infrahub/seed.py
- [X] T026 [US2] Add provision_schema() and seed() methods to InfrahubIntentStore in packages/intent/snapl_intent/infrahub/store.py

### Integration Tests (require running Infrahub via docker compose)

- [X] T027 [US2] Write integration test for schema provisioning against live Infrahub (3-batch load, idempotent re-run) in tests/integration/test_intent/test_infrahub_schema.py
- [X] T028 [US2] Write integration test for data seeding against live Infrahub (full dcfabric topology, upsert on re-run) in tests/integration/test_intent/test_infrahub_seed.py
  - **Scope note**: Milestone A of T028-followup landed — attribute-only sections plus `device_types`, `autonomous_systems`, and `devices` (with `manufacturer`, `organization`, `device_type`, `platform`, `location`, `asn` relationships) now load through `SEED_ORDER`. Remaining sections (`vrfs`, `ip_prefixes`, `interfaces`, `bgp_peer_groups`, `bgp_sessions`) are still parked in `SEED_DEFERRED`.
- [ ] T028-followup [US2] Milestones for promoting the remaining `SEED_DEFERRED` sections in `packages/intent/snapl_intent/infrahub/seed.py`:
  - [X] **Milestone A — device-chain relationships**: per-section `_Rel` declaration + peer-id substitution at upsert time, for `device_types`, `autonomous_systems`, `devices`. Raises `IntentValidationError` when a referenced peer does not exist.
  - [ ] **Milestone B — IP-namespace bootstrap**: materialise the default namespace so `vrfs` + `ip_prefixes` can promote out of deferred.
  - [ ] **Milestone C — interfaces + IP addressing**: resolve `device` parents, materialise `management_ip` on devices and per-link `ip_address` entries. Expand `test_seed.py::test_ingest_second_run_upserts_in_place` to cover device idempotency with interfaces in play.
  - [ ] **Milestone D — BGP peer-groups/sessions**: `RoutingProtocol` inheritance requires `device` + `vrf`, but topology declares one shared peer-group. Resolved via **shadow copies** — materialise N `BGPPeerGroup` rows at seed time from one logical YAML declaration; schema left unchanged.
- [X] T029 [US1] Write integration test for desired state query against live Infrahub (single device, role filter, use_case filter, empty result) in tests/integration/test_intent/test_infrahub_query.py
  - **Scope note**: Device fixture is still created inline via the SDK (in `_query_devices_seeded`) because `test_edge` isn't yet in the packaged seed (see Phase 6 T034). The inline path can be removed once the per-use-case topology tree lands.

**Checkpoint**: US1 + US2 fully functional end-to-end. Schema provisioning loads 3 batches including business intent stubs. Seed ingests dcfabric topology. get_desired_state returns seeded data with correct relationships. All unit and integration tests pass.

---

## Phase 5: User Story 3 — Inspect Data Model Definitions (Priority: P3)

**Goal**: Developers and operators can query the schema definition for a use case and receive a structured Schema object listing entities, fields, version, and source files.

**Independent Test**: Provision dcfabric schema, call get_schema("dcfabric") — verify returned Schema lists expected entities and source files. Call get_schema("nonexistent") — verify IntentSchemaError raised.

### Tests (TDD — write first, verify they fail)

- [X] T030 [P] [US3] Write unit tests for InfrahubIntentStore.get_schema() with mock client (valid use case returns Schema, unknown use case raises IntentSchemaError) in tests/unit/test_intent/test_store.py (append to existing)

### Implementation

- [X] T031 [US3] Add get_schema() method to InfrahubIntentStore (query Infrahub schema registry, map to Schema model) in packages/intent/snapl_intent/infrahub/store.py

**Checkpoint**: US3 complete. get_schema() returns structured schema definition with entities and source files.

---

## Phase 6: User Story 4 — Support Independent Use Cases (Priority: P4)

**Goal**: Operations on one use case produce zero side effects on another. Use case filtering is absolute — query with use_case filter returns only matching devices.

**Independent Test**: Seed two use cases with different devices, modify one, verify the other is unchanged. Query with use_case filter — only matching devices returned.

### Tests (TDD — write first, verify they fail)

- [ ] T032 [P] [US4] Write unit tests for use case isolation (get_desired_state with use_case filter returns only matching devices, seed to one use case does not affect another) in tests/unit/test_intent/test_store.py (append to existing)

### Fixture & Implementation

- [ ] T033 [US4] Validate and enforce use_case filter in all InfrahubIntentStore query paths in packages/intent/snapl_intent/infrahub/store.py
- [ ] T034 [P] [US4] Create minimal second-use-case seed fixture (2 devices tagged use_case=test_edge, reusing dcfabric schema types) in packages/intent/snapl_intent/seed/test_edge/topology.yml
- [ ] T035 [US4] Write integration test for cross-use-case isolation against live Infrahub (seed dcfabric + test_edge fixtures, verify queries return only matching use_case, verify modifications to one use case do not affect the other) in tests/integration/test_intent/test_infrahub_query.py (append to existing)

**Checkpoint**: US4 complete. Use case isolation verified — operations on dcfabric do not affect the test_edge fixture, satisfying SC-004.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Coordinated deletion (completes FR-011), Infrahub branch support, missing integration test coverage, and end-to-end validation.

- [ ] T036 [P] Write unit tests for InfrahubIntentStore.delete_device() (success removes device + interfaces + BGP sessions, not found raises IntentNotFoundError, deletion error raises IntentDeletionError) in tests/unit/test_intent/test_store.py (append to existing)
- [ ] T037 Add delete_device() method to InfrahubIntentStore — fulfills FR-011. The method exposes deletion as an operation callers can gate; the Intent module itself does not query the Collector — coordination is the caller's (Orchestrator's) responsibility. File: packages/intent/snapl_intent/infrahub/store.py
- [ ] T038 [P] Add Infrahub branch parameter support to seed() and get_desired_state() in packages/intent/snapl_intent/infrahub/store.py
- [ ] T039 [P] Write integration test for get_schema() against live Infrahub (provision dcfabric schema, verify Schema object returned with correct entities and source files, verify unknown use case raises IntentSchemaError) in tests/integration/test_intent/test_infrahub_schema.py (append to existing)
- [ ] T040 Add performance assertions to integration tests validating SC-001 (<5s single-device retrieval) and SC-007 (<10s error on unavailable SoT) in tests/integration/test_intent/test_infrahub_query.py
- [ ] T041 Run quickstart.md validation end-to-end against live Infrahub (provision -> seed -> query -> inspect schema)
- [ ] T042 Verify all unit tests pass: uv run pytest tests/unit/test_intent/ -v
- [ ] T043 Verify all integration tests pass against live Infrahub: uv run pytest tests/integration/test_intent/ -v

> **Note on SC-002 (<2min seed of 50 devices)**: Current dcfabric fixture (T020) has ~6 devices. A 50-device fixture is deferred to a follow-up feature — current seed implementation is measured against the 6-device topology, and scaling to 50 is an incremental data change (no new code).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2. Unit tests only — integration deferred.
- **US2 (Phase 4)**: Depends on Phase 2. YAML files (T015-T020) can start in parallel with US1 (Phase 3).
- **US3 (Phase 5)**: Depends on Phase 2. Can proceed after US2 schema provisioning works.
- **US4 (Phase 6)**: Depends on US1 + US2 (needs both query and seed to test isolation).
- **Polish (Phase 7)**: Depends on all user stories complete.

### User Story Dependencies

- **US1 (P1)**: Unit tests independent. Integration tests (T029) depend on US2 — placed in Phase 4.
- **US2 (P2)**: Independent from US1 for implementation. YAML files can start immediately after Phase 2.
- **US3 (P3)**: Depends on US2 schema provisioning (get_schema reads provisioned schemas).
- **US4 (P4)**: Depends on US1 + US2 (needs query and seed to verify cross-use-case isolation).

### Within Each User Story

1. Tests MUST be written and FAIL before implementation (TDD mandate)
2. Schema/data YAML files before logic that reads them
3. Unit tests before integration tests
4. Core logic modules (schema.py, seed.py) before store method wiring
5. Story complete and tested before moving to next priority

### Parallel Opportunities

**Phase 1**: T002, T003, T004 can all run in parallel (after T001)
**Phase 2**: T005, T006 in parallel; T007 in parallel with tests; T008 after T005 passes
**Phase 3**: T012 can start immediately after Phase 2
**Phase 4**: T015-T020 all in parallel (independent YAML files); T021-T023 all in parallel (independent test files)
**Phase 5**: T030 can start as soon as store.py exists from Phase 3
**Phase 6**: T032, T034 can run in parallel (test and fixture are independent)
**Phase 7**: T036, T038, T039 can run in parallel

---

## Parallel Example: User Story 2

```bash
# Launch all schema/seed YAML files together (T015-T020):
Task: T015 "Batch 1 base schema YAML in schemas/base/"
Task: T016 "Batch 2 extension schema YAML in schemas/extensions/"
Task: T017 "Batch 3 network_device.yml"
Task: T018 "Batch 3 network_interface.yml"
Task: T019 "Batch 3 business_intent.yml"
Task: T020 "Seed data YAML in seed/dcfabric/topology.yml"

# Launch all unit tests together (T021-T023):
Task: T021 "Unit tests for schema provisioning in test_schema.py"
Task: T022 "Unit tests for data ingestion in test_seed.py"
Task: T023 "Unit tests for store provision/seed in test_store.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1 (unit tests only)
4. **STOP and VALIDATE**: get_desired_state() works with mock client
5. Proceed to US2 for full end-to-end capability

### Incremental Delivery

1. Setup + Foundational -> Foundation ready
2. US1 unit tests -> MVP query capability validated
3. US2 YAML files + schema + seed -> Full provision-seed-query pipeline
4. US1 integration tests -> Complete US1 end-to-end validation
5. US3 schema inspection -> Developer tooling
6. US4 use case isolation (with test_edge fixture) -> Multi-use-case validation (SC-004)
7. Polish -> Deletion (FR-011), branching, integration test coverage additions, performance assertions, quickstart validation

### Single-Developer Strategy (Recommended)

1. Phase 1 + 2 sequentially (foundation)
2. Phase 3 US1 (unit tests + store scaffolding)
3. Phase 4 US2 (YAML files in parallel, then tests, then implementation, then integration tests including US1 integration)
4. Phase 5 US3 + Phase 6 US4 sequentially
5. Phase 7 Polish

---

## Notes

- TDD is mandatory: test file before source file, Red-Green-Refactor
- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- Schema YAML files use Infrahub schema-library format (ported from predecessor project-network-synapse-quattro)
- Seed data dependency order: Org -> Location -> Manufacturer -> Platform -> DeviceType -> ASN -> VRF -> IPPrefixes -> Devices -> IPAddresses -> Interfaces -> BGPPeerGroups -> BGPSessions
- Integration tests require running Infrahub: docker compose -f development/docker-compose.yml up -d
- test_store.py is a single file that grows across phases (US1 -> US2 -> US3 -> US4 -> Polish)
- Business intent stubs (T019) are schema-only — no seed data, no retrieval, no tests needed
- Commit after each task or logical group
