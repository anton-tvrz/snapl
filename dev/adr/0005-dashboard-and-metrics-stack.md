# ADR-0005: Dashboard and Metrics Stack

## Status

- **Decision 1** (FastAPI and uvicorn, spec 010): Accepted on 2026-11-08, once spec 010's implementation PRs (#154, #160, #162, #164, #166, #168) had merged.
- **Decision 2** (spec 011): Proposed.
- **Decision 3** (spec 012): Accepted on 2026-11-09. Implemented by #180, #182, #184, #186 and #188; spec 012's quickstart items 1–7 were run against the live lab in #195. That run found four defects, none of which changes the decision: #191 (a series' first observation is invisible to `rate()`), #192 (the default buckets make the gNMI p95 a constant 480 ms), #194 and #196.

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

The UI uses React, Vite, TypeScript, Tailwind with shadcn/ui, Recharts and React Flow.
- **Build-time only**: Node is needed to *build* it, never to run snapl. The built files are not committed. `invoke dashboard.build` produces them, and `build-artifacts.yml` builds them before `uv build`, so the wheel ships them.
- **CI**: gets one path-filtered front-end job.
- **Bounded**: the toolchain lives in one directory and touches no Python package's dependencies.

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
- **`timedelta` histograms are exported in milliseconds (R4)**, with the runtime's default buckets. The gNMI metric is named `snapl_gnmi_call_duration`, with no unit suffix, and the unit is stated in its description and in the dashboard's views.

**Linux caveat (not measured).** On Linux Docker, `host.docker.internal` needs `extra_hosts: host-gateway` (the Compose service carries it) and reaches the bridge address, not loopback. A Linux host must start the worker with `WORKER_METRICS_BIND=0.0.0.0` (or the bridge IP), and the dashboard with a matching `DASHBOARD_HOST`, for Prometheus to scrape them. Nothing automates this.

### Diagram

```mermaid
flowchart LR
    subgraph host["host"]
        worker["worker<br/>(Temporal SDK metrics :18091)"]
        dash["snapl dashboard :18030<br/>FastAPI · read-only"]
        ui["browser<br/>010 page / 011 React UI"]
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
