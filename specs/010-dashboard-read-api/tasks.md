# Tasks: Dashboard Read API and MVP Page

**Input**: Design documents from `/specs/010-dashboard-read-api/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/dashboard-api.md, contracts/cli-dashboard.md, contracts/lab-runs.md, quickstart.md

**Tests**: TDD is mandatory per CLAUDE.md and the constitution. Every phase lists its tests first, and each test is run and seen to fail before the code it covers is written.

**Organization**: Tasks are grouped by user story (US1 to US5). US1 and US2 are P1, US3 and US4 are P2, and US5 is P3.

**PR slicing** (git-workflow: < 400 lines per PR):
- **PR A**: Phases 1–2
- **PR B**: Phase 3 (US1)
- **PR C**: Phase 4 (US2)
- **PR D**: Phase 5 (US3)
- **PR E**: Phase 6 (US4)
- **PR F**: Phases 7–8 (US5, polish)

Each PR says `Closes` a task issue under #101 and carries `changelog/101.added.md` (or appends to it).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 to US5 from spec.md
- Exact file paths in every description

---

## Phase 1: Setup

**Purpose**: Dependencies, settings, ignore rules. No behaviour yet.

- [x] T001 Add `fastapi>=0.115`, `uvicorn>=0.32` and `httpx>=0.28` to `dependencies` in packages/presentation/pyproject.toml. Declare `snapl_presentation/dashboard/static/*` as package data (hatch `[tool.hatch.build.targets.wheel]` include). Run `uv lock` and confirm `uv lock --check` passes (research R1).
- [x] T002 [P] Add `.snapl/` to .gitignore, with a comment naming spec 010 and spec 012 as its users.
- [x] T003 [P] Extend tests/unit/test_presentation/test_cli.py (or a new test_settings.py next to it) so `CliSettings.from_env()` resolves the new fields:
  - `dashboard_port` (`DASHBOARD_PORT`, default 18030)
  - `dashboard_host` (`DASHBOARD_HOST`, default `127.0.0.1`)
  - `temporal_ui_address` (`TEMPORAL_UI_ADDRESS`, default `http://localhost:${TEMPORAL_UI_PORT:-18034}`)
  - `github_repo` (`SNAPL_GITHUB_REPO`, default `None`, resolved later from git)
  - `github_token` (`GITHUB_TOKEN`)
  - `test_runs_dir` (`SNAPL_TEST_RUNS_DIR`, default `.snapl/test-runs`)

  Also assert that `repr(settings)` never contains the token value. Run the test and see it fail.
- [x] T004 Implement those fields in packages/presentation/snapl_presentation/settings.py. Exclude `github_token` from repr (`field(repr=False)`). T003 passes.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**:
- Shared probes
- The read-only audit log
- Drift direction in the audit trail
- The view and envelope models

Every story depends on these.

### Tests (write first, verify they fail)

- [x] T005 [P] Write tests/unit/test_presentation/test_probes.py. It pins the behaviour of the helpers that move out of cli.py:
  - `probe` returns a reason for a refused or timed-out port and `None` for a listening one
  - `connect` raises `ConnectionCliError` naming `TEMPORAL_HOST` without calling `build_client` when the probe fails
  - `count_pollers` returns `len(response.pollers)` from a fake `describe_task_queue`
  - `identify_sot` delegates to `snapl_intent.infrahub.identity.identify`

  Import from `snapl_presentation.probes`, so it fails until T009.
- [x] T006 [P] Extend tests/unit/test_orchestrator/test_audit_sqlite.py with `SqliteAuditLog(database_url=path, read_only=True)`:
  - opens an existing DB and returns the same events as a writable instance
  - `initialize()` does not create a missing file and raises `AuditLogError`
  - `initialize()` executes no DDL (the file's mtime and size are unchanged)
  - `append()` raises `AuditLogError("audit log opened read-only")`
  - a concurrent writable instance can still append while the read-only one is open (WAL)
  - it opens a WAL-mode DB after the writer has closed, with and without the `-wal`/`-shm` files present. macOS keeps them on close, Linux deletes them, so cover both explicitly.
- [x] T007 [P] Extend tests/unit/test_orchestrator/test_workflow_scan_drift.py:
  - The per-device `detect_drift` audit payload carries `undesired_paths`. It is a subset of `paths`, in the same order, and contains exactly the paths whose `DriftItem.undesired` is true.
  - It is `[]` for a forward-only report.
  - It is absent when `paths` is absent (clean device).
  - Existing keys are unchanged (FR-012a, research R4).
- [x] T007a [P] Extend tests/unit/test_orchestrator/test_workflow_deploy_intent.py:
  - An operator-started run's `WORKFLOW_STARTED` payload is `{"parent_workflow_id": None}`.
  - A run started as a child of `ReconcileDevices` carries the parent's workflow id.
  - No other event's payload changes (FR-016a, research R5).
- [x] T008 [P] Write tests/unit/test_presentation/test_dashboard_models.py:
  - `View` rejects `available=False` without `reason` and `remedy`, and rejects `stale=True` without `data` and `fetched_at`.
  - `Envelope.contract_version == "1"`.
  - Every model is frozen and forbids extra fields.
  - `DeviceView` invariants from data-model.md:
    - path lists are populated only for their states
    - `error` is required for `error`
    - `last_scanned_at is None` iff `never_scanned`
  - `Series` rejects `start >= end`.
  - Datetimes serialise with an offset.

### Implementation

- [x] T009 Move `_probe`, `_connect`, `count_pollers` and `identify_sot` from packages/presentation/snapl_presentation/cli.py into a new packages/presentation/snapl_presentation/probes.py, public names `probe`, `connect`, `count_pollers` and `identify_sot`. cli.py imports them, and its behaviour is unchanged. T005 and the existing tests/unit/test_presentation/test_cli.py and test_cli_schedule.py all pass. Keep the docstring explaining why the TCP probe exists.
- [x] T010 [P] Add `read_only: bool = False` to `SqliteAuditLog.__init__` in packages/orchestrator/snapl_orchestrator/audit/sqlite.py:
  - **When true**: connect with `f"file:{path}?mode=ro"`, `uri=True`; `initialize()` checks the file exists and skips the DDL and the WAL pragma; `append()` raises.
  - **When false**: the code path is unchanged.
  - `:memory:` with `read_only=True` raises `ValueError`.
  - T006 passes.
- [x] T011 [P] In packages/orchestrator/snapl_orchestrator/workflows/scan_drift.py `_audit_device`, add `payload["undesired_paths"] = [i.path for i in report.items if i.undesired]` inside the existing `if report.items:` branch. T007 passes. `tests/unit/test_orchestrator/test_vendor_seams.py` still passes.
- [x] T011a [P] In packages/orchestrator/snapl_orchestrator/workflows/deploy_intent.py, pass `payload={"parent_workflow_id": parent.workflow_id if (parent := workflow.info().parent) else None}` to the `WORKFLOW_STARTED` `_record_event`. T007a passes. No `workflow.patched` is needed, because activity inputs are not part of replay.
- [x] T012 Create packages/presentation/snapl_presentation/dashboard/__init__.py and packages/presentation/snapl_presentation/dashboard/models.py with every model in data-model.md. Reuse `WorkflowReason` and `DriftScheduleView` from `snapl_orchestrator.models`. T008 passes.
- [x] T013 **Live evidence item 1** (quickstart.md):
  - With T011 and T011a deployed to the worker, hand-add an interface address on `leaf-01` and change a BGP peer AS on `spine-01`.
  - Run deploy, scan, reconcile, scan per the quickstart.
  - Verify `snapl audit --device leaf-01` shows `undesired_paths == paths`, and that the reconcile's deploys carry its id as `parent_workflow_id`.
  - Copy the resulting audit DB to tests/unit/test_presentation/fixtures/audit_demo_arc.sqlite, with a README.md next to it recording the date, commit and steps.
  - **Needs the maintainer's lab.**

**Checkpoint**: probes shared, audit log readable without writes, drift direction recorded, models in place. PR A.

---

## Phase 3: User Story 1 — See the State of the Fabric in One Place (Priority: P1) 🎯 MVP

**Goal**: `snapl dashboard` serves `/api/v1/devices` and a page that shows every device's role, drift state, last scan and last deploy.

**Independent Test**: Against `audit_demo_arc.sqlite` and a fake intent store, `GET /api/v1/devices` returns six devices with the states the fixture implies, and the page renders them.

### Tests (write first, verify they fail)

- [x] T014 [P] [US1] Write the device projection tests in tests/unit/test_presentation/test_dashboard_projections.py:
  - Feed `project_devices(events, states)` hand-built event lists covering every `DriftState`:
    - in_sync, missing, undesired, missing_and_undesired
    - drifted (event without `undesired_paths`)
    - error (`ACTIVITY_FAILED` with `payload.error`)
    - never_scanned
  - `last_deploy` comes from the latest `DeployIntent` terminal event for the device.
  - A device in events but not in `states` gets `in_intent=False` and is excluded from the device list.
  - `suggested_command` is `snapl reconcile <name>` for forward drift, and `None` with the #65 note for undesired-only drift.
  - Sorting is spines first, then by name.
  - Add one test over `audit_demo_arc.sqlite` asserting the observed states from T013.
- [x] T015 [P] [US1] Write tests/unit/test_presentation/test_dashboard_sources.py for `AuditSource`:
  - The first `refresh()` reads all events through `query_by_time_range` on a fake `AuditLog`.
  - The next `refresh()` asks from 5 minutes before the newest timestamp seen, and drops duplicates by `event_id`.
  - **Late row**: after a refresh that has read an event at T, the fake log gains an event stamped T − 30 s. The next refresh includes it.
  - **Very late row**: an event stamped T − 1 h appears after the next full rebuild, which runs 15 minutes later on an injected clock. A failed rebuild keeps the current read model.
  - Projections receive events ordered by `timestamp`, whatever order they arrived in.
  - A missing audit file gives `available=False`, a reason naming the path, and remedy `Set SNAPL_AUDIT_DB …`, and the file is not created.
  - The source never calls `append` (the fake raises on it).
- [x] T016 [P] [US1] Add `SotSource` tests to test_dashboard_sources.py:
  - Devices and roles come from a fake store's `get_desired_state`.
  - The result is cached for 30 s, using an injected clock.
  - A foreign identity (`is_foreign=True`) gives `available=False` with the identity detail as reason (FR-015).
  - A store error gives `available=False` with the remedy `uv run invoke dev.deps`, and keeps the last good data with `stale=True`.
- [x] T017 [P] [US1] Write tests/unit/test_presentation/test_dashboard_app.py, part 1:
  - `create_app(sources)` with fake sources.
  - `GET /api/v1/devices` returns the envelope and a `devices` view.
  - Every route has methods ⊆ {GET, HEAD}, and only the framework's OpenAPI route has `HEAD`. Collect routes recursively through each included router's `original_router.routes`. FastAPI wraps an included router in one entry with an empty method set, so a flat walk of `app.routes` passes vacuously (research R1). Assert the collected set equals the contract's route list.
  - `HEAD`, `POST`, `PUT`, `PATCH` and `DELETE` to `/api/v1/devices` return 405 (SC-006).
  - `/docs` and `/redoc` return 404.
  - `/api/v1/openapi.json` matches the snapshot at tests/unit/test_presentation/fixtures/openapi_v1.json. Create the snapshot from the first green run and review it by hand against contracts/dashboard-api.md.
  - `GET /` returns HTML containing no `http://` or `https://` URL other than relative ones (FR-008).
- [x] T018 [P] [US1] Write tests/unit/test_presentation/test_cli_dashboard.py:
  - `snapl dashboard` resolves port and host from options, then env, then defaults.
  - A port already bound (a socket the test opens) exits with `ExitCode.ERROR` and the message from contracts/cli-dashboard.md, without starting uvicorn (patched).
  - `--host 0.0.0.0` prints the no-authentication warning.
  - The start line names the use case and URL.

### Implementation

- [x] T019 [US1] Implement `project_devices` in packages/presentation/snapl_presentation/dashboard/projections.py as a pure function `(events: Sequence[AuditEvent], states: Sequence[DesiredState] | None, names: Mapping[UUID, str]) -> list[DeviceView]` per research R4. T014 passes.
- [x] T020 [US1] Implement in packages/presentation/snapl_presentation/dashboard/sources.py:
  - a small `Cached[T]` helper: TTL, single-flight `asyncio.Lock`, keeps the last good value and marks it stale, injectable clock
  - `AuditSource`: opens `SqliteAuditLog(read_only=True)`. It re-reads a trailing 5-minute window on each refresh and rebuilds from a full read every 15 minutes, swapping the result in only on success. It dedups by `event_id` and orders by `timestamp` (research R3).
  - `SotSource`: `load_states` and `build_store` from `resolve.py`, then `probes.identify_sot`

  Each source returns a `View`. T015 and T016 pass.
- [x] T021 [US1] Implement `create_app(sources, settings, use_case)` in packages/presentation/snapl_presentation/dashboard/app.py:
  - mount `/api/v1/devices`
  - serve `static/index.html` at `/` via `importlib.resources`
  - set `openapi_url="/api/v1/openapi.json"`, `docs_url=None` and `redoc_url=None`
  - register a 405 handler for non-GET methods

  T017 passes (except routes added in later phases).
- [x] T022 [US1] Create packages/presentation/snapl_presentation/dashboard/static/index.html:
  - one page, inline CSS and JS, no external requests
  - fetches `/api/v1/devices` every 5 s
  - renders a device table with `textContent` only
  - shows each view's `fetched_at` age and any reason or remedy
  - shows `suggested_command` as copyable text, not a button (FR-008 to FR-010)
- [x] T023 [US1] Add the `dashboard` command to packages/presentation/snapl_presentation/cli.py per contracts/cli-dashboard.md:
  - pre-bind port check
  - non-loopback warning
  - builds sources from `CliSettings`
  - runs `uvicorn.Server` programmatically

  T018 passes.

**Checkpoint**: `uv run snapl dashboard` shows the fabric's state. US1 is independently usable. PR B.

---

## Phase 4: User Story 2 — See What the Loop Has Been Doing (Priority: P1)

**Goal**: `/api/v1/runs` and `/api/v1/schedule`: recent runs with outcome and trigger, what is running, the schedule's state, repeated failed repairs and the worker.

**Independent Test**: Against the audit fixture plus a fake Temporal client holding one schedule and one running workflow, both endpoints return the expected runs, triggers, schedule state and running workflow.

### Tests (write first, verify they fail)

- [x] T024 [P] [US2] Add run projection tests to tests/unit/test_presentation/test_dashboard_projections.py:
  - **Run splitting**:
    - two `DeployIntent` runs under the same `deploy-intent-{id}` become two `RunView`s, keyed by `(workflow_id, started_at)`
    - a started run with no terminal event is `running` if its id is in the running set, else `unfinished`
  - **Triggers**:
    - `DriftResponse` (actor `schedule:…`) → `schedule`
    - its `{wf}-scan` and `{wf}-reconcile` children → `schedule`
    - a `DeployIntent` whose `parent_workflow_id` is a scheduled `{wf}-reconcile` → `schedule`
    - a `DeployIntent` whose parent is an operator-started reconcile, or with `parent_workflow_id: null` → `operator`
    - a `DeployIntent` without the `parent_workflow_id` key (recorded before FR-016a) → `unknown`
    - **regression**: an operator `DeployIntent` (`parent_workflow_id: null`) that starts inside a scheduled reconcile's window for the same device stays `operator`
    - a CLI scan → `operator`, with `actor` only when recorded
  - **Durations**: `duration_s` for running runs counts to an injected now.
  - **Limits**: `limit` and the `workflow_type` and `device` filters.
  - **Links**: `web_ui_url` format from research R12.
- [x] T025 [P] [US2] Add repeated-failure tests: `project_repeated_failures(events, threshold=3)` per research R7.
  - Count consecutive `result == "failed"` remediation records per device.
  - `succeeded` resets the count.
  - `unknown` and `skipped` neither count nor reset.
  - A scheduled run that didn't attempt the device doesn't reset.
  - Devices under 3 are omitted.
- [x] T026 [P] [US2] Add `TemporalSource` tests to test_dashboard_sources.py with fake functions:
  - `list_running_workflows`, `count_pollers` and `find_drift_schedule` are each called at most once per 5 s TTL.
  - Probe failure gives `available=False` and the remedy `uv run invoke dev.deps` without calling `connect`.
  - No schedule gives `ScheduleStatus(exists=False, suggested_command="snapl schedule create --use-case dcfabric")`.
  - `paused_by` and `paused_at` come from the latest `SCHEDULE_CHANGED` with change `paused`.
  - The fake client raises on any method outside the read list (research R2).
- [x] T027 [P] [US2] Extend test_dashboard_app.py:
  - `/api/v1/runs` with limit bounds 1–500 (422 outside).
  - `/api/v1/schedule`: with Temporal down, `schedule.available` is false while `repeated_failures` is still present with `stale: true` (contracts/dashboard-api.md).
  - Update the OpenAPI snapshot.

### Implementation

- [x] T028 [US2] Implement `project_runs(events, running_ids, now, limit, filters, ui_address, namespace)` and `project_repeated_failures(events, names, threshold=3)` in packages/presentation/snapl_presentation/dashboard/projections.py. T024 and T025 pass.
- [x] T029 [US2] Implement `TemporalSource` in packages/presentation/snapl_presentation/dashboard/sources.py using `probes.connect`, `worker.client.list_running_workflows`, `probes.count_pollers` and `schedules.find_drift_schedule`. T026 passes.
- [x] T030 [US2] Add `/api/v1/runs` and `/api/v1/schedule` to packages/presentation/snapl_presentation/dashboard/app.py. T027 passes.
- [x] T031 [US2] Extend static/index.html:
  - runs table with Web UI links
  - running list
  - schedule panel: mode, interval, paused with reason and actor, next and last run, the repeated-failure warning with its command, and worker pollers
- [ ] T032 [US2] **Live evidence item 2** (quickstart.md): run the demo arc with the dashboard open, record that each step appeared within 10 s (SC-001), and save the overview JSON for the PR body. **Needs the lab.**

**Checkpoint**: US1 and US2 both work independently. PR C.

---

## Phase 5: User Story 3 — See Drift History Over Time (Priority: P2)

**Goal**: `/api/v1/history` returns audit-derived series over any range, with gaps as no data.

**Independent Test**: Over a fixture spanning six months with a month-long gap, each series returns the expected points, and the gap has none.

### Tests (write first, verify they fail)

- [x] T033 [P] [US3] Add series tests to tests/unit/test_presentation/test_dashboard_projections.py, generating events with timestamps over 6 months and a 30-day gap:
  - `drifted_devices` and `undesired_paths` are one point per `ScanDrift` terminal event, with no points in the gap.
  - `deploys` and `repairs` are per-bucket counts by outcome, with `null` (not 0) in buckets with no scan and no deploy.
  - Markers appear for `ReconcileDevices` runs and remediate-mode `DriftResponse` runs.
  - `first_event_at` is set.
  - Bucket edges are UTC-aligned.
  - More than 2,000 buckets raises `ValueError`.
- [x] T034 [P] [US3] Add a performance test, marked `slow`: build 100,000 synthetic events, then time `project_devices`, `project_runs` and a 30-day `1h` series. Each must finish in < 1 s (SC-007).
- [x] T035 [P] [US3] Extend test_dashboard_app.py:
  - `/api/v1/history` defaults (last 24 h, `1h`)
  - repeatable `series`
  - 422 for a naive time, `start >= end` and over 2,000 buckets
  - the range is never clamped

  Update the OpenAPI snapshot.

### Implementation

- [x] T036 [US3] Implement `project_series(events, name, start, end, bucket)` in packages/presentation/snapl_presentation/dashboard/projections.py per research R8. T033 and T034 pass. If T034 fails, keep per-device and per-workflow indexes in `AuditSource`, updated incrementally, rather than rescanning the event list.
- [x] T037 [US3] Add `/api/v1/history` to packages/presentation/snapl_presentation/dashboard/app.py. T035 passes.
- [x] T038 [US3] Extend static/index.html with a "last 24 hours" table per series, refreshed every 60 s, showing gaps as "no scans" and listing markers.

**Checkpoint**: History works from the audit trail alone. PR D.

---

## Phase 6: User Story 4 — Know Whether the Live Lab Evidence Is Current (Priority: P2)

**Goal**: Live runs are recorded. The dashboard shows the latest run per suite and the commits since, plus CI status and coverage.

**Independent Test**:
- Run the recording helper with a stub pytest command and check the record.
- Against fixture records, a temp git repo and fake GitHub and Codecov responses, `/api/v1/lab-runs` and `/api/v1/ci` return the expected views.

### Tests (write first, verify they fail)

- [ ] T039 [P] [US4] Create fixtures in tests/unit/test_presentation/fixtures/lab_runs/: `passed-e2e/`, `failed-integration/`, `running-dead-pid/`, `corrupt-json/` and `format-2/`, each with `run.json` and, where relevant, `junit.xml`, per contracts/lab-runs.md.
- [ ] T040 [P] [US4] Write tests/unit/test_tasks/test_lab_runs.py for `record_run(ctx, suite, command)` in tasks/lab_runs.py, with a stub command and a temp `SNAPL_TEST_RUNS_DIR`:
  - `run.json` exists with `status: "running"`, `commit`, `tree`, `branch`, `dirty`, `device_image` (parsed from containerlab/dcfabric.yml) and `pid` **before** the command runs (the stub asserts it).
  - After the run, the file is rewritten atomically with status from the exit code (0 → passed, 1 → failed, 2 or KeyboardInterrupt → interrupted, other → failed), `ended_at`, `exit_code` and `counts` parsed from junit.xml.
  - Missing junit.xml leaves out `counts`.
  - An unwritable folder prints a warning and the exit code is still pytest's.
  - The last printed line matches contracts/cli-dashboard.md.
- [ ] T041 [P] [US4] Extend tests/unit/test_tasks/test_demo.py: `demo.reset` does not touch `.snapl/` (create a sentinel file, run reset with patched `dev.lab_destroy` and `dev.down`, then assert it still exists) (FR-029).
- [ ] T042 [P] [US4] Write tests/unit/test_presentation/test_dashboard_lab_runs.py over the T039 fixtures and a temp git repo:
  - `running-dead-pid` → `incomplete`
  - `corrupt-json` and `format-2` → `unreadable` with a `problem`, and the others are still listed
  - `latest` is the newest readable run per suite
  - `commits_on_main_since` counts `git rev-list <commit>..main` for an ancestor, with `matched_by: ancestor`
  - **squash merge**: a run on a branch commit, then a squash commit on `main` with the same tree, then two more commits → `2`, `matched_by: tree`
  - a commit still in local history but not on main, with no tree match → `None` with reason "commit not on main yet"
  - a commit missing from local history → `None` with reason "commit not in local history"
  - a record with no `tree` and no ancestor → `None` with reason "run recorded without a tree"
  - a missing folder → available, no runs, remedy `uv run invoke test-e2e`
- [ ] T043 [P] [US4] Write tests/unit/test_presentation/test_dashboard_ci.py with `httpx.MockTransport`. Capture real response bodies from the public API for `anton-tvrz/snapl` once and store them under fixtures/github/ and fixtures/codecov/ (Principle III).
  - Parse `main`'s gate and jobs from check-runs, plus open PRs and their heads.
  - Parse coverage from Codecov.
  - A `304` with `If-None-Match` reuses the cached body.
  - The refresh interval is 120 s with a token and 600 s without.
  - Without a token and with 8 open PRs, check runs are fetched for the 5 most recently updated, and `omitted_prs == 3`. With a token, all 8 are fetched and `omitted_prs == 0`.
  - The request budget assumes a `304` costs a call when unauthenticated (research R9). Assert the call count, not only the 200s.
  - `X-RateLimit-Remaining < 10` stops requests until `X-RateLimit-Reset`, and serves `stale: true`.
  - No network on first call → `available=False`, remedy `export GITHUB_TOKEN=…`.
  - The token never appears in any `View` or log record.
  - The repo comes from `git remote get-url origin` (https and ssh forms) unless `SNAPL_GITHUB_REPO` is set.
- [ ] T044 [P] [US4] Extend test_dashboard_app.py with `/api/v1/lab-runs` and `/api/v1/ci` over fake sources, and update the OpenAPI snapshot.

### Implementation

- [ ] T045 [US4] Implement tasks/lab_runs.py (`record_run`). In tasks/main.py, route `test_e2e` through it (adding `--junitxml`), and add `test_integration` running `pytest tests/integration -m integration -v`. Its docstring says the live stack must be up. T040 and T041 pass.
- [ ] T046 [P] [US4] Implement packages/presentation/snapl_presentation/dashboard/lab_runs.py: the reader, liveness via `os.kill(pid, 0)`, and the git commit count via `subprocess.run([...], check=False)` with a timeout and no shell. T042 passes.
- [ ] T047 [P] [US4] Implement packages/presentation/snapl_presentation/dashboard/ci.py: an `httpx.AsyncClient` with ETag cache, adaptive refresh, rate-limit backoff and single-flight lock. T043 passes.
- [ ] T048 [US4] Add `/api/v1/lab-runs` and `/api/v1/ci` to packages/presentation/snapl_presentation/dashboard/app.py, and wire `LabRunSource` and `CiSource` into `snapl dashboard`. T044 passes.
- [ ] T049 [US4] Extend static/index.html:
  - lab evidence panel: latest per suite, age, "N commits on main since the last live run", incomplete and unreadable runs
  - CI panel: `main` and each PR with gate, failed jobs, links and coverage
- [ ] T050 [US4] **Live evidence items 5 and 6** (quickstart.md):
  - Run `invoke test-e2e` on `main`, then pull three commits that land on `main` afterwards. Verify "3 commits on main since the last live run", matched by `ancestor`. Don't make throwaway commits on your own `main`.
  - Run `invoke test-integration` at PR E's up-to-date branch head, squash-merge it, pull, and verify the run is matched by `tree`.

  **Needs the lab.**

**Checkpoint**: Lab evidence and CI visible. PR E.

---

## Phase 7: User Story 5 — Tell Me When a Source Is Down, and How Old the Data Is (Priority: P3)

**Goal**: Every source fails independently, every view shows its age, and `/api/v1/overview` and `/api/v1/sources` serve the page.

**Independent Test**: For each source in turn, make it fail. The views depending on it report unavailable with a reason and a remedy, and every other view is unchanged.

### Tests (write first, verify they fail)

- [ ] T051 [P] [US5] Add a parametrised failure matrix to test_dashboard_app.py:
  - For each `SourceName`, a fake that raises or times out.
  - Assert `GET /api/v1/overview` returns 200 within 2 s (SC-003).
  - Assert only the dependent views have `available: false` with reason and remedy.
  - Assert the others equal the all-healthy response.
  - Also all sources down at once → 200 (FR-007).
- [ ] T052 [P] [US5] Add `/api/v1/sources` tests: one entry per `SourceName`, with no `data`, and `fetched_at` matching the last successful read.
- [ ] T053 [P] [US5] Add a cross-check test (SC-002): over the same fakes, `/api/v1/devices` and `/api/v1/schedule` agree with what `snapl status --json` and `snapl schedule show --json` render, using the CLI's renderer on the same inputs.

### Implementation

- [ ] T054 [US5] Add `/api/v1/overview` and `/api/v1/sources` to packages/presentation/snapl_presentation/dashboard/app.py. Fetch views with `asyncio.gather(..., return_exceptions=True)`, wrap each in the source's timeout (`SNAPL_CONNECT_TIMEOUT`), and turn every exception into an unavailable view. T051 to T053 pass.
- [ ] T055 [US5] Switch static/index.html to a single `/api/v1/overview` poll every 5 s, plus `/api/v1/history` every 60 s. Add a source status strip from `/api/v1/sources` showing each source's state, age and remedy.
- [ ] T056 [US5] **Live evidence items 3 and 4**:
  - Stop the worker, then Temporal, then Infrahub, and record what the page showed each time.
  - Run `demo.reset` then `demo.up`, and record that history and lab runs survived.

  **Needs the lab.**

**Checkpoint**: All five stories work. Failure is data.

---

## Phase 8: Polish & Cross-Cutting

- [ ] T057 [P] Write tests/integration/test_presentation/test_dashboard_local.py, marked `integration`: serve the app against `WorkflowEnvironment.start_local()` and a temp audit DB seeded by running `ScanDrift` with mocked activities, then assert `/api/v1/overview` shows the run and the device state.
- [ ] T058 [P] Add a "Watch the loop" scenario to docs/demo-scenarios.md: `snapl dashboard`, then the demo arc, plus what to point at. Run the text through the humanizer skill before committing.
- [ ] T059 [P] In README.md, replace the "No metrics or dashboards (#101)" limitation with what 010 delivers and what it does not (no metrics until #147, no designed UI until #146). Add `snapl dashboard` to Quick start.
- [ ] T060 [P] In AGENTS.md, add the dashboard to the presentation row and to Key Commands. In CLAUDE.md, move the 010 line under Recent Changes from "planned" to done, and add `invoke test-integration`.
- [ ] T061 [P] Add changelog/101.added.md: "`snapl dashboard`: a local, read-only dashboard and JSON API over the audit trail, Temporal, the Source of Truth, CI and recorded live lab runs (spec 010)." Add changelog/101.changed.md: "`invoke test-e2e` now records each run under `.snapl/test-runs/`; new `invoke test-integration`."
- [ ] T062 Mark ADR-0005 `Accepted` for its 010 section once the implementation PRs merge.
- [ ] T063 Run `uv run invoke lint`, `uv run mypy packages/`, `uv run invoke test-unit` and `uv run invoke scan`. Confirm ≥ 80% coverage on `snapl_presentation/dashboard/` and `tasks/lab_runs.py`. Walk the whole of quickstart.md once more and paste the evidence into the final PR.

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1 (Setup)** has no dependencies.
- **Phase 2 (Foundational)** depends on Phase 1 and blocks every story.
  - T013 (live fixture) depends on T011 and T011a, and blocks the fixture-based assertions in T014.
- **US1 (Phase 3)** depends on Phase 2. It is the MVP.
- **US2 (Phase 4)** depends on Phase 2 and on T020 (`Cached` and `AuditSource`) and T021 (`create_app`) from US1. It does not depend on US1's projections.
- **US3 (Phase 5)** depends on T020 and T021 only.
- **US4 (Phase 6)** depends on T021 only. The lab-run writer (T045) depends on nothing in the package.
- **US5 (Phase 7)** depends on every source existing (US1, US2 and US4).
- **Polish** comes after all stories.

### Within each story

Tests first, seen failing → projections and sources → routes → page → live evidence.

### Parallel opportunities

- **Phase 1**: T002 and T003 in parallel.
- **Phase 2**: T005 to T008 and T007a (tests) in parallel, then T010, T011 and T011a in parallel. T009 and T012 touch different files and can run alongside.
- **US1 tests**: T014 to T018 all [P].
- **US3 and US4** can be built in parallel once T020 and T021 exist.
- **US4**: T046 and T047 in parallel. Its test tasks T039 to T044 are all [P].

### Parallel example: US4

```text
T039 fixtures  |  T040 writer tests  |  T042 reader tests  |  T043 CI tests
        ↓                ↓                    ↓                    ↓
               T045 writer        T046 reader          T047 CI client
                                        ↘          ↙
                                   T048 routes → T049 page
```

## Implementation Strategy

### MVP first

1. Phases 1–2 (PR A), including the live fixture (T013).
2. Phase 3, US1 (PR B). **Stop and validate**: `snapl dashboard` answers "is the fabric in sync?" on its own.

### Incremental delivery

3. US2 (PR C): what the loop did.
4. US3 (PR D) and US4 (PR E), in either order.
5. US5 and polish (PR F): failure behaviour hardened, docs, changelog.

Each PR is shippable and leaves `main` green.

### Lab-only tasks

T013, T032, T050 and T056 need the maintainer's Containerlab fabric. Every other task runs anywhere, including a cloud session.
