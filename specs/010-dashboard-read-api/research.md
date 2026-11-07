# Research: Dashboard Read API and MVP Page

**Feature**: 010-dashboard-read-api | **Date**: 2026-11-07

Each entry records a decision, why it was made, and what was rejected. Facts about the existing code were read from the tree at `main` (122343f).

---

## R1 — HTTP framework

**Decision**: FastAPI, served by uvicorn. Both are dependencies of `snapl-presentation` only. Tests use FastAPI's `TestClient` (httpx, already in `uv.lock`).

**Rationale**:
- The response contract is a set of Pydantic v2 models, and the constitution makes Pydantic the model layer.
- FastAPI serialises those models directly and generates an OpenAPI document from them. Spec 011's TypeScript types can be generated from that document, so the contract has one source.
- It is async, which the Temporal client and `aiosqlite` need.

**Checked on FastAPI 0.162.2** (2026-11-07), because R2 and the tests depend on it:
- A route declared with `@router.get` answers `GET` only. `HEAD` gets `405`, like any other method. The framework's own routes (`openapi_url`, `/docs`, `/redoc`) answer `GET` and `HEAD`.
- An included router shows up in `app.routes` as one `_IncludedRouter` entry. It has no `path` and an empty method set, and its routes sit under `.original_router.routes`.
- `fastapi.testclient` warns that using it with `httpx` is deprecated in favour of `httpx2`. This is only a warning, since pytest doesn't treat warnings as errors here.

**Alternatives considered**:
- **Starlette alone**: smaller, but the response-model validation and OpenAPI generation would be hand-written.
- **aiohttp**: no Pydantic integration.
- **stdlib `http.server`**: synchronous, so every Temporal call would need its own event loop.

The framework is a new required technology, recorded in ADR-0005.

## R2 — Enforcing read-only (FR-003, SC-006)

**Decision**: enforce it in three layers.

1. **Routes**: every route is declared with `@router.get`, so it answers `GET` only, and `HEAD` gets `405` like every other method (R1). The app sets `docs_url=None` and `redoc_url=None`. The Swagger and ReDoc pages load scripts from a CDN, which FR-008 rules out, and the OpenAPI document stays at `/api/v1/openapi.json`.
   - A unit test collects every route and fails on any route with a method outside `{GET, HEAD}`. Only the framework's OpenAPI route answers `HEAD`.
   - The test descends into each included router's `original_router.routes`. A flat walk of `app.routes` passes vacuously, because the included-router entry has an empty method set.
   - It also asserts that it found every route listed in `contracts/dashboard-api.md`.
2. **Audit trail**: opened read-only. `SqliteAuditLog.initialize()` runs the schema DDL and sets WAL mode, which are writes. A new constructor flag, `read_only=True`, opens the database as `file:<path>?mode=ro` with `uri=True`, skips the DDL, and refuses `append()` with `AuditLogError`. No change to the `AuditLog` ABC.
3. **Other sources**: only read calls on Temporal (`list_workflows`, `describe_task_queue`, `find_drift_schedule`) and on the intent store (`get_desired_state`, `identify`). A test fakes each client and fails on any call outside that list.

**Rationale**: a route test alone would not catch a handler that writes. The audit trail is the only source a careless handler could actually change, so it is opened in a mode SQLite itself enforces.

**Alternatives considered**: a method-filtering middleware. It is redundant with (1), and it hides the intent from anyone reading the route table.

## R3 — Reading the audit trail at scale (SC-007)

**Decision**: an in-memory **read model** that is built once and then extended.

- At startup the server reads the whole trail once through `query_by_time_range(epoch, now)`.
- **On every refresh** it re-reads a trailing window, from 5 minutes before the newest timestamp seen up to now, through the same ABC method (FR-006). Events already held are dropped by `event_id`.
- **Every 15 minutes** it rebuilds the read model from a full read in the background, and swaps it in only once the read has succeeded.
- The projections (per-device state, runs, schedule repairs, history buckets) are pure functions in `dashboard/projections.py`, from events to view models, so the unit tests need no database. They order events by `timestamp`, never by arrival, because arrival order is not timestamp order (below).

