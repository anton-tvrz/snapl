# Implementation Plan: Measured Metrics and Long-Lived History

**Branch**: `012-measured-metrics` | **Date**: 2026-11-08 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/012-measured-metrics/spec.md`

## Summary

Measure the loop, keep what is measured, and show it on the dashboard.

- **Worker**:
  - `run_worker` builds a Temporal `Runtime` with a Prometheus exporter on `127.0.0.1:18091`, falling back to no metrics if the port is taken (R3).
  - The two activities that call gNMI, `apply_config` and `collect_drift_state`, time each call and record it through `activity.metric_meter()` (R1, R5).
  - No new dependency, and no ABC or workflow change.
- **Stack**:
  - A `prometheus` Compose service, pinned to v3.10.0, with size-only retention and data in `./.snapl/prometheus/` (a bind mount, so `demo.reset` keeps it).
  - It scrapes the worker and the dashboard (R6).
- **Dashboard**:
  - A `PrometheusSource` behind 010's `_Reader`, and `GET /api/v1/metrics/{view}` with six named views (R7).
  - The dashboard's own `GET /metrics` exports source health (R8).
  - A Metrics section on the page.
- **Tasks**: `invoke history.archive` (R9) and `invoke history.purge` (R10).

Research decisions are in [research.md](research.md), types in [data-model.md](data-model.md), interfaces in [contracts/](contracts/), and live evidence in [quickstart.md](quickstart.md). Prometheus is ADR-0005 Decision 3.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**:
- **None new in Python.**
  - The worker uses temporalio's runtime metrics (1.25, already locked).
  - The dashboard uses httpx (already declared in 010) and renders `/metrics` text itself (R8).
- **New service**: `prom/prometheus:v3.10.0` in `development/docker-compose.yml`.

**Storage**:
- `./.snapl/prometheus/` (Prometheus TSDB, host bind mount, git-ignored by 010's `.snapl/` rule).
- `./.snapl/archives/` (archives).
- The audit DB is read only, through SQLite's backup API, by the archive task.

**Testing**: pytest, marked `unit` and `integration`.
- **Worker metrics:** a test-owned `WorkflowEnvironment.start_local()` with a `Runtime` on a free port, scraping the endpoint over HTTP. Allowed overnight (test servers that tests start themselves).
- **Dashboard:** `TestClient` with an httpx `MockTransport` serving the captured fixtures (R11).
- **Invoke tasks:** a fake `ctx`, temp dirs, and a stub HTTP server for the admin API and the dashboard.
- **Compose:** a YAML-level test of `development/docker-compose.yml`, covering image pin, flags, bind mount, loopback port and `extra_hosts`.

**Target Platform**: macOS with OrbStack. Linux is documented, not automated (R2).

**Project Type**: additions to `snapl-orchestrator` (worker and activities), `snapl-presentation` (dashboard), invoke tasks, and Compose.

**Performance Goals**:
- SC-001: within 60 s of the call (15 s scrape, plus 15 s cache, plus a 5 s page poll).
- A metrics view answers within 010's 1.5 s request budget or degrades to stale.

**Constraints**:
- Executor and collector are unchanged (FR-006).
- The dashboard is GET-only (010).
- Nothing deletes metrics except a confirmed purge (FR-010).
- No per-path labels (SC-006).

**Scale/Scope**: 6 devices, 15 s scrapes, months of data under a 2 GB cap.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. NAF Alignment | PASS | Metrics are Observability's concern, measured where the Orchestrator calls Executor and Collector. Executor and Collector stay unaware (ADR-0005). The dashboard reads Prometheus as a fifth external source, in Presentation. |
| II. Spec-Driven Development | PASS | A new capability, specified first. Scope was settled in #147 and the four open points on 2026-11-08. |
| III. TDD | PASS | Each new module's test is listed first. The live evidence (quickstart items 1–6) is the lab half. Fixtures are real captures (R11). |
| IV. Modularity | PASS | One helper names the metric (`snapl_orchestrator/metrics.py`). `PrometheusSource` sits behind 010's source protocol. |
| V. Contract-First | PASS | `contracts/metrics-api.md` and `contracts/history-tasks.md` come before code. The OpenAPI snapshot gains the new route and models. No ABC changes. |
| VI. Single Vendor, Named Seams | PASS | Labels are vendor-neutral (`device`, `operation`). No SR Linux names in metrics. |
| VII. Simplicity | JUSTIFIED | A new Compose service and a host directory. See Complexity Tracking. No new Python dependency. |
| VIII.2 Drift response | PASS | Read-only: metrics trigger nothing. |

**Gate result (pre-research)**: PASS. No `[NEEDS CLARIFICATION]` remain. The four open points were answered by the maintainer.

## Project Structure

### Documentation (this feature)

```text
specs/012-measured-metrics/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── metrics-api.md
│   └── history-tasks.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
packages/orchestrator/snapl_orchestrator/
├── metrics.py                    # NEW: GnmiOperation, CallOutcome, record_gnmi_call (R5)
├── worker/metrics.py             # NEW: build_runtime(port, bind) -> Runtime | None (R3)
├── worker/run.py                 # build_runtime, then Client.connect(runtime=...)
└── activities/{executor,collector}.py   # time each call, record it

