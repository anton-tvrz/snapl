# Tasks: Device Telemetry

**Input**: Design documents from `/specs/016-device-telemetry/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/telemetry.md, quickstart.md

**Tests**: TDD is mandatory per CLAUDE.md and the constitution, for Python and for the UI alike. Every phase lists its tests first, and each test is run and seen to fail for the expected reason before the code it covers is written.

**Organization**: by block, in the order the loop needs them: collector, observability, the worker, the server, the page, archives, docs. User stories are tagged on each task (US1 interfaces, US2 health, US3 BGP, US4 CPU and memory, US5 archives).

**PR slicing** (Python slices < 400 lines of non-test code; UI slices as in spec 014):
- **PR A**: Phase 1 (the collector's stream)
- **PR B**: Phase 2 (the health rule)
- **PR C1**: Phase 3 (the audit event and the eight metric families)
- **PR C2**: Phase 4 (the runner and the bootstrap)
- **PR D1**: Phase 5 (health from the trail: `DeviceView.health`, `/health`, `snapl status`)
- **PR D2**: Phase 6 (the network from the store: six views, two units, `/network`)
- **PR E1**: Phase 7 (health on the page)
- **PR E2**: Phase 8 (the drawer's network and "The network" group)
- **PR F**: Phase 9 (archives, snapshots)
- **PR G**: Phase 10 (docs and governance)

Each PR says `Closes` a task issue under #312 and carries `changelog/312.added.md` (or appends to it).

**Lab rule for unattended runs**: a task marked **Needs the lab** uses the Compose stack, the real Prometheus, the maintainer's worker, the live dashboard or a device. Leave it unticked and defer it (dev/guides/overnight-runs.md). Tests may start their own `WorkflowEnvironment`, a metrics runtime on a free port, stub HTTP servers, a headless browser and the fixture server. That is not the lab. No test dials a device: the collector's tests replay the captures through a fake pygnmi client.

**#311**: nothing in Phases 1 to 10 waits for it. The lab tasks that do (T061, T062) say so.

**The gate for every slice**:

```bash
uv run invoke lint && uv run mypy packages/ && uv run bandit -r packages/ -c pyproject.toml -q && uv run pytest tests/unit -q
uv run invoke dashboard.test && uv run invoke dashboard.e2e
```

A slice that changes the contract also regenerates the OpenAPI snapshot and the UI's types (`uv run invoke dashboard.types`) in the same PR, so `types:check` passes.

**The look check for every UI slice**: as in spec 014's tasks.md (build, fixture server with `--sources up` on a free port, `e2e/look.mjs`, read the pictures, copy them to `.snapl/overnight/look/016-<slice>/` in the main checkout). Open spine-01's drawer: it is the longest (5 interfaces, 4 sessions).

**Rules that hold in every task**:
- A label value comes from intent (`device`, `interface`, `peer`) or an enum. Never from the device's answer, an error or a path.
- Health never changes a drift figure, the in-sync count, or what the loop repairs.
- Telemetry failing never stops the worker, fails a scan, or takes a page down. A device with no telemetry is `unknown`, never `unhealthy`.
- No vendor name outside `GnmiCollector` and the fixtures (`tests/unit/test_orchestrator/test_vendor_seams.py` holds this).
- Projection tests have a 1 s budget and run 4 to 8 times slower under coverage in CI. Read the measured time before changing a limit.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 to US5 from spec.md
- Exact file paths in every description

---

## Phase 0: Captures (done while planning)

- [x] T000 Capture sample-mode subscriptions from leaf-01 and spine-01 into `tests/unit/test_collector/fixtures/telemetry/`, with `dev/scripts/capture_srlinux_telemetry.py` and a README (research R1). *Done 2026-11-19 in the spec PR.*

---

## Phase 1: The Collector's Stream (US1, US3, US4) 🎯 MVP — PR A

**Goal**: `GnmiCollector.stream_telemetry` turns a device's subscription into vendor-neutral samples.

- [x] T001 [P] [US1] Tests first in `tests/unit/test_collector/test_models.py`: `TelemetryTarget` equality by device and items; `InterfaceSample`, `BgpSessionSample`, `PlatformSample` and `TelemetrySample` are frozen and accept `None` for every value (data-model.md).
- [x] T002 [US1] Add the five models to `packages/collector/snapl_collector/models.py` and export them from `__init__.py`.
- [x] T003 [US1] Tests first in `tests/unit/test_collector/test_abc.py`: `Collector` declares `stream_telemetry(target, *, interval_s)`; a subclass without it cannot be instantiated. Update every fake `Collector` in the tests that breaks (search for `Collector)` subclasses).
- [x] T004 [US1] Add the abstract method to `packages/collector/snapl_collector/abc.py`, with its docstring: the iterator ends by raising `TelemetryStreamError` when the stream breaks, and the caller restarts it (research R4). Add `TelemetryStreamError` to `exceptions.py` and `__init__.py`.
- [x] T005 [US1] Tests first in a new `tests/unit/test_collector/test_subscribe.py` for the bridge, with a fake pygnmi client whose `subscribe2` yields from a list, blocks, or raises: messages arrive on the event loop in order; closing ends the iterator cleanly, and a `CANCELLED` error after a requested stop is not raised; an error before a stop is raised as `TelemetryStreamError`; the thread is gone after the iterator ends; the subscribe request holds one `sample` entry per path with the interval in nanoseconds, `mode: stream` and `json_ietf`.
- [x] T006 [US1] Implement `packages/collector/snapl_collector/gnmi/subscribe.py`: `subscribe_sample(*, host, port, username, password, insecure, timeout, paths, interval_s) -> AsyncIterator[dict]`, running `subscribe2` in a thread and bridging with `loop.call_soon_threadsafe` (research R2).
- [x] T007 [US1] Tests first in `tests/unit/test_collector/test_collector.py`, replaying both captures from `fixtures/telemetry/` through a patched `subscribe_sample`: the paths built from a target (research R1's table, one per item, plus CPU and memory); the first sample has `sync=True` and every item; later samples carry one item each; counters are parsed from strings; `lo0` has state and no counters; spine-01's session reads `active` with 0 prefixes; CPU and memory parse; an update for an item outside the target is dropped; a value that does not parse is `None` and logged once; the device's notification timestamp becomes `timestamp`.
- [x] T008 [US1] Implement `GnmiCollector.stream_telemetry` in `packages/collector/snapl_collector/gnmi/collector.py`: build the paths from the target, parse each update by its list entry ignoring module prefixes (research R1), yield `TelemetrySample`s. The dial target and credentials resolve as `collect` does.
- [x] T009 Start `changelog/312.added.md`.

**Checkpoint**: both captures replay into the samples research R1 describes.

---

## Phase 2: The Health Rule (US2) — PR B

**Goal**: `Observer.evaluate_health` says healthy, unhealthy or unknown, with reasons, from intent's expectations and what was observed.

- [x] T010 [P] [US2] Tests first in `tests/unit/test_observability/test_models.py`: `DeviceHealthState`, `HealthReasonKind`, `HealthReason`, `HealthExpectation`, `ObservedItem`, `HealthObservation`, `DeviceHealth` (data-model.md); reasons render the fixed texts.
- [x] T011 [US2] Add them to `packages/observability/snapl_observability/models.py`.
- [x] T012 [US2] Table tests first in a new `tests/unit/test_observability/test_health.py`, one row per rule in research R6: never sampled → `unknown` "no telemetry yet"; last sample 31 s ago at 10 s → `unknown` with its time; 29 s ago → judged; an interface expected up and down for 5 s → healthy (debounce); for 11 s → unhealthy with `ethernet-1/49 down (expected up)`; an interface intended down and up → healthy; intended down and down → healthy; an expected item never reported → `absent`; a session `active` for 11 s → `BGP 10.10.1.1 active (expected established)`; a link down and its session down → both reasons, interfaces first; CPU and memory are not inputs at all (FR-008); reasons are ordered.
- [x] T013 [US2] Implement `packages/observability/snapl_observability/health.py` (pure, no clock) and add `evaluate_health(expectation, observation, *, now, interval_s)` to the `Observer` ABC in `abc.py`, implemented by `StructuralObserver` by calling it. Tests first in `test_abc.py` and `test_observer.py`.
- [x] T014 Append to the changelog.

**Checkpoint**: the table covers every row of research R6.

---

## Phase 3: The Audit Event and the Metric Families (US1 to US4) — PR C1

**Goal**: the worker can record a health change and export every figure, each with a fixed label set.

- [x] T015 [US2] Tests first in `tests/unit/test_orchestrator/test_models.py`: `AuditEventType.HEALTH_CHANGED` requires an `actor` and `payload.before` and `payload.after` from `DeviceHealthState`; other payload keys are free; the SQLite log stores and reads it back (`test_audit_sqlite.py`).
- [x] T016 [US2] Add the value and its validation to `packages/orchestrator/snapl_orchestrator/models.py`.
- [x] T017 [US1] Tests first in `tests/unit/test_orchestrator/test_metrics.py`, with the fakes in `metric_fakes.py`: one test per family holds its name, type and label set (contracts/telemetry.md); `direction` is an enum; the counter-delta rule (research R5): the first total adds 0, a rise adds the difference, a fall adds the new total, a restart of the stream resets the last totals; no recorder raises when the meter does.
- [x] T018 [US1] Implement the recorders in `packages/orchestrator/snapl_orchestrator/metrics.py`, taking a `MetricMeter` argument (the runtime's, not an activity's), and a small `CounterTotals` class for the delta rule.
- [x] T019 [US1] Tests first in `tests/unit/test_orchestrator/test_worker_metrics.py`: with a real runtime on a free port, record one sample of each family through `Runtime.metric_meter` and read the endpoint: each family appears under its name with its labels. Write the labels the runtime adds into research R5 and contracts/telemetry.md (settles R5's open point).
- [x] T020 Append to the changelog.

**Checkpoint**: `curl` on the test runtime's endpoint shows all eight families.

---

## Phase 4: The Runner and the Bootstrap (US1 to US4) — PR C2

**Goal**: a worker streams every device in intent, exports the figures and records health changes.

- [x] T021 [US1] Tests first in a new `tests/unit/test_orchestrator/test_telemetry_runner.py`, with a fake `Collector` whose streams are driven by the test, a fake intent store, a fake `Observer` or the real rule, the in-memory audit log and the metric fakes. Time is driven by the test, not slept:
  - one stream per device in intent, each with the target built from `DesiredState` (interfaces by name, sessions by peer address)
  - a sample updates the figures (via T018's recorders) and the observed items' `since`
  - a stream that raises moves to `broken`, retries after 1, 2, 4 … 30 s, and resets the backoff on `streaming`; other devices' streams carry on (FR-003)
  - every tick evaluates health; a change writes one `health_changed` event with the payload in data-model.md; no change writes nothing
  - every sixth tick re-reads intent: a changed target restarts that stream, a removed device's stream stops, a new device's starts (FR-004)
  - at start, the last recorded health per device is read back, so an unchanged state writes nothing
  - a clean stop records `unknown` "telemetry stopped" for each device whose last state was something else, and stops every thread
  - an intent store that fails keeps the current streams and retries at the next refresh
- [x] T022 [US1] Implement `packages/orchestrator/snapl_orchestrator/telemetry/runner.py` and `telemetry/__init__.py` (data-model.md's runner). The interval is the module constant `SAMPLE_INTERVAL_S = 10`.
- [x] T023 [US1] Tests first in `tests/unit/test_orchestrator/test_worker_run.py`: `run_worker` starts the runner beside `worker.run()` with the same audit log instance, the collector, the observer and the runtime's meter; stopping the worker stops the runner; `SNAPL_TELEMETRY=off` starts none; a runner that raises is logged and does not stop the worker; with metrics off, the runner records into a no-op meter.
- [x] T024 [US1] Wire it in `packages/orchestrator/snapl_orchestrator/worker/run.py`, and document `SNAPL_TELEMETRY` in its docstring and in `tasks/orchestrator.py`'s env list.
- [x] T025 Check `tests/unit/test_orchestrator/test_vendor_seams.py` still passes with the new package. Append to the changelog.
- [x] T026 **Needs the lab**: quickstart items 2 and 3 (six streams, the series count, the spines flagged and the leaves not). Write them into `specs/016-device-telemetry/evidence.md`. *Done 2026-11-20 after #311 and #317: six streams, 162 series, all six healthy, so no spine flagged. The runner also dials the integration tests' `test_edge` devices (#341).*

**Checkpoint**: a test worker with two fake devices streams, flags one and records the change once.

---

## Phase 5: Health From the Trail (US2) — PR D1

**Goal**: the server and the CLI say each device's health and its history.

- [x] T027 [US2] Tests first in `tests/unit/test_presentation/test_dashboard_projections.py`: the latest health per device from built `health_changed` events; `null` with none; health episodes opened by `unhealthy`, closed by `healthy` or `unknown`, clipped to the range, with the opening reasons; and **SC-007**: every existing projection (devices, runs, steps, drift timeline, drift summary, series) answers the same on the captured trail with health events interleaved as without them.
- [x] T028 [US2] Add `DeviceHealthState` (re-exported from observability), `HealthReasonView`, `DeviceHealthView`, `HealthEpisode`, `DeviceHealthEntry`, `FabricHealth`, `HealthResponse`, and `DeviceView.health` to `packages/presentation/snapl_presentation/dashboard/models.py`; the projections to `projections.py`.
- [x] T029 [US2] Tests first in `test_dashboard_app.py`: `/api/v1/devices` carries `health`; with the worker view showing no pollers, a recorded state reads `unknown`, "the worker is not running"; `GET /api/v1/health` defaults to 24 h, honours `start` and `end`, `422` for `start >= end` and an over-long range, unavailable when the audit trail is; HEAD is `405`; every drift figure and the in-sync count are the same with an unhealthy device (FR-010).
- [x] T030 [US2] Add the route and the field to `app.py`.
- [x] T031 [US2] Tests first in `tests/unit/test_presentation/test_cli.py` and `test_render.py`: `snapl status` prints the `fabric health` line and `--json` the `health` key; it never changes the exit code; with nothing recorded it says `no telemetry recorded`; `snapl audit --device` renders a `health_changed` event with its reasons on one line each.
- [x] T032 [US2] Implement T031 in `cli.py` and `render.py`.
- [x] T033 [US2] Add health events to the fixture server in `dev/scripts/serve_dashboard_fixtures.py`: spine-01 and spine-02 unhealthy for their sessions (today's lab), leaf-01 unhealthy for an hour with `ethernet-1/49 down (expected up)` then healthy, leaf-03 `unknown` for 2 minutes. Tests first in `test_ui_fixtures.py`.
- [x] T034 Regenerate the OpenAPI snapshot and the UI's types. Append to the changelog.

**Checkpoint**: the fixture server's `/api/v1/health` shows leaf-01's episode.

---

## Phase 6: The Network From the Store (US1, US3, US4) — PR D2

**Goal**: six named views and a fabric-wide `/network`.

- [x] T035 [US1] Tests first in `test_dashboard_prometheus.py` and `test_dashboard_models.py`: the six view names, their queries, units and series labels (contracts/telemetry.md); `bytes_per_second` and `percent` units; synthetic range answers in the shape of spec 012's fixtures parse; `bgp-established` has no window note.
- [x] T036 [US1] Add the views to `MetricsViewName` and `VIEWS` in `prometheus.py`, and the units to `MetricUnit` in `models.py`.
- [x] T037 [US1] Tests first for `/api/v1/network` in `test_dashboard_app.py`, with a stub Prometheus: items come from intent in name and peer order; values from instant queries; an item with no series is `null`, never 0; an interface intended disabled has `expected: "down"`; a disabled session has `expected: null`; the store down → unavailable with reason and remedy (US1 scenario 4); the SoT down → the devices it last named, as `/devices` does.
- [x] T038 [US1] Add `InterfaceState`, `SessionState`, `DeviceNetwork`, `FabricNetwork`, `NetworkResponse` to `models.py`, the instant queries to `prometheus.py`, and the route to `app.py`.
- [x] T039 [US1] Synthetic network series in the fixture server: traffic on every link, errors on one interface, the spines' sessions at 0 established, CPU and memory with the lab note. Tests first in `test_ui_fixtures.py`.
- [x] T040 Regenerate the OpenAPI snapshot and the UI's types. Append to the changelog.

**Checkpoint**: `curl /api/v1/network` on the fixture server lists spine-01's 5 interfaces and 4 sessions.

---

## Phase 7: Health on the Page (US2) — PR E1

**Goal**: health is visible next to drift without changing it.

- [x] T041 [US2] Tests first: `src/lib/` health helpers (mark per state, the number row's context "2 unhealthy" or "all healthy"); `NumberRow.test.tsx`; the fabric panel's chips in `components/topology/` (a mark beside the drift colour, the drift colour unchanged, a title naming the reasons); `DeviceDrawer.test.tsx` (a "Health" section first: state, reasons, since; "the worker is not running"; nothing when `health` is `null`).
- [x] T042 [US2] Implement T041. Add `health` to the data provider (`data/provider.ts`, `http.ts`, `inline.ts`).
- [x] T043 [US2] A health history strip under the drift timeline, from `/api/v1/health` episodes, in the timeline's frame. Tests first. *Built as a line along the foot of each device's drift lane, on the lanes' axis (which now starts before the first health change in range), with its key in the lanes' legend line: a separate strip of any height pushed the first metrics chart past spec 014's SC-003 limit of 2000 px (1990 px before this PR, 2042 px with a one-row strip).*
- [x] T044 [US2] The look check, with spine-01's drawer open. Append to the changelog.

---

## Phase 8: The Drawer's Network and "The Network" Group (US1, US3, US4) — PR E2

**Goal**: the operator sees interfaces, sessions, CPU and memory.

- [x] T045 [US1] Tests first in `DeviceDrawer.test.tsx`: an "Interfaces" table (name, up or down against expected, traffic in and out, errors and discards when not zero), a "BGP sessions" table (peer, established or not against expected, prefixes), CPU and memory; `null` values show as "no data", never 0; the network view unavailable shows its reason.
- [x] T046 [US1] Implement it, reading the device's entry from `/network` (one request for the page). *The network sections follow what snapl found (paths, last scan and deploy, recent runs), keeping the drawer drift-first; CPU and memory are the latest values from `/network`, and their history over the range is in "The network" group. At a past time the drawer says these show only the present, since `/network` is now's. A flag's "expected …" is read out and in its title, not shown: shown, it wrapped the state column to three lines (look check).*
- [x] T047 [US1] Tests first in `src/lib/metrics.test.ts` and `components/panels/Metrics.test.tsx`: "The network" group after spec 015's four, folded by default, the six views with their title suffixes, the `bytes_per_second` and `percent` units formatted (`1.2 kB/s`, `94%`), and the lab note on CPU and memory.
- [x] T048 [US1] Implement T047 in `src/lib/metrics.ts` and `Metrics.tsx`. *With the decision on #312 (2026-11-20): `interface-traffic` is summed by device (6 series, contract amended); any view over six series draws the six highest by peak and says how many it left out; errors or discards that stayed at zero say so instead of drawing flat lines.*
- [x] T049 The look check, with spine-01's drawer open and the network group unfolded. Measure the page height against #305's 7086 px and record it in the PR. Append to the changelog.

---

## Phase 9: Archives and Snapshots (US5) — PR F

**Goal**: a recording of an incident shows the fault.

- [x] T050 [US5] Tests first in `test_dashboard_archive.py`, `test_dashboard_snapshot.py` and `tests/unit/test_tasks/test_history.py`: the archive export, the archive reader and the snapshot bundle carry `/health`, `/network` (at the archive's end) and the six views; an archive made before this feature answers each as "not in this archive"; `DeviceView.health` in an archive is what was recorded, never "the worker is not running".
- [x] T051 [US5] Implement T050 in `tasks/history.py`, `archive.py` and `snapshot.py`. The six views follow spec 015's snapshot step rule. *In archive mode `/network` answers the exported figures and `/health` is computed from the archived trail, as `/drift-summary` is; it says "not in this archive" only when the archive has no health export and its trail no health event. `DeviceView.health` in an archive was already the recorded one (C2); a test now holds it.*
- [x] T052 [US5] Measure a seven-day snapshot on the test fixtures before and after (as spec 015 T045) and record both sizes in research.md. Append to the changelog. *267,413 to 416,171 bytes (+56%), research R12.*

---

## Phase 10: Docs and Governance — PR G

- [x] T053 [P] `dev/adr/0005-dashboard-and-metrics-stack.md`: a decision for device telemetry (streams in the worker, not workflows; health in the audit trail; the eight families). Amend 010's versioning rule with `DeviceHealthState` (`specs/010-dashboard-read-api/contracts/dashboard-api.md`).
- [ ] T054 [P] **Needs the lab** (after T026: its evidence is what the README may claim; amended 2026-11-20 in PR G) `README.md`: replace "No device telemetry" and "Config-level verification only" with what the lab has shown so far, and only that (T026's evidence; leave the words for anything still unproven). Add the caveat on the eBGP underlay while #311 is open. `docs/demo-scenarios.md`: a scenario "in sync, and still broken" (the spines today). *#311 closed on 2026-11-19 (#316, #318): the caveat is now #317's (sessions establish but exchange no routes), and whether any device is "in sync, and still broken" today is for T026 to show.*
- [x] T055 [P] `CLAUDE.md` "Recent Changes" and `AGENTS.md` if it lists the blocks' capabilities. `dev/knowledge/host-resource-registry.md` if anything new listens (nothing should). *Nothing new listens: the streams dial out to each device's gNMI port, and the figures use the worker's existing 18091. `AGENTS.md`'s block table names device health and telemetry streams.*
- [x] T056 Finish `changelog/312.added.md` as one readable paragraph. *The README's "No device telemetry" stays until T054, so the paragraph ends by saying none of this has been run on the lab.*

---

## Phase 11: Lab Evidence

All **Needs the lab**. Results go in `specs/016-device-telemetry/evidence.md`.

- [x] T057 [US1] **Needs the lab**: quickstart item 1, the fault captures as fixtures, each read by a test (Principle III). *Done 2026-11-20: `srlinux_v26_3_1_leaf01_interface_down_up.json` and `srlinux_v26_3_1_spine01_peer_paused.json`, read through the collector (`TestStreamTelemetryFaultCaptures`) and the runner (`TestLabCaptures`). The pause is 100 s, past the 90 s hold time.*
- [x] T058 [US1] **Needs the lab**: quickstart item 4 (SC-001). *Done 2026-11-20: shown down on the page in 24 s, flagged in 19 s, drifted and unhealthy as two marks, cleared 48 s after the reconcile.*
- [ ] T059 [US2] **Needs the lab**: quickstart item 5 (SC-006).
- [ ] T060 **Needs the lab**: quickstart item 6 (SC-005).
- [ ] T061 [US3] **Needs the lab**, **after #311**: quickstart item 7 (SC-002, SC-003).
- [x] T062 [US5] **Needs the lab**: quickstart item 8 (SC-008), and recapture the Prometheus fixtures for the six views. *Done 2026-11-20: SC-008 holds (the archive's health history has every episode, with every live source unreachable). US5 scenario 1 doesn't: the archive's network views are at a 42 min step and miss a minutes-long fault (#351). Snapshot 3.50 MB for 7 days. The six fixtures are captured and read by tests.*

---

## Dependencies

- A and B in either order. C1 needs B's `DeviceHealthState`. C2 needs A, B and C1.
- D1 needs C1. D2 needs C1's names only (the fixture series are synthetic).
- E1 needs D1. E2 needs D2.
- F needs D1 and D2. G last.
- T026 needs C2 merged and the worker restarted on main. T061 needs #311.

## Deferred to a later spec

- Any response to health beyond the flag: alerts, notifications, repairs.
- Telemetry for items not in intent, per-queue counters, optics.
- A second vendor.