**Fact: rows do not arrive in timestamp order.** An event's `timestamp` is `workflow.now()`, taken when the workflow builds the event (`scan_drift.py:77`). The row is inserted later, by the `record_audit_event` activity.
- The activity can be retried up to 5 times: 1, 2, 4 and 8 s apart, with a 10 s timeout per attempt (`_AUDIT_RETRY` in every workflow). A healthy worker can therefore insert a row about a minute after its timestamp.
- If the worker dies between building the event and writing it, the row is inserted only once a worker picks the activity up again. That has no upper bound.
- `ScanDrift` writes its per-device events from parallel activities, and several workflows write at once.

So a read of "timestamp at or after the newest one seen" would skip any row that lands after a newer row has already been read, for as long as the process runs. Append-only rules out updates, not late inserts.

**What the window guarantees**:
- **5 minutes** covers the whole retry schedule several times over. Any row a healthy worker writes is seen on the next refresh.
- **The 15-minute rebuild** catches rows delayed longer, for example by a worker restart. They appear at most 15 minutes after they land, never silently dropped. This is a stated limitation in `contracts/dashboard-api.md`.
- At 5 s refreshes, the window costs a few hundred rows per read on a busy fabric.

**Rationale**: the `AuditLog` ABC has no "latest per device" or "count by bucket" query, and adding one would widen a block contract for a single consumer.
- 100,000 audit rows load in a few seconds once. After that, each refresh reads only the trailing window.

**Alternatives considered**:
- **A new ABC method per view**: widens the contract (Principle V) and pushes view logic into the Orchestrator.
- **Querying SQLite directly with GROUP BY**: forbidden by FR-006, and it couples Presentation to the storage schema.
- **Re-reading the whole trail every 5 seconds**: fails SC-007.
- **A cursor on the row id** (`query_since(row_id)` in the ABC): exact, and the cleanest fix for late rows. Rejected for 010 because it widens the ABC, and the event model carries no row id to hand back as a cursor. If the 15-minute rebuild proves too slow in practice, this is the fallback, as a spec amendment of its own.

## R4 — Device drift state and the missing/undesired split (FR-012, FR-012a)

**Decision**:
- **Device state** comes from the latest `detect_drift` per-device event that `ScanDrift._audit_device` writes, keyed on `target_id`.
- **New payload field**: `_audit_device` gains `"undesired_paths": [...]`, the subset of `paths` whose `DriftItem.undesired` is true. This is an additive change to the workflow's audit payload. The signature, the existing fields and determinism are unchanged.
- **Classifying a drifted device**:
  - `missing` when no path is undesired
  - `undesired` when every path is undesired
  - `missing_and_undesired` otherwise
- An older event without the field is classified as `drifted`.
- `error` comes from an `ACTIVITY_FAILED` event, with its `payload.error`.
- `never_scanned` means no event for the device.

**Rationale**: today the audit payload records `paths` only, so the direction of each difference is lost after the scan returns. The CLI shows it only for the scan it just ran. The dashboard reads history, so the direction has to be stored. Guessing it from old events would break ADR-0004 (claim only what is true).

## R5 — Workflow runs, grouping and trigger (FR-016, FR-018)

**Fact**: `DeployIntent` runs under a deterministic id, `deploy-intent-{device_id}`, from both the CLI and `ReconcileDevices`. One workflow id therefore holds many runs. `ScanDrift` and `ReconcileDevices` take a random suffix from the CLI. `DriftResponse` children are `{parent}-scan` and `{parent}-reconcile`.

**Decision**:
- **Splitting runs**: a run is the events of one `workflow_id` from a `WORKFLOW_STARTED` up to the next terminal event (`WORKFLOW_TERMINATED` or `WORKFLOW_CANCELLED`). A `WORKFLOW_STARTED` with no terminal event yet is open. It is shown as running only if Temporal lists that id as running (R6). Otherwise it is shown as `unfinished`, for example when the worker died mid-run.
  - *Amended in PR C*: a new `WORKFLOW_STARTED` under an id whose previous run is still open closes that run as `unfinished`. Only the latest open run of an id can be `running`, because Temporal runs one execution per id at a time. An event whose `WORKFLOW_STARTED` row has not arrived yet is left out until it does (rows land late, R3), rather than inventing a start time.
  - *Amended in PR C*: when Temporal cannot be asked, an open run is shown as `unfinished`, since nothing confirms it is still going. The running panel carries Temporal's reason and remedy.
