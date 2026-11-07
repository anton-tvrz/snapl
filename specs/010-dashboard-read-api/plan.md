# Implementation Plan: Dashboard Read API and MVP Page

**Branch**: `010-dashboard-read-api` | **Date**: 2026-11-07 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/010-dashboard-read-api/spec.md`

## Summary

Give the loop one place to look at. `snapl dashboard` runs a local, read-only FastAPI server on 18030.
- It brings together the audit trail, Temporal, the SoT, GitHub CI and recorded live lab runs.
- It exposes them as a typed, versioned JSON API (`/api/v1/…`), and serves one plain page that renders it every 5 seconds.

The design:
- **Read model**: the audit trail is read through the `AuditLog` ABC only, once at startup, then by re-reading a trailing 5-minute window, with a full rebuild every 15 minutes because rows land out of timestamp order. It feeds pure projections: device state, runs, repeated repair failures and history series (R3).
- **Read-only, three ways** (R2):
  - GET-only routes, pinned by a route-table test
  - the audit trail opened in SQLite read-only mode, through a new `read_only` flag on `SqliteAuditLog`
  - fake clients that fail on any write call
- **Failure is data**: every view carries source, availability, `fetched_at`, staleness, reason and remedy. A source failure never becomes an HTTP error, and never blocks other views (R15).
- **Three small additions outside Presentation**:
  - `ScanDrift` records `undesired_paths` per device, so drift direction survives into history (FR-012a, R4)
  - `DeployIntent` records `parent_workflow_id` when it starts, so a run's trigger is recorded, not inferred from timing (FR-016a, R5)
  - `SqliteAuditLog(read_only=True)`
  No ABC, workflow signature or schema changes.
- **Lab evidence**: `invoke test-e2e` and a new `invoke test-integration` record each live run under `.snapl/test-runs/`. The dashboard compares those runs against local `main` (R10).
- **CI**: GitHub REST with ETags and an adaptive refresh, plus Codecov's public API for coverage. It works with no token (R9).

Research decisions are in [research.md](research.md), types in [data-model.md](data-model.md), interfaces in [contracts/](contracts/), and usage and live-evidence steps in [quickstart.md](quickstart.md). The new technologies are recorded in [ADR-0005](../../dev/adr/0005-dashboard-and-metrics-stack.md).

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**:
- New in `snapl-presentation`:
  - `fastapi>=0.115`
  - `uvicorn>=0.32` (plain, no `[standard]` extras)
  - `httpx>=0.28`, already locked transitively, now declared directly for the CI client
- Existing: pydantic, typer and rich (presentation), temporalio and aiosqlite (orchestrator).
- No Node, and no front-end build.

**Storage**:
- Reads the existing SQLite audit trail, read-only.
- Writes only `.snapl/test-runs/` (from invoke tasks, not the server).
- No new database. Caches are in-memory and per-process.

**Testing**: pytest, markers `unit` and `integration`.
- `TestClient` against the app with fake sources.
- Projections tested as pure functions over event fixtures. One fixture is a real audit DB captured from a live demo-arc run (Principle III), committed under `tests/unit/test_presentation/fixtures/`.
- The OpenAPI document is snapshot-tested.
- `test_lab_runs.py` drives the helper with a stub pytest command.
- One integration test serves the app against `WorkflowEnvironment.start_local()` and a temp audit DB.

**Target Platform**: macOS with OrbStack/Docker. The dev stack runs from `development/docker-compose.yml`, and the dashboard runs on the host next to the worker.

**Project Type**: an addition to the `snapl-presentation` library package and CLI, plus invoke tasks.

**Performance Goals**:
- Every view answers in ≤ 1 s with 100,000 audit events (SC-007).
- A change appears ≤ 10 s after its audit record (SC-001).
- The page loads in ≤ 2 s with any source down (SC-003).

**Constraints**:
- Read-only (FR-003).
- Loopback bind by default (FR-002).
- Never falls back to another port (#107).
- GitHub unauthenticated rate limit: ≤ 60 calls/hour across all tabs.
- No vendor names in models (Principle VI).

**Scale/Scope**:
- One viewer and one use case per server.
- `dcfabric` has 6 devices.
- The audit trail can hold up to roughly 10⁵ events.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. NAF Alignment | PASS | An operator-facing view is Presentation. It reads the Orchestrator's audit log and schedule view and the Intent store, along the existing `presentation → orchestrator → {…}` direction. No new package, and no new ADR-0004 deviation. |
| II. Spec-Driven Development | PASS | A new capability, specified before implementation. |
| III. TDD | PASS | Every new source file is paired with its test file, listed first (Project Structure). Live evidence is spelled out in quickstart.md for the two workflow payload changes, FR-012a and FR-016a. A captured live audit DB backs the projection tests. |
| IV. Modularity | PASS | No new package. `projections.py` is pure. Sources sit behind small protocols, so each view is unit-testable without infrastructure. |
| V. Contract-First | PASS | `contracts/dashboard-api.md`, `cli-dashboard.md` and `lab-runs.md` come before code. The OpenAPI snapshot makes the contract a test. No block ABC changes. |
| VI. Single Vendor, Named Seams | PASS | No vendor names in dashboard models. `device_image` is an opaque string read from the lab topology. `test_vendor_seams.py` is unaffected, since no workflow imports change. |
| VII. Simplicity | JUSTIFIED | Adds two runtime dependencies and an in-memory read model. See Complexity Tracking. No ABC methods, no new store, no background workers beyond per-source caches. |
| VIII.1 Authority split | PASS | The dashboard shows intent and observed state side by side and never promotes either. |
| VIII.2 Drift response | PASS | Read-only. Responses stay with the CLI, which records the actor. The dashboard only shows the command. Repeated failed repairs are surfaced, not acted on. |
| VIII.5 Undesired config | PASS | Undesired paths are shown with "not removed automatically (#65)". No removal path is introduced. |

**Gate result (pre-research)**: PASS. No `[NEEDS CLARIFICATION]` in the spec. Scope was settled 2026-11-07.

## Project Structure

### Documentation (this feature)

```text
specs/010-dashboard-read-api/
├── spec.md                 # Feature specification
├── plan.md                 # This file
├── research.md             # Phase 0
├── data-model.md           # Phase 1
├── quickstart.md           # Phase 1 (incl. live-evidence checklist)
├── contracts/
│   ├── dashboard-api.md    # HTTP API v1
│   ├── cli-dashboard.md    # `snapl dashboard`, `invoke test-e2e|test-integration`
│   └── lab-runs.md         # .snapl/test-runs record format
├── checklists/
│   └── requirements.md     # Spec quality checklist
└── tasks.md                # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

