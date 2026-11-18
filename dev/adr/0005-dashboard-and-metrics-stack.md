# ADR-0005: Dashboard and Metrics Stack

## Status

- **Decision 1** (FastAPI and uvicorn, spec 010): Accepted on 2026-11-08, once spec 010's implementation PRs (#154, #160, #162, #164, #166, #168) had merged.
- **Decision 2** (spec 011): Accepted on 2026-11-10, once spec 011's implementation PRs had merged: #200, #202, #204, #206, #208, #210, #212, #214, #216, #218, #220, #222, #224, #226 and #228.
- **Decision 3** (spec 012): Accepted on 2026-11-09. Implemented by #180, #182, #184, #186 and #188; spec 012's quickstart items 1–7 were run against the live lab in #195. Two defects it found are in this decision's metrics, and neither changes the decision: #191 (a series' first observation is invisible to `rate()`) and #192 (the default buckets make the gNMI p95 a constant 480 ms). Both are fixed: #191 in the dashboard's queries (spec 012 data-model, `R(x)`), #192 by bucket overrides on the worker. Spec 015 extends it without changing it (see "Spec 015 additions" under Decision 3): #271, #273, #276, #278, #280, #282, #285, #288, #293, #295 and its PR G. Its counters and scan steps have not yet been run against the live lab (spec 015 tasks T037, T044, T049, T050).

## Date

2026-11-07

## Context

#101 found that drift and audit have no surface an operator can look at, and that snapl has no metrics at all. Spec 006 shipped the CLI half of #101's proposal. Spec 009 then made the loop run on its own every five minutes. The gap got worse: the loop now does work nobody watches.

#101 proposed Prometheus plus Grafana, and the port registry reserved 3000→18030 and 9090→18090 for them. A design session with the maintainer on 2026-11-07 revisited that and split the work into three specs:

- **010** — a local, read-only dashboard server with a typed JSON API and a plain page
- **011** — a designed UI on that API
- **012** — measured metrics

Each spec brings a technology the constitution's Technology Stack does not list. This ADR records all three together, because they only make sense as one design.

## Decision

### 1. FastAPI and uvicorn in `snapl-presentation` (spec 010)

The dashboard is a second Presentation surface next to the CLI, so the dependency goes in `snapl-presentation` only.
- **Why FastAPI**: the API's contract is the same Pydantic v2 models the rest of snapl uses. FastAPI validates and serialises them and generates an OpenAPI document from them. Spec 011 generates its front-end types from that document, so there is one source for the contract.
- **Bind and access**: it binds to loopback by default, has no authentication, and is read-only. Only GET routes exist, and the audit log is opened in SQLite read-only mode.

### 2. A Node build toolchain for the UI only (spec 011)

The UI uses React, Vite, TypeScript, Tailwind and Recharts.
- **Build-time only**: Node is needed to *build* it, never to run snapl. The built files are not committed. `invoke dashboard.build` produces them, and `build-artifacts.yml` builds them before `uv build`, so the wheel ships them.
- **CI**: gets one path-filtered front-end job.
- **Bounded**: the toolchain lives in one directory, `packages/presentation/ui/`, and touches no Python package's dependencies.

