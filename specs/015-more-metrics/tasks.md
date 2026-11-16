# Tasks: More Metrics

**Input**: Design documents from `/specs/015-more-metrics/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/metrics-views.md, quickstart.md

**Tests**: TDD is mandatory per CLAUDE.md and the constitution, for Python and for the UI alike. Every phase lists its tests first, and each test is run and seen to fail for the expected reason before the code it covers is written.

**Organization**: Tasks are grouped by user story (US1 to US5). The audit-trail figures come first, because they touch nothing that PR #241 rewrites and need no metrics store.

**PR slicing** (Python slices < 400 lines of non-test code; UI slices as in spec 014):
- **PR A**: Phase 1 (the four history series)
- **PR B**: Phase 2 (the drift summary route, and both in the archive, the snapshot and the fixture server)
- **PR C**: Phase 3 (the page: fabric history, drift by device, the runs card)
- **PR D**: Phase 4, **After #241** (views over exported families and the `count` unit). Split in two: **D1** the server and the plain page, **D2** the designed page's panels and groups.
- **PR E**: Phase 5, **After #241** (the worker's three counters and their views)
- **PR F**: Phase 6, **After #241** (scan steps)
- **PR G**: Phase 7 (files, docs and governance)

Each PR says `Closes` a task issue under #249 and carries `changelog/249.added.md` (or appends to it).

**Lab rule for unattended runs**: a task marked **Needs the lab** runs the Compose stack, the real Prometheus, the maintainer's worker, the live dashboard or a device. Leave it unticked and defer it (dev/guides/overnight-runs.md). Tests may start their own `WorkflowEnvironment.start_local()` server, a metrics runtime on a free port, stub HTTP servers, a headless browser and the fixture server. That is not the lab.

**After #241**: PR #241 rewrites `worker/metrics.py`, `dashboard/prometheus.py`, `workflows/scan_drift.py` and adds `workflows/audit.py`. Before starting a slice marked **After #241**, run `gh pr view 241 --json state -q .state`.
- `MERGED`: start the slice, and build on what #241 added (its rate helper, its `record_event` helper, its bucket table).
- anything else: do not start it. Take the next slice in the list that is not marked. When only **After #241** slices and lab tasks are left, write the final report saying the run is waiting for #241, and stop.

**Spec 014 first**: this spec's UI slices (C, D2) build on spec 014's frame, scale, number row and metrics panel. If a spec 014 slice they need has not merged, stop and say so in the report.

**The gate for every slice**:

```bash
uv run invoke lint && uv run mypy packages/ && uv run bandit -r packages/ -c pyproject.toml -q && uv run pytest tests/unit -q
uv run invoke dashboard.test && uv run invoke dashboard.e2e
```

A slice that changes the contract also regenerates the OpenAPI snapshot and the UI's types (`uv run invoke dashboard.types`) in the same PR, so `types:check` passes.

**The look check for every UI slice**: as in spec 014's tasks.md (build, fixture server with `--sources up` on a free port, `e2e/look.mjs`, read the pictures, copy them to `.snapl/overnight/look/015-<slice>/` in the main checkout).

**Rules that hold in every task**:
- Additions only to the v1 contract. `/history` with no `series=` answers as before.
- No label outside the contract. State, kind, outcome and reason come from enums.
- A view says whether it is a mean, a percentile, a count or a current value. Durations from the trail are scan to scan.
- Projection tests have a 1 s budget and run 4 to 8 times slower under coverage in CI. Read the measured time before changing a limit.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 to US5 from spec.md
- Exact file paths in every description

---

## Phase 1: The Four History Series (US2) 🎯 MVP — PR A

**Goal**: the history API answers devices in sync, open drifts, runs and repair durations from the audit trail.

- [x] T001 [US2] Tests first in `tests/unit/test_presentation/test_dashboard_projections.py` (or the file that holds `project_series`' tests), on the captured trail and on built events: `in_sync_devices` has one point per scan with the count of in-intent devices in sync, and a device with a scan error is not in sync; `open_drifts` follows the episodes; `runs` buckets by start time with `by_outcome` keys `<trigger>.<outcome>`; `repair_duration` has one point per closed episode at `closed_at` with `duration_s`; a range with no scan gives no points; a device removed from intent is not counted.
- [x] T002 [US2] Add the four values to `SeriesName` in `packages/presentation/snapl_presentation/dashboard/models.py` and implement them in `project_series` in `projections.py` (research R5), reusing the timeline's episodes and `_split_runs`. `value` stays an `int`.
- [x] T003 [US2] Tests first in `tests/unit/test_presentation/test_dashboard_app.py`: `/api/v1/history` with no `series=` returns exactly the original four; with `series=runs&series=in_sync_devices` it returns those; an unknown name is `422`; the `at`-style past range still works.
- [x] T004 [US2] In `app.py`, make the default the original four (today it is "all"), and update the parameter's description. Check `archive.py` and `snapshot.py` still ask for what they need.
- [x] T005 [US2] Regenerate the OpenAPI snapshot and `packages/presentation/ui/src/api/schema.d.ts`. Start `changelog/249.added.md`.

**Checkpoint**: `curl '/api/v1/history?series=runs'` on the fixture server shows the arc's runs by trigger.

---

## Phase 2: The Drift Summary, and Carrying Both (US2) — PR B

**Goal**: drift by device is one request, and the archive, the snapshot and the fixture server have everything from Phases 1 and 2.

- [ ] T006 [US2] Tests first for `project_drift_summary` in the projections tests: episodes counted per device and clipped to the range; `open` at the range's end; `drifted_s` scan to scan inside the range; `most_missing` and `most_undesired`, with `None` for scans before the undesired split; the order (most episodes, then longest, then name); a device with no episode is left out.
- [ ] T007 [US2] Add `DeviceDriftSummary`, `DriftSummary` and `DriftSummaryResponse` to `models.py` and `project_drift_summary` to `projections.py` (contracts/metrics-views.md).
- [ ] T008 [US2] Tests first in `test_dashboard_app.py`: `GET /api/v1/drift-summary` defaults to 24 h, honours `start` and `end`, answers `422` for `start >= end` and for a range over the timeline's limit, and is an unavailable view when the audit trail is; HEAD is `405`.
- [ ] T009 [US2] Add the route to `app.py`.
- [ ] T010 [US2] Tests first in `test_dashboard_archive.py`, `test_dashboard_snapshot.py` and `tests/unit/test_tasks/test_history.py`: the archive export, the archive reader and the snapshot bundle carry the drift summary and the four new series; an archive made without them answers "not in this archive".
- [ ] T011 [US2] Implement T010 in `tasks/history.py`, `archive.py` and `snapshot.py`.
- [ ] T012 [US2] Regenerate the OpenAPI snapshot and the UI's types. Append to the changelog.

**Checkpoint**: the fixture server answers `/api/v1/drift-summary` with spine-01 and leaf-01.

---

## Phase 3: The Page (US2) — PR C

**Goal**: the fabric's history and the drift by device are on the designed page, and the fourth card is runs.

- [ ] T013 [US2] Tests first, then add `driftSummary(range)` and the `series` argument for `history` to `packages/presentation/ui/src/data/provider.ts`, `http.ts` and `inline.ts`, with fixtures in `src/test/fixtures/`.
- [ ] T014 [US2] Tests first in `components/panels/FabricHistory.test.tsx`: four small charts in one frame (devices in sync as a stepped line against the devices in intent, open drifts as a stepped line, runs as stacked bars by trigger with failures marked by shape and word, repair durations as points labelled scan to scan); each has a text alternative and a table behind a toggle; "no scans in this range" when empty; the present-only and archive states.
- [ ] T015 [US2] Implement `FabricHistory.tsx` in spec 014's frame and scale. No animation.
- [ ] T016 [US2] Tests first in `components/panels/DriftByDevice.test.tsx`, then implement `DriftByDevice.tsx`: ranked horizontal bars of total drifted time per device with its episode count, open count and most paths, each row opening the device; "No drift in this range." when empty.
- [ ] T017 [US2] Tests first in `lib/numbers.test.ts`, then change the fourth card in `lib/numbers.ts` to `runs` (data-model.md): the runs in the last 24 hours, with the failures in its context.
- [ ] T018 [US2] Put both panels in `App.tsx` after the history panel, asked for on the history interval and with the page's range. Add them to the App tests for live, past, archive and snapshot modes, and to `parity.test.ts` only if the plain page shows the same fact.
- [ ] T019 [US2] Add a browser check to `e2e/smoke.spec.ts` for the two panels on the fixtures. Run the look check. Append to the changelog.

**Checkpoint**: SC-002: the fabric's history renders with the fixture server's other sources down.

---

## Phase 4: Views Over Exported Families (US1, US5) — PR D, **After #241**

**Goal**: everything the worker and the dashboard already export has a view and a panel.

### D1: the server and the plain page

- [ ] T020 [US1] Tests first in `tests/unit/test_presentation/test_dashboard_prometheus.py`: each of `workflow-mean`, `gnmi-mean`, `workflows-completed`, `activity-time`, `worker-slots`, `server-request-time` and `source-fetch-time` renders the query in contracts/metrics-views.md with only `$ns`, `$uc` and the window filled; its unit and labels; a matrix in the captured format parses to its series. Build the matrix responses by hand in the captured format, using the label sets in research R2.
- [ ] T021 [US1] Add `count` to `MetricUnit` and the seven names to `MetricsViewName` in `models.py`, and the seven specs to `VIEWS` in `prometheus.py`, using #241's rate helper for rates. For `increase`, apply research R4: when the window is longer than the step, set the view's note to say the window.
- [ ] T022 [US1] Tests first in `test_dashboard_app.py`, then check the routes: each new view answers, an unknown one is still `404`, and the OpenAPI enum lists all of them.
- [ ] T023 [P] [US1] Add synthetic series for the seven views to `dev/scripts/serve_dashboard_fixtures.py`, with the label sets of research R2, and extend its test in `tests/unit/test_presentation/test_ui_fixtures.py`.
- [ ] T024 [US1] Make the plain page (`dashboard/static/index.html`) list the new views and show the `count` unit. Its tests in `test_dashboard_pages.py` (or the file that holds them) come first.
- [ ] T025 [US1] Regenerate the OpenAPI snapshot and the UI's types. Append to the changelog.

### D2: the designed page

- [ ] T026 [US5] Tests first in `lib/metrics.test.ts`: every view is in exactly one group of data-model.md "MetricGroup"; a view the list does not know goes to "Other" by its raw name; `formatValue` for `count` rounds for display and names the window; each title says mean, 95th percentile, count or current value.
- [ ] T027 [US5] Change `METRIC_VIEWS` in `lib/metrics.ts` to the grouped list and add the `count` unit.
- [ ] T028 [US5] Tests first in `components/panels/Metrics.test.tsx` and `App.test.tsx`, then render the groups under `GroupLabel`s in `App.tsx`. A `count` panel's lead value is the latest point with its window, never a sum.
- [ ] T029 [US5] Run the look check: 13 panels with data (the three counter views arrive in PR E). Append to the changelog.

**Checkpoint**: the metrics section has four groups.

---

## Phase 5: The Worker's Counters (US3) — PR E, **After #241**

**Goal**: scan results, drift paths and deploy ends are counted.

- [ ] T030 [US3] Read the installed `temporalio`'s `MetricMeter.create_counter` and how a counter's name is exported (research R6). Record what was found in research.md and correct the family names in contracts/metrics-views.md if a suffix is added.
- [ ] T031 [US3] Tests first in `tests/unit/test_orchestrator/test_metrics.py`: each recorder adds one with exactly its label set; a value outside its enum is recorded as `unknown`; a recorder never raises, and does nothing outside an activity or with metrics off. One test per counter holds the label set (FR-013).
- [ ] T032 [US3] Add `PathKind`, `DeployOutcome`, `record_scan_result`, `record_drift_paths` and `record_deploy_end` to `packages/orchestrator/snapl_orchestrator/metrics.py`.
- [ ] T033 [US3] Tests first against `WorkflowEnvironment.start_local()` with a metrics runtime on a free port, as spec 012's worker tests do: after a scan with one drifted device the endpoint shows the scan and path counters; after a failed deploy it shows the deploy counter with its reason. Prove each test red first by leaving the call out.
- [ ] T034 [US3] Call the recorders: scan results and paths in `activities/observability.py` after the drift is evaluated, deploy ends in `workflows/deploy_intent.py` through `workflow.metric_meter()` where the workflow records its end.
- [ ] T035 [US3] Tests first, then add the views `scan-results`, `drift-paths` and `deploy-outcomes` to `models.py`, `prometheus.py`, the fixture server, the plain page and `lib/metrics.ts`. Regenerate the OpenAPI snapshot and the types.
- [ ] T036 [US3] Run the look check: 16 panels with data. Append to the changelog.
- [ ] T037 [US3] **Needs the lab**: quickstart items 5 and 6.

**Checkpoint**: SC-003 holds in the label tests.

---

## Phase 6: Scan Steps (US4) — PR F, **After #241**

**Goal**: a scheduled run shows its blocks.

- [ ] T038 [US4] Tests first in the scan workflow's tests (`tests/unit/test_orchestrator/`), on the local test server with fakes: a scan writes one `fetch_devices_for_use_case` event for the use case and one `collect_drift_state` event per device, each completed or failed with its reason, beside the `detect_drift` events it writes today; a failed collect is recorded as failed and the scan carries on as it does now.
- [ ] T039 [US4] Write the events in `workflows/scan_drift.py` through #241's `record_event` helper.
- [ ] T040 [US4] Tests first in the projections tests: on a trail with the new events, `project_steps` for a scan gives Intent, Collector and Observability as done or failed with the time of each block's last step, and Executor as not reached; a drift response shows its scan's blocks and its repairs; a scan without the new events is still not recorded.
- [ ] T041 [US4] Add the two names to `STEP_BLOCKS` and change `project_steps` in `projections.py`.
- [ ] T042 [US4] FR-019: run the whole existing projection, app, archive and snapshot test suites against the captured trail with the new events added to it, without changing what any test expects. Add that augmented trail as a fixture and one test that compares each existing answer with and without the new events.
- [ ] T043 [US4] Update `components/LoopStrip.test.tsx` and its fixtures for a scan with recorded steps. Run the look check on the `loop` view. Append to the changelog.
- [ ] T044 [US4] **Needs the lab**: quickstart item 7, with the scan's duration before and after.

**Checkpoint**: SC-005 on the fixtures.

---

## Phase 7: Files, Docs and Governance (US5, all) — PR G

**Goal**: the files stay small and the words match.

- [ ] T045 [US5] Measure a seven-day snapshot on the test fixtures before and after the new views (research R8). If it grows by 60% or more, export the new views at no more than 120 points per series in `snapshot.py`, with the step named in each view's note, tests first. Record both sizes in research.md.
- [ ] T046 [P] Update `specs/012-measured-metrics/data-model.md` with a pointer to this spec's contract, `dev/adr/0005-dashboard-and-metrics-stack.md` Decision 3 with the new views and counters, and `CLAUDE.md` "Recent Changes".
- [ ] T047 [P] Update `README.md` and `docs/demo-scenarios.md`: what the dashboard measures now, and a scenario for "what did the loop do today?". Claim only what the lab has shown; say the device telemetry is not there.
- [ ] T048 Finish `changelog/249.added.md` as one readable paragraph.
- [ ] T049 **Needs the lab**: quickstart item 8 (recapture the Prometheus fixtures for the new views, an archive, a snapshot and its size).
- [ ] T050 **Needs the lab**: write the live evidence for the counters and the scan steps into the spec's folder, as spec 012's evidence was (#195).

---

## Dependencies

- A before B before C.
- D, E and F need #241 merged. D before E (E adds views to D's groups). F is independent of D and E.
- G last.

## Deferred to a later spec

- Telemetry from the devices: interface state and counters, BGP sessions, CPU and memory, with a rule for how many series a fabric may add.
- Scraping Temporal's server and Infrahub.
- Alerting.