Test files are listed first in each pair, because CLAUDE.md requires the test before the source.

```text
tests/unit/test_presentation/
├── fixtures/
│   ├── audit_demo_arc.sqlite         # NEW: captured from a live demo-arc run (deploy, drift, scan, reconcile, scan)
│   └── lab_runs/                     # NEW: run.json + junit.xml samples (passed, failed, running, corrupt, format 2)
├── test_dashboard_models.py          # NEW: View invariants, envelope, enum tolerance
├── test_dashboard_projections.py     # NEW: device state (all 7), run splitting on reused ids, trigger
│                                     #   attribution, repeated failures, series buckets/gaps/markers, 100k-event timing
├── test_dashboard_sources.py         # NEW: per-source cache/staleness/timeout; incremental audit read + dedup;
│                                     #   SoT identity refusal; fake clients fail on any write call
├── test_dashboard_ci.py              # NEW: GitHub/Codecov parsing, ETag 304 reuse, adaptive refresh, rate-limit backoff
├── test_dashboard_lab_runs.py        # NEW: reader — incomplete via dead pid, unreadable, commits-since/unknown
├── test_dashboard_app.py             # NEW: every route via TestClient; GET-only route table; 405s; 422s;
│                                     #   each source down (SC-003); OpenAPI snapshot; static page served, no external URLs
├── test_cli_dashboard.py             # NEW: option/env resolution, port-in-use refusal, non-loopback warning
├── test_probes.py                    # NEW: moved _probe/_connect/count_pollers keep their behaviour
└── test_cli.py                       # EXTEND: status still passes after the probes move

tests/unit/test_orchestrator/
├── test_workflow_scan_drift.py       # EXTEND: per-device payload carries undesired_paths ⊆ paths, order kept
├── test_workflow_deploy_intent.py    # EXTEND: WORKFLOW_STARTED carries parent_workflow_id (child vs operator)
└── test_audit_sqlite.py              # EXTEND: read_only — mode=ro, no DDL, append refused, missing file refused

tests/unit/test_tasks/
├── test_lab_runs.py                  # NEW: record written before/after, atomic, status from exit code,
│                                     #   KeyboardInterrupt → interrupted, recording failure never changes exit code
└── test_demo.py                      # EXTEND: demo.reset leaves .snapl/ alone

tests/integration/test_presentation/
└── test_dashboard_local.py           # NEW (integration): app against start_local() Temporal + temp audit DB

packages/presentation/snapl_presentation/
├── probes.py                         # NEW (moved from cli.py): _probe, connect, count_pollers, identify_sot
├── cli.py                            # EXTEND: `dashboard` command; import probes
├── settings.py                       # EXTEND: dashboard_port/host, temporal_ui_address, github_repo/token
└── dashboard/
    ├── __init__.py
    ├── models.py                     # NEW: data-model.md
    ├── projections.py                # NEW: pure events → views
    ├── sources.py                    # NEW: AuditSource (incremental), TemporalSource, SotSource, cache wrapper
    ├── ci.py                         # NEW: GitHub + Codecov client
    ├── lab_runs.py                   # NEW: record reader
    ├── app.py                        # NEW: FastAPI app factory, routes
    └── static/index.html             # NEW: MVP page (no build, no external requests)

packages/presentation/pyproject.toml  # EXTEND: fastapi, uvicorn, httpx; package data for static/

packages/orchestrator/snapl_orchestrator/
├── workflows/scan_drift.py           # EXTEND: _audit_device adds undesired_paths
├── workflows/deploy_intent.py        # EXTEND: WORKFLOW_STARTED payload adds parent_workflow_id
└── audit/sqlite.py                   # EXTEND: read_only flag

tasks/
├── lab_runs.py                       # NEW: record helper
└── main.py                           # EXTEND: test_e2e records; new test_integration

.gitignore                            # EXTEND: .snapl/
dev/adr/0005-dashboard-and-metrics-stack.md   # NEW (this PR)
docs/demo-scenarios.md                # EXTEND (implementation PR): "Watch the loop" scenario
README.md                             # EXTEND (implementation PR): replace the "No metrics or dashboards" limitation
changelog/101.added.md                # implementation PR
```

