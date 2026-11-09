# Tasks: Measured Metrics and Long-Lived History

**Input**: Design documents from `/specs/012-measured-metrics/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/metrics-api.md, contracts/history-tasks.md, quickstart.md

**Tests**: TDD is mandatory per CLAUDE.md and the constitution. Every phase lists its tests first, and each test is run and seen to fail before the code it covers is written.

**Organization**: Tasks are grouped by user story (US1 to US5). US1 and US2 are P1, US3 and US4 are P2, and US5 is P3. Phases are ordered so that work needing no lab comes first.

**PR slicing** (git-workflow: < 400 lines per PR):
- **PR A**: Phases 1–2 (worker recording)
- **PR B**: Phase 3 (dashboard metrics API)
- **PR C**: Phase 4 (dashboard export and page)
- **PR D**: Phase 5 (stack)
- **PR E**: Phase 6 (history tasks)
- **PR F**: Phase 7 (docs and governance)

Each PR says `Closes` a task issue under #147 and carries `changelog/147.added.md` (or appends to it).

**Lab rule for unattended runs**: a task marked **Needs the lab** runs Compose, the real Prometheus container, the maintainer's worker, `demo.reset` or a device. Leave it unticked and defer it (dev/guides/overnight-runs.md). Tests may start their own `WorkflowEnvironment.start_local()` server and their own stub HTTP servers on free ports. That is not the lab.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 to US5 from spec.md
- Exact file paths in every description

---

## Phase 1: Setup

- [x] T001 [P] Add the Prometheus captures in tests/unit/test_presentation/fixtures/prometheus/ to `.secrets.baseline`. Done in the docs PR: detect-secrets flagged Prometheus's revision hash in `buildinfo.json`, and `uvx detect-secrets==1.5.0 scan --baseline .secrets.baseline` added that one entry.
- [x] T002 [P] Extend tests/unit/test_presentation/test_settings.py: `CliSettings.from_env()` resolves `prometheus_address` from `PROMETHEUS_ADDRESS`, else `http://localhost:${PROMETHEUS_PORT:-18090}`. Add `PROMETHEUS_ADDRESS` and `PROMETHEUS_PORT` to `_DASHBOARD_VARS`. Then add the field in packages/presentation/snapl_presentation/settings.py.

---

## Phase 2: Worker Recording (US1, US5) 🎯 MVP

**Goal**: the worker exposes SDK metrics and gNMI timing, and never fails because of metrics.

### Tests (write first, verify they fail)