- **New payload field (FR-016a)**: `DeployIntent`'s `WORKFLOW_STARTED` event gains `"parent_workflow_id"`. It holds `workflow.info().parent.workflow_id` when the run is a child, and `null` when an operator started it. This is an additive payload change, like FR-012a. The signature and existing fields are unchanged. Replay is unaffected, because Temporal does not compare activity inputs, and `workflow.info()` is deterministic.
- **Trigger**:
  - `schedule` if the run is a `DriftResponse` (its events carry `actor="schedule:…"`), or is named in one of that run's payloads (`scan_workflow_id`, `reconcile_workflow_id`).
    - *Amended in PR C*: a `DriftResponse` names its children in its payload only when it ends, and never when it fails. Its children's ids are fixed by `drift_response.py` (`{wf}-scan`, `{wf}-reconcile`), so a child is also `schedule` when its id is one of those for a `DriftResponse` in the trail.
  - A `DeployIntent` run takes its parent's trigger: `schedule` when `parent_workflow_id` is a scheduled reconcile, `operator` when it is an operator-started reconcile. With `parent_workflow_id: null` it is `operator`.
    - *Amended in PR C*: a parent id of the scheduled form (`…-reconcile`) whose `DriftResponse` is not in the trail gives `unknown`. Its form suggests the schedule, but the run that would confirm it is not there.
  - A `DeployIntent` run recorded before FR-016a has no `parent_workflow_id` key. Its trigger is `unknown`.
  - Everything else is `operator`.
- **Why not infer it from timing**: a first draft called a `DeployIntent` `schedule` when it started inside a scheduled reconcile's window for the same device. That mislabels a real case. A reconcile skips a device whose deploy is already in flight (`reconcile_devices.py:151`). An operator who runs `snapl deploy leaf-01` just after a scheduled reconcile starts, and before it reaches `leaf-01`, would have the run credited to the schedule. ADR-0004 rules that out. Only the parent id is evidence.
- **Actor names**: only the actor the event carries. Operator-started workflows record none today. The spec was amended to say so, and adding an actor to them is out of scope.

## R6 — What is running now

**Decision**: reuse `snapl_orchestrator.worker.client.list_running_workflows` (the call behind `snapl status`), filtered to the task queue. Reuse the poller count from `cli.count_pollers` for FR-021.
- Both functions move to a small shared module, `snapl_presentation.probes`, together with `_probe` and `_connect`. The CLI and the dashboard then use one implementation.
- `_connect`'s TCP probe stays. The comment in `cli.py` records why: `Client.connect` blocks the event loop on a dead host, which would freeze every view, not only Temporal's.

## R7 — Schedule view and repeated failed repairs (FR-019, FR-020)

**Decision**:
- **Current state**: `schedules.find_drift_schedule` returns `DriftScheduleView` (mode, interval, paused, note, next and recent runs), which is current from Temporal.
- **Pause reason and actor**: the latest `SCHEDULE_CHANGED` audit event.
- **Consecutive failed repairs**: from `DRIFT_RESPONSE` per-device events with `decision == "remediated"`. For each device, walk its scheduled responses newest first and count `result == "failed"` until the first `succeeded`.
  - A scheduled run that did not attempt the device, because it was clean or excluded, does not reset the count.
  - `unknown` and `skipped` neither count nor reset.
  - The threshold is 3.

## R8 — History series (FR-022, FR-023)