Unchanged by design:
- Every package's `abc.py`, and `audit/schema.sql`.
- Every workflow signature.
- `reconcile_devices.py`, `drift_response.py` and `schedules.py`. `deploy_intent.py` changes only in its start payload (FR-016a).
- The executor, collector, observability, adapters and templates.
- `development/docker-compose.yml`. Nothing new runs in Compose in 010.

**Structure Decision**:
- **Placement**: a `dashboard/` subpackage inside `snapl-presentation`, because the dashboard is a second Presentation surface next to the CLI and shares its settings and probes.
- **`probes.py`**: the connection helpers move out of `cli.py` into `probes.py` so both surfaces use one implementation. The CLI's behaviour is pinned by its existing tests before the move.
- **Lab runs**: the writer lives in `tasks/` (invoke) and the reader in the package. They meet only at the documented file format.

## Implementation Order (for /speckit-tasks)

Each step is red → green → refactor:

1. **Foundation**:
   - `probes.py` move (refactor, behaviour pinned first)
   - `SqliteAuditLog(read_only=True)`
   - `ScanDrift` `undesired_paths`, and `DeployIntent` `parent_workflow_id`
   - models and settings
   - Capture `audit_demo_arc.sqlite` from a live run, after both payload changes, so the fixture carries them. Live evidence item 1.