- [x] T003 [P] [US1] tests/unit/test_orchestrator/test_metrics.py:
  - `record_gnmi_call(device, operation, started, outcome)` records one `snapl_gnmi_call_duration` sample in milliseconds with exactly the labels `device`, `use_case`, `operation` and `outcome` (data-model.md).
  - Use a fake `MetricMeter` (temporalio.common.MetricMeter's interface) injected through a parameter, or `activity.metric_meter` patched. No runtime.
  - A test asserts that the label keys are exactly that set (SC-006).
  - `GnmiOperation` is `configure`/`collect`, and `CallOutcome` is `ok`/`error`.
- [x] T004 [P] [US1] tests/unit/test_orchestrator/test_activities_timing.py (or extend the existing activity tests):
  - `apply_config` records `configure` with `ok` when `ApplyResult.success` is true, and `error` when it is false (FR-005).
  - On a raised exception it records `error` and re-raises the same exception object (FR-004).
  - `collect_drift_state` does the same with `collect` and `CollectResult.success`.
  - Use the activity test environment (`temporalio.testing.ActivityEnvironment`) with the recorder patched.
- [x] T005 [P] [US5] tests/unit/test_orchestrator/test_worker_metrics.py:
  - `build_runtime(port=…, bind="127.0.0.1")` returns a `Runtime` on a free port.
  - With the port already bound by a test socket, it returns `None` and logs a warning containing the port and `WORKER_METRICS_PORT` (research R3).
  - With `port="off"` it returns `None` without trying to bind.
  - `metrics_settings_from_env()` reads `WORKER_METRICS_PORT` (default 18091) and `WORKER_METRICS_BIND` (default `127.0.0.1`), and rejects a non-numeric port other than `off` with a clear error.
- [x] T006 [US1] tests/integration/test_orchestrator/test_worker_metrics_live.py, marked `integration`:
  - Start `WorkflowEnvironment.start_local(runtime=build_runtime(free_port))`.
  - Run a worker with the real `apply_config` activity over a fake executor that returns success, then HTTP GET the endpoint.
  - Assert that a `snapl_gnmi_call_duration_count{…operation="configure",outcome="ok"…}` line is present and that `temporal_workflow_endtoend_latency` is present.
  - The server is test-owned, not the lab.

### Implementation

- [x] T007 [US1] packages/orchestrator/snapl_orchestrator/metrics.py: `GnmiOperation`, `CallOutcome` and `record_gnmi_call`, using `activity.metric_meter().create_histogram_timedelta("snapl_gnmi_call_duration", …, "ms")`. It is the only place the name and labels appear (R5).
- [x] T008 [US1] packages/orchestrator/snapl_orchestrator/activities/executor.py and collector.py:
  - Time each call with `time.monotonic()` in `try/finally`.
  - Derive the outcome from `result.success`, or `error` on an exception.
  - Call `record_gnmi_call`.
  - Behaviour and return values are otherwise unchanged.
- [x] T009 [US5] packages/orchestrator/snapl_orchestrator/worker/metrics.py: `metrics_settings_from_env()` and `build_runtime()` (R3).
- [x] T010 [US5] packages/orchestrator/snapl_orchestrator/worker/run.py:
  - Call `build_runtime()` before `Client.connect`, and pass `runtime=` when it isn't `None`.
  - Log `metrics: http://<bind>:<port>/metrics` or the warning from T009 (contracts/history-tasks.md).
  - Extend the existing run_worker tests so that a taken port still reaches `Worker(...)`.

**Checkpoint**: the worker exports metrics locally. Live evidence items 1 and 2 are lab tasks (T040, T041).

---

## Phase 3: Dashboard Metrics API (US1, US3, US5)

### Tests (write first, verify they fail)

- [x] T011 [P] [US1] tests/unit/test_presentation/test_dashboard_prometheus.py, against the captured fixtures through `httpx.MockTransport`:
  - `parse_matrix` turns `range_gnmi_duration_p95.json` into `MetricSeries` with only the view's labels. `__name__`, `job` and `instance` are dropped. String values parse to float.
  - `NaN`/`+Inf`/`-Inf` become `None`; synthesise these from a captured file by replacing one value, and say so in the test.
  - `range_absent_metric.json` gives an empty series list with the note "no data in range".
  - `error_bad_query.json` and `error_too_many_points.json` become `SourceUnavailable` with Prometheus's `error` text.
  - A connection error becomes `SourceUnavailable` with the `dev.deps` remedy.
  - `choose_step(start, end, requested)` never yields more than 10,000 points, and returns the requested step when it fits (FR-013).
  - Each `MetricsViewName` maps to exactly the query in data-model.md, with `$ns` and `$uc` from settings. A test asserts that no request parameter reaches the query string except `start`, `end` and `step` (FR-011).
  - The cache: two calls within 15 s for the same rounded range make one HTTP request.
- [x] T012 [US1] Extend tests/unit/test_presentation/test_dashboard_app.py:
  - `GET /api/v1/metrics/gnmi-duration` returns `MetricsResponse` with `source: "prometheus"`.
  - An unknown view returns 404, `start >= end` returns 422, and a naive datetime returns 422.
  - With a failing `PrometheusSource`, it returns 200 and `available: false` with the remedy (FR-012).
  - `/api/v1/sources` lists `prometheus`. Its entry comes from `PrometheusSource.refresh()`, which probes `GET /-/ready` (fixture `ready.json`), so 010's `_Reader.read_all(*SourceName)` needs no special case.
  - **Existing tests:** tests that loop over every `SourceName` (the `FIELD` map, the per-source failure matrix, the sources-strip tests) map `prometheus` to no overview view. It feeds `/sources` and the metrics routes only, never `/overview`.
  - `CONTRACT_ROUTES` gains the new route.
  - The OpenAPI snapshot test fails until T015.

### Implementation

- [x] T013 [US1] packages/presentation/snapl_presentation/dashboard/models.py: `SourceName.PROMETHEUS`, `MetricsViewName`, `MetricPoint`, `MetricSeries`, `MetricsData` and `MetricsResponse` (data-model.md). Add the prometheus remedy to `_FAILURE_REMEDY` in app.py.
- [x] T014 [US1] packages/presentation/snapl_presentation/dashboard/prometheus.py: `VIEWS`, `choose_step`, `parse_matrix` and `PrometheusSource(address, namespace, use_case)` with the keyed 15 s cache (R7). `refresh()` implements 010's source protocol by probing `GET /-/ready` and returning a `View[None]`, which is what `/sources` shows. The metrics routes call a separate `query(view, start, end, step)` method. Add `prometheus` to `Sources` and wire it in `cli.py`'s `_serve_dashboard`.
- [x] T015 [US1] app.py: the `/api/v1/metrics/{view}` route through `_Reader` (keyed per view and range, so one slow range doesn't block another). Regenerate tests/unit/test_presentation/fixtures/openapi_v1.json and check by script that the change is additive only, as done in #174. **Expected:** the `SourceName` schema changes because it gains `prometheus`. That is the compatible change T016 records, not a contract break, so don't stop the PR for it. Any other changed schema is a break.
- [x] T016 [US5] specs/010-dashboard-read-api/contracts/dashboard-api.md:
  - Add `SourceName` to the enums consumers must tolerate.
  - Add a dated note pointing at 012's contracts/metrics-api.md (research R7).
  - List this under "Spec amendments" in the PR body.

**Checkpoint**: the metrics API works against fixtures.

---

## Phase 4: Dashboard Export and Page (US3, US1)

### Tests (write first, verify they fail)

- [x] T017 [P] [US3] tests/unit/test_presentation/test_dashboard_exposition.py:
  - `render_exposition(outcomes)` produces valid text format 0.0.4: `# HELP` and `# TYPE` lines, then one sample per source for `snapl_dashboard_source_up` (1/0) and `snapl_dashboard_source_fetch_duration_milliseconds`.
  - A source never fetched is omitted.
  - Parse the output with a minimal line parser in the test to prove the shape.
- [x] T018 [US3] Extend tests/unit/test_presentation/test_dashboard_app.py:
  - `GET /metrics` returns `text/plain; version=0.0.4` and reflects the last outcome of each source after a `/api/v1/sources` request. One source fails, so it shows 0.
  - HEAD returns 405.
  - The route isn't in the OpenAPI document.
- [x] T019 [P] [US1] Extend the page tests in test_dashboard_app.py:
  - The page fetches `/api/v1/metrics/` for each view.
  - It contains a range selector with `1h`, `24h` and `7d`.
  - It still has no absolute URL and no `innerHTML`.

### Implementation

- [x] T020 [US3] `_Reader` records each source's last outcome (answered or not, and the duration in ms) without an extra fetch. Add packages/presentation/snapl_presentation/dashboard/exposition.py and the `GET /metrics` route (R8).
- [x] T021 [US1] packages/presentation/snapl_presentation/dashboard/static/index.html, the Metrics section (contracts/metrics-api.md, "Page"):
  - one panel per view, refreshed every 30 s;
  - small inline SVG line charts built with `createElementNS`;
  - up/down strips for `worker-up` and `source-health`;
  - the unavailable reason and remedy instead of a chart;
  - the range in `localStorage` inside try/catch.

**Checkpoint**: Prometheus can scrape the dashboard, and the page shows metrics when they exist.

---

## Phase 5: Stack (US2)

### Tests (write first, verify they fail)

- [x] T022 [P] [US2] Extend tests/unit/test_infrastructure/test_compose.py for a `prometheus` service:
  - image exactly `prom/prometheus:v3.10.0`;
  - port `127.0.0.1:${PROMETHEUS_PORT:-18090}:9090`;
  - a bind mount from `../.snapl/prometheus` to `/prometheus`, and no named volume for it;
  - flags include `--storage.tsdb.retention.size=` and `--web.enable-admin-api`, and none includes `retention.time`;
  - `extra_hosts` has `host.docker.internal:host-gateway`.
- [x] T023 [P] [US2] A new test file next to test_compose.py for development/prometheus/prometheus.yml: the jobs `snapl-worker` → `host.docker.internal:18091` and `snapl-dashboard` → `host.docker.internal:18030`, at a 15 s interval.
- [x] T024 [P] [US2] Extend tests/unit/test_tasks/test_dev.py and test_demo.py:
  - `dev.deps` creates `.snapl/prometheus` (mode 0777) before `compose up`.
  - No command run by `dev.down(volumes=True)` or `demo.reset` names `.snapl` (FR-009).

### Implementation

- [x] T025 [US2] development/docker-compose.yml: the `prometheus` service (contracts/history-tasks.md, "Compose"). development/prometheus/prometheus.yml.
- [x] T026 [US2] tasks/dev.py: `deps` creates the directory. dev/knowledge/host-resource-registry.md: move 18090 and 18091 from reserved to in use.
- [x] T027 [US2] **Needs the lab.** Run `invoke dev.deps`, then confirm Prometheus is ready on 18090, both targets are listed, and `.snapl/prometheus/` holds data. Paste `curl localhost:18090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, health}'` into the PR.

---

## Phase 6: History Tasks (US4, US2)

### Tests (write first, verify they fail)

- [x] T028 [P] [US4] tests/unit/test_tasks/test_history.py, archive:
  - **Setup:** a temp checkout with an audit DB written by `SqliteAuditLog`, a `.snapl/test-runs/` tree, and a stub HTTP server on a free port. The stub plays both Prometheus (it answers the snapshot POST and creates `snapshots/<name>/` under a temp data dir) and the dashboard (it serves canned JSON).
  - **Contents:** the archive holds every path in contracts/history-tasks.md. `manifest.json` matches data-model.md. The audit copy opens with `sqlite3` and has the same row count.
  - **Snapshot clean-up:** the snapshot directory is removed from the live data dir afterwards.
  - **Missing parts:** with Prometheus down, or the dashboard down, the archive is still written, the part is `present: false` with a reason, and the exit is 1 (FR-018).
  - **Output location:** `SNAPL_ARCHIVE_DIR` is honoured. No `.partial` file is left behind.
- [x] T029 [P] [US2] tests/unit/test_tasks/test_history.py, purge:
  - It refuses while the stub answers on `PROMETHEUS_PORT`.
  - It refuses without a TTY and without `--yes`.
  - Any typed answer other than `purge` deletes nothing.
  - `--yes` deletes only `.snapl/prometheus/`, leaving the audit DB, the test runs and the archives untouched.
  - Every test uses temp dirs only.

### Implementation

- [x] T030 [US4] tasks/history.py: `archive` (R9). Register it in tasks/__init__.py as the `history` collection.
- [x] T031 [US2] tasks/history.py: `purge` (R10).
- [ ] T032 [US4] **Needs the lab.** Quickstart item 6: archive with everything up, check all five parts with standard tools, then archive with the dashboard stopped.

---

## Phase 7: Docs and Governance

- [x] T033 [P] README.md:
  - Replace "no metrics until #147" with what 012 delivers.
  - Add `history.archive` and `history.purge`.
  - Say that `demo.reset` keeps metrics.
- [x] T034 [P] AGENTS.md:
  - Add Prometheus to the stack.
  - Add `WORKER_METRICS_PORT` and the history tasks to Key Commands.
  - Remove any "monitoring" claim that isn't true.
- [x] T035 [P] docs/demo-scenarios.md: a short "How long did it take?" scenario: deploy, scan, then the gNMI panel. Run it through the humanizer skill.
- [x] T036 [P] dev/scripts/capture_prometheus_fixtures.py, test first. tests/unit/test_dev_scripts/test_capture_prometheus_fixtures.py runs the script's `capture(address, out_dir)` against a stub HTTP server on a free port, and asserts the file names and the `{http_status, body}` shape, including a 400 case:
  - Commit the capture script (research R11).
  - It queries a Prometheus at a given address with the six views' queries plus the error, empty, buildinfo, ready and snapshot cases.
  - It writes `{http_status, body}` files.
  - Lint-clean (ruff and bandit; urllib against a local address needs a justified `# nosec` on its own line, per 010's convention).
  - Update the fixtures README to point at it.
- [x] T037 dev/adr/0005-dashboard-and-metrics-stack.md, Decision 3: record v3.10.0, the measured facts (R1–R4) and the Linux caveat. Leave the status Proposed until T042.
- [x] T038 changelog/147.added.md, as the slices land: worker metrics, Prometheus in the stack, dashboard metrics and source health, `history.archive` and `history.purge`.
- [x] T039 Run `uv run invoke lint`, `uv run mypy packages/`, `uv run bandit -r packages/ -c pyproject.toml -q`, `uv run invoke test-unit` and `uv run invoke scan`. Confirm ≥ 80 % coverage on the new modules.

### Lab evidence (deferred in unattended runs)

- [x] T040 **Needs the lab.** Quickstart item 1 (SC-001, SC-006).
- [x] T041 **Needs the lab.** Quickstart item 2 (SC-004).
- [ ] T042 **Needs the lab.** Quickstart items 3–5 (SC-002, SC-003). Then mark ADR-0005 Decision 3 Accepted.
- [x] T043 **Needs the lab.** Quickstart item 7: re-capture the fixtures from the real stack with T036's script, and update the tests if the shapes changed.
- [ ] T044 **Needs the lab.** After T042 (live evidence in, ADR-0005 Decision 3 Accepted): bump the constitution to 2.1.0 (minor), listing FastAPI/uvicorn (010) and Prometheus (012) under Technology Stack (FR-021). Node waits for 011.

---

## Dependencies & Execution Order

- **Phase 1** comes before everything else.
- **Phase 2** (worker) and **Phase 3** (API) are independent of each other. Phase 3 uses fixtures, not the worker.
- **Phase 4** depends on Phase 3 (`SourceName.PROMETHEUS` and `_Reader` changes).
- **Phase 5** is independent of the code phases, and its lab task T027 needs Phase 2's worker to show `snapl-worker` up.
- **Phase 6** depends on Phase 5's paths (`.snapl/prometheus`) and Phase 4's dashboard routes for the export.
- **Phase 7** comes last. T044 waits until everything has merged.

## Implementation Strategy

- **MVP**: PR A gives measured gNMI timing on the worker's endpoint, verifiable with `curl` before any Prometheus exists.
- **Unattended runs**: PRs A–F can each merge with their lab tasks deferred. The maintainer then runs T027, T032 and T040–T043 in one lab session, following quickstart.md.