**Revised 2026-11-09** (#146, spec 011 research):
- **React Flow is dropped.** The first version of this decision named it for the topology. A fixed, tiered drawing of six devices uses none of what it brings (pan, zoom, drag, a minimap). The topology is a hand-written SVG component whose layout is a pure, tested function (R7). Revisit if a use case brings a fabric too large for one screen.
- **Versions** are the latest on npm on 2026-11-09, pinned exactly, with a committed lockfile (R1).
- **No fallback to "no UI"**: a checkout without Node still serves 010's plain page at `/`, and the plain page stays at `/plain` (R2).
- **Served through explicit GET routes**, not a static mount, so 010's read-only route test still sees every route and method (R3).
- **A Content-Security-Policy on every response** replaces 010's `textContent` rule as the guard against external requests and injected markup (R4).
- **The UI's types are generated from the OpenAPI snapshot** the Python tests already pin, and CI fails when they are stale (R5).

**As built** (2026-11-10):
- **TypeScript 5.9, not 7.** openapi-typescript 7.13 needs TypeScript 5 (#204).
- **No shadcn/ui components.** The panels needed none of them; Tailwind alone covers them (#204).

**Look** (2026-11-16, spec 014, #246): the page's spacing, type scale and panel frame are rules in the "Look" section of `dev/skills/dashboard/SKILL.md`, and `packages/presentation/ui/src/look.test.ts` fails on code that breaks them. Spec 014 restyled the page to them with no new dependency.
- **One more build output**: `static/ui/snapshot.html`, built with `vite-plugin-singlefile`, a whole page in one file for `--snapshot` (#228).

### 3. Prometheus, without Grafana (spec 012)

Prometheus runs in the Compose stack on 18090. It scrapes:
- the worker's built-in Temporal SDK metrics on 18091 (the worker runs on the host)
- gNMI timing, measured in the orchestrator activities, so `snapl-executor` and `snapl-collector` take no metrics dependency

**Grafana is dropped.** The dashboard queries Prometheus through its own API, and Prometheus's `/graph` page covers ad-hoc queries. Port 18030 moves to the dashboard.

**Retention is by size only** (`--storage.tsdb.retention.size`, with no time limit), and the data lives in a git-ignored host directory, not a Docker volume. Two reasons:
- A time limit deletes everything on the first start after a long break from the project.
- `demo.reset` removes Docker volumes, and a reset should restart the lab, not erase what was learned from it.

**Version and measured facts** (spec 012 research, checked on 2026-11-08 against temporalio 1.25.0 and Prometheus v3.10.0 on macOS with OrbStack):
- **Pinned to `prom/prometheus:v3.10.0`**, the version the dashboard's fixtures were captured from. A bump re-captures them (`dev/scripts/capture_prometheus_fixtures.py`).
- **One endpoint, no new dependency (R1).** An activity's histogram, recorded through `activity.metric_meter()`, appears on the same endpoint as the SDK's own metrics. The runtime adds `activity_type`, `namespace`, `task_queue` and `service_name` itself. So the worker serves everything from `Runtime(telemetry=TelemetryConfig(metrics=PrometheusConfig(...)))`, and no package takes `prometheus_client`.
- **Loopback is reachable from Compose on OrbStack (R2).** Prometheus in a container scraped a server bound to `127.0.0.1` on the host through `host.docker.internal`, so the worker binds `127.0.0.1:18091` and stays off the LAN.
- **A taken port fails at `Runtime()` construction (R3)**, with `ValueError: … Address already in use`, before any client connects. The worker catches it, logs a warning, and runs without metrics.
- **`timedelta` histograms are exported in milliseconds (R4)**. The three histograms the dashboard charts get their own bucket bounds (`HISTOGRAM_BUCKETS_MS` in `worker/metrics.py`), because the runtime's defaults made the gNMI p95 a constant 480 ms (#192); the rest keep the defaults. The gNMI metric is named `snapl_gnmi_call_duration`, with no unit suffix, and the unit is stated in its description and in the dashboard's views.

**Spec 015 additions** (`specs/015-more-metrics/contracts/metrics-views.md`). The same Prometheus, the same worker endpoint, and no new dependency:
- **Seven views over families already exported**: `workflow-mean`, `gnmi-mean`, `workflows-completed`, `activity-time`, `worker-slots`, `server-request-time` and `source-fetch-time`.
- **Three counters the worker now records**, through the same runtime as R1, and three views over them:
  - `snapl_scan_results_total`, labelled `device`, `use_case` and `state`. The state is one of `in_sync`, `missing`, `undesired`, `missing_and_undesired`, `error` or `unknown`.
  - `snapl_drift_paths_total`, labelled `device`, `use_case` and `kind` (`missing`, `undesired` or `unclassified`).
  - `snapl_deploy_ends_total`, labelled `device`, `use_case`, `outcome` and `reason`.

  The scan counters count a scheduled or operator scan's drift checks, not a deploy's verification. Every label value is bounded, with no paths or error text in labels (spec 012 SC-006).
- **A `count` unit**: a count's point covers its window, and points are never added up, because neighbouring windows can overlap.
- **Not from Prometheus**: four history series, the drift summary, and a scan's steps all come from the audit trail.
  - The steps a scan now records are reading intent for the use case and collecting each device. They sit behind the workflow patch `scan-drift-record-steps`, so the loop strip can show a scan's blocks.
- **The snapshot exports the ten new views at a coarser step**. That is 3 h for seven days, and each view's note says so. At the page's step, they would have almost tripled a seven-day snapshot (spec 015 research R8).

**Linux caveat (not measured).** On Linux Docker, `host.docker.internal` needs `extra_hosts: host-gateway` (the Compose service carries it) and reaches the bridge address, not loopback. A Linux host must start the worker with `WORKER_METRICS_BIND=0.0.0.0` (or the bridge IP), and the dashboard with a matching `DASHBOARD_HOST`, for Prometheus to scrape them. Nothing automates this.

### Diagram

```mermaid
flowchart LR
    subgraph host["host"]
        worker["worker<br/>(Temporal SDK metrics :18091)"]
        dash["snapl dashboard :18030<br/>FastAPI · read-only"]
        ui["browser<br/>011 UI at / · 010 page at /plain"]
        file["snapshot.html<br/>opened from disk, no server"]
        audit[("audit SQLite<br/>read-only")]
        runs[("./.snapl/test-runs")]
    end
    subgraph compose["Compose"]
        temporal["Temporal :18033"]
        infrahub["Infrahub :18000"]
        prom["Prometheus :18090<br/>(012)"]
    end
    gh["GitHub / Codecov"]

    ui --> dash
    dash -. "--snapshot writes" .-> file
    dash --> audit
    dash --> runs
    dash --> temporal
    dash --> infrahub
    dash --> gh
    dash -. 012 .-> prom
    prom -. 012 .-> worker
```

## Consequences

### Positive
- The loop's state, history and evidence are visible in one place. Every view states its source and age (ADR-0004).
- One contract, from Pydantic through OpenAPI to TypeScript, serves the page, the UI, snapshot export and metrics.
- The block boundaries hold. Executor and collector gain no dependency, and no ABC is widened.
- Long breaks from the project lose no history.

### Negative
- `snapl-presentation` gains two runtime dependencies.
- Contributors who change the UI need Node. CI gains a front-end job.
- Prometheus adds a Compose service and a host directory that grows until its size cap.
- With no Grafana, dashboards are code, not configuration. That is a deliberate trade for a single maintainer.
- The constitution's Technology Stack lists FastAPI and uvicorn (010) and Prometheus (012) since 2.1.0 (#195). Node is added when 011 lands, so the stack never claims something not yet in the code.