2. **US1 (P1)**:
   - device projection
   - `SotSource` with the identity check
   - incremental `AuditSource`
   - `/api/v1/devices`
   - the app factory, GET-only route test and OpenAPI snapshot
   - `snapl dashboard`
   - a minimal page with the device table
3. **US2 (P1)**: run splitting and triggers, repeated failures, `TemporalSource` (running, schedule, worker), `/runs` and `/schedule`, then the page tables. Live evidence item 2.
4. **US3 (P2)**: series projection, `/history`, and a page table of the last 24 h.
5. **US4 (P2)**: `tasks/lab_runs.py` and `test-integration`, the lab-run reader, `ci.py`, `/lab-runs` and `/ci`, then the page tables. Live evidence item 5.
6. **US5 (P3)**: the per-source failure matrix, `/sources`, the page status strip and the SC-003 tests. Live evidence items 3 and 4.
7. **Docs**: the demo scenario, the README limitation, the changelog, and the `AGENTS.md` dashboard entry.

## Post-Design Constitution Re-Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. NAF Alignment | PASS | The design adds no import against the dependency arrows. The two Orchestrator changes are Orchestrator code, used by Presentation in-process, which is the same pattern spec 009 used for `schedules.py`. |
| III. TDD | PASS | Every new module has its test listed first. Projection tests run on a captured live DB, not hand-written events alone. |
| V. Contract-First | PASS | Three contracts written. The OpenAPI snapshot turns contract drift into a failing test. The lab-run format is shared by its writer and reader through one fixture set. |
| VI. Vendor seams | PASS | No new seam is touched. `undesired_paths` is derived from the vendor-neutral `DriftItem.undesired`, and `parent_workflow_id` from Temporal's workflow info. |
| VII. Simplicity | JUSTIFIED | See Complexity Tracking. |
| VIII.2 / VIII.5 | PASS | No write path exists. Undesired config is shown, and never offered for removal. |

## Complexity Tracking

| Addition | Why needed | Simpler alternative rejected because |
|-----------|------------|-------------------------------------|
| FastAPI + uvicorn | A typed HTTP contract generated from the same Pydantic models, async for Temporal and aiosqlite, and an OpenAPI document spec 011 can generate types from (R1) | stdlib `http.server` is synchronous. Starlette alone would mean hand-writing validation and OpenAPI |
| In-memory read model with incremental reads | SC-007 (100k events in ≤ 1 s) without widening the `AuditLog` ABC (R3) | New ABC queries push view logic into the Orchestrator. Direct SQL breaks FR-006. Full re-reads fail SC-007 |
| `undesired_paths` in the scan audit payload | Drift direction is otherwise lost after the scan returns, and history would have to guess it (R4, ADR-0004) | Inferring direction from `desired is None` is exactly the inference `DriftItem.undesired` exists to avoid |
| `parent_workflow_id` in the deploy start payload | A run's trigger must be recorded, not inferred from timing. A reconcile skips a device whose deploy is in flight, so a time window credits operator deploys to the schedule (R5, ADR-0004) | Matching deploys to reconcile windows by time is the inference that mislabels |
| Trailing-window re-read plus a 15-minute rebuild | Audit rows land out of timestamp order, because each row is written by a retried activity after its timestamp is taken (R3) | "Read from the newest timestamp seen" silently drops late rows. A row-id cursor widens the ABC |
| ETag + adaptive refresh for CI | Unauthenticated GitHub allows 60 calls/hour, and the page polls every 5 s (R9) | A fixed short TTL exceeds the limit with a handful of open PRs |