packages/presentation/snapl_presentation/
├── settings.py                   # prometheus_address
└── dashboard/
    ├── models.py                 # SourceName.PROMETHEUS, MetricsViewName, MetricPoint, MetricSeries, MetricsData, MetricsResponse
    ├── prometheus.py             # NEW: PrometheusSource, VIEWS, choose_step, parse_matrix (R7)
    ├── exposition.py             # NEW: render the /metrics text from _Reader's outcomes (R8)
    ├── app.py                    # /api/v1/metrics/{view}, /metrics, the reader records outcomes, sources gain prometheus
    └── static/index.html         # Metrics section

tasks/history.py                  # NEW: archive, purge (R9, R10)
tasks/dev.py                      # deps creates .snapl/prometheus (mode 0777)
development/docker-compose.yml    # prometheus service
development/prometheus/prometheus.yml   # NEW: scrape config

tests/unit/test_orchestrator/test_metrics.py                 # NEW
tests/unit/test_orchestrator/test_worker_metrics.py          # NEW
tests/unit/test_presentation/test_dashboard_prometheus.py    # NEW
tests/unit/test_presentation/test_dashboard_exposition.py    # NEW
tests/unit/test_presentation/test_dashboard_app.py           # extended
tests/unit/test_tasks/test_history.py                        # NEW
tests/unit/test_infrastructure/test_compose.py               # extended
tests/integration/test_orchestrator/test_worker_metrics_live.py   # NEW: start_local + a real runtime endpoint
tests/unit/test_presentation/fixtures/prometheus/            # captured (R11)
```

## Implementation Order (for /speckit-tasks)

Lab-free work comes first, so an unattended run can ship it. Every step is red → green → refactor.

1. **Worker recording** (US1, US5 for the port fallback): `metrics.py`, `worker/metrics.py`, `run_worker` wiring, the activity timing, and an integration test against a self-started dev server.
2. **Dashboard metrics API** (US1, US3, US5): models, `PrometheusSource` on fixtures, the route, `SourceName.PROMETHEUS` in `/sources`, the 010 contract amendment, and the OpenAPI snapshot.
3. **Dashboard export and page** (US3, US1): `exposition.py`, `GET /metrics`, the page's Metrics section.
4. **Stack** (US2): the Compose service, the scrape config, `dev.deps` creating the directory, and the YAML and reset-safety tests. The live bring-up is deferred to the lab.
5. **History tasks** (US4, US2): `history.archive` and `history.purge` with fakes. Live runs are deferred to the lab.
6. **Docs and governance**: README, AGENTS.md, demo scenario, changelog, ADR-0005 facts, port registry, constitution bump (after merge), and the fixture-capture script.

## Post-Design Constitution Re-Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. NAF Alignment | PASS | `record_gnmi_call` is in the Orchestrator, which already imports both blocks' models. No new arrow. |
| V. Contract-First | PASS | Two contracts. The OpenAPI snapshot covers the metrics route and models. `/metrics` is pinned by an exposition test. |
| VII. Simplicity | JUSTIFIED | See below. |

## Complexity Tracking

| Addition | Why needed | Simpler alternative rejected because |
|-----------|------------|-------------------------------------|
| Prometheus service | Measured, long-lived metrics are the feature (#147) | Storing timings in the audit DB turns an append-only trail into a time-series store, and loses the SDK's own metrics |
| Host bind mount, not a volume | `demo.reset` removes volumes; history must survive it (FR-009) | A named volume with an exclusion in `demo.reset` is one forgotten flag away from deletion |
| Size-only retention | A time limit deletes everything after a long break (FR-008, ADR-0005) | — |
| Named views, server-side | No arbitrary PromQL from a browser (FR-011), and one place to change a query | Passing PromQL through is simpler and turns a read-only dashboard into a query proxy |
| Hand-written exposition text | Two gauge families with enum labels (R8) | `prometheus_client` adds a dependency for about 20 lines |