**Decision**: each series is computed on the server from the read model, for a given `start`, `end` and `bucket`.
- **Bucket**: one of `5m`, `1h`, `1d`, `1w`. The server rejects any range with more than 2,000 buckets.
- **`drifted_devices`** and **`undesired_paths`**: one point per scan (the `ScanDrift` workflow's terminal event), not per bucket, so the induced-drift spike is not averaged away. A bucket with no scan has no point. That is the "gap, not zero" of US3 scenario 3.
- **`deploys`** and **`repairs`**: counts per bucket by outcome. A bucket with no scan *and* no deploy is returned as `null` for every outcome, not 0.
- **Markers**: one per `ReconcileDevices` run and per `DriftResponse` run with `mode == remediate`.

## R9 — CI status (FR-024, FR-025)

**Decision**:
- **Repository**: from `git remote get-url origin`, overridable with `SNAPL_GITHUB_REPO`.
- **API calls**, through the GitHub REST API with httpx:
  1. `GET /repos/{repo}/actions/runs?branch=main&per_page=1&workflow_id=…`
  2. `GET /repos/{repo}/pulls?state=open`
  3. For `main`'s head and each open PR's head: `GET /repos/{repo}/commits/{sha}/check-runs`. The check runs named under the quality gate give the per-job results.
- **Coverage**: from Codecov's public API for the repository (`api.codecov.io/api/v2/github/{owner}/repos/{repo}`, totals for the branch), so no artifact download and no token are needed.
- **Token**: `GITHUB_TOKEN` is used when set (FR-025).
- **Cache**:
  - Responses are cached with their `ETag` and re-asked with `If-None-Match`. GitHub does not count a `304` against the primary rate limit, but **only when the request carries an `Authorization` header**. That is from GitHub's REST best-practices page, checked 2026-11-07. Without a token, every request counts, `304` or not, so ETags save bandwidth there but not budget.
  - The time between refreshes is 120 s with a token. Without one it is 600 s, which is 6 refreshes an hour. A refresh costs `3 + N` calls: the `main` run, the PR list, and the check runs for `main` and each of `N` open PRs. With 5 open PRs that is 8 × 6 = 48 calls an hour, under the unauthenticated 60.
  - **Unauthenticated PR cap**: 7 PRs would be 10 calls a refresh, which is exactly 60 an hour with nothing left for a restart. So without a token, check runs are fetched for at most **5** open PRs, the most recently updated first. `CiStatus.omitted_prs` counts the rest, and the view's remedy is `export GITHUB_TOKEN=…`. With a token there is no cap.
  - When `X-RateLimit-Remaining` drops below 10, the server stops asking until the reset time, and keeps serving the last answer with its age (FR-031).
  - Codecov's API is a separate service and costs nothing against GitHub's limit.
- One fetch runs at a time no matter how many tabs poll (single-flight lock).

**Alternatives considered**:
- **Downloading the `test-results` artifact for coverage**: needs a token even for a public repository.
- **`gh` CLI**: not installed on every machine, and it would shell out on every refresh.

## R10 — Recording live lab runs (FR-026 to FR-029)

**Decision**: the invoke tasks wrap pytest.
- `invoke test-e2e` keeps its body, and a new `invoke test-integration` runs `tests/integration -m integration` against the live stack. Both call one helper, `tasks/lab_runs.py`.
- **Folder**: each run gets `.snapl/test-runs/<UTC timestamp>-<suite>/`.
- **Before pytest starts**, the helper writes `run.json` with:
  - `format: 1`, `suite`, `status: "running"`, `started_at`
  - `commit` (`git rev-parse HEAD`), `tree` (`git rev-parse HEAD^{tree}`), `branch`
  - `dirty` (`git status --porcelain` non-empty)
  - `device_image`: the configured `image:` from `containerlab/dcfabric.yml`, recorded as configured, not inspected
  - `pid`
- **pytest** runs with `--junitxml=<dir>/junit.xml`.
- **In a `finally`**, the helper rewrites `run.json` with `status` (`passed` | `failed` | `interrupted`), `ended_at`, `exit_code` and the counts parsed from the JUnit file.
- **Incomplete runs**: a run whose `status` is still `running` while its `pid` is not alive (or not ours) is shown as `incomplete`. That covers a killed terminal or a laptop lid closed mid-run.
- **Gitignore**: `.snapl/` is added to `.gitignore`. `demo.reset` removes Docker volumes and the lab only (`tasks/demo.py:reset`), so the records survive it with no change (FR-029). A unit test in `test_demo.py` pins that.

**Commits since (FR-028)**: counted against the local `main` ref, and the view says "as of your last fetch".

**Fact: `main` is squash-merged.** It is linear, with one commit per PR. A live run is usually recorded on a PR branch, because that is where Principle III asks for evidence. That commit never becomes an ancestor of `main`. An ancestor test alone would show "unknown" for nearly every run that matters.

The reader resolves the run's commit to a commit on `main` in this order:
1. **Ancestor**: if `git merge-base --is-ancestor <commit> main` succeeds, count `git rev-list --count <commit>..main`. `matched_by` is `ancestor`.
2. **Same tree**: otherwise, look for the newest commit among the last 500 on `main` (`git log -n 500 --format='%H %T' main`) whose tree equals the run's `tree`. If one exists, count from it. `matched_by` is `tree`. This works because a PR must be up to date with `main` before it merges. Squashing an up-to-date branch gives a commit whose tree is the branch head's tree.
3. **Unknown**: otherwise, the count is `null` with a reason:
   - "commit not on main yet" when the commit still exists locally (an open PR, or a run made before later pushes to the branch)
   - "commit not in local history" when `git cat-file -e` fails (a rebased-away commit)
   - "run recorded without a tree" for an older record

A tree match does not hold if the run was made on an earlier commit of the PR than the one merged, or if the run was dirty. The dirty case is already flagged by `dirty`, and the view shows `matched_by` next to the count.

## R11 — The MVP page (FR-008 to FR-010)

**Decision**: one static `index.html` with plain JavaScript and inline CSS, shipped as package data (`snapl_presentation/dashboard/static/`) and served by FastAPI at `/`.
- Every 5 seconds it fetches `/api/v1/overview`, and every 60 seconds `/api/v1/history` for the last 24 hours.
- It renders tables with `textContent`, never `innerHTML`, because audit payloads hold device-supplied strings.
- No framework, no build step, no request to any other origin.

**Rationale**: the page exists to prove the data before spec 011 designs anything. Spec 011 replaces it, and the API does not change.

## R12 — Links into the Temporal Web UI (FR-017)

**Decision**: `TEMPORAL_UI_ADDRESS`, defaulting to `http://localhost:${TEMPORAL_UI_PORT:-18034}`, read in `CliSettings`. Links have the form `{ui}/namespaces/{ns}/workflows/{workflow_id}`.
- The audit trail stores no Temporal run id. For `deploy-intent-{device}`, the link therefore opens the workflow id's page, which lists every run. Documented as a known limitation in the contract.

## R13 — Source of Truth device list and identity (FR-011, FR-015)

**Decision**:
- **Device list**: `load_states(build_store(settings), settings, use_case=…)`. Role comes from `DesiredState.device.role`.
- **Identity check**: `identify_sot`, the same check `snapl status` and spec 009 FR-031 use, run before the list is used. A foreign Infrahub makes the device view unavailable, with the identity detail as its reason.
- **Cache**: 30 s, since the SoT changes only on seed or edit.
- **SoT down**: the device view falls back to the device ids seen in the audit trail, with names from the last successful load in this process. It is labelled as `degraded`, with no roles.

## R14 — Serving, binding and the port (FR-001, FR-002)

**Decision**:
- **Command**: `snapl dashboard [--use-case dcfabric] [--port N] [--host 127.0.0.1]` runs uvicorn programmatically.
- **Port**: from `DASHBOARD_PORT`, default 18030, already reserved in the registry.
- **Port in use**: the bind is checked before uvicorn starts. If the port is taken, the command exits non-zero, naming the port and `DASHBOARD_PORT`, and never tries another (#107).
- **Non-loopback host**: prints a one-line warning that the dashboard has no authentication.

## R15 — Contract shape and versioning (FR-004, FR-005)

**Decision**: path versioning, `/api/v1/…`.
- **Envelope**: every response has `contract_version`, `use_case`, `generated_at`, and one or more views.
- **View**: each view is `{source, available, fetched_at, reason?, remedy?, stale?, data}`. `data` is `null` when the source never answered.
- **Contract test**: the OpenAPI document is snapshot-tested, so an accidental contract change fails a unit test, and an intended one updates the snapshot in the same PR.

## R16 — Where the code lives

**Decision**: a `snapl_presentation.dashboard` subpackage: `app.py`, `sources.py`, `projections.py`, `models.py`, `ci.py`, `lab_runs.py` and `static/`.
- **Dependency direction**: unchanged, `presentation → orchestrator → {intent, …}`. Presentation already imports intent for `resolve.py`.
- **Lab-run reader**: `snapl_presentation.dashboard.lab_runs` reads what `tasks/lab_runs.py` writes. The format is in `contracts/lab-runs.md`, and both sides test against the same fixture.

## R17 — Skills for this and later specs

**Decision**: no skill is vendored for 010. The page is deliberately plain.
- For 011, `anthropics/skills` `frontend-design` and `webapp-testing` were reviewed in full on 2026-11-07 at commit `2c5958e`. Both are Apache-2.0 and instructions-only, except a 105-line local server runner in `webapp-testing`. Recorded in #146.
- The public repository has no dataviz skill. Chart rules go into `dev/skills/dashboard/`, written once this contract has settled.
