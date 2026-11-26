# Research: Measured Metrics and Long-Lived History

Facts marked **measured** were checked on 2026-11-08 against temporalio 1.25.0 and Prometheus v3.10.0 on the maintainer's Mac (OrbStack). The scripts are summarised under each item. Everything else is a decision, with its reason.

## R1 — Custom metrics go through the SDK runtime, not `prometheus_client` (FR-001, FR-003, FR-006)

**Measured**: a worker built on `Runtime(telemetry=TelemetryConfig(metrics=PrometheusConfig(bind_address=...)))` ran a workflow on a `WorkflowEnvironment.start_local()` server. Its activity recorded a histogram through `activity.metric_meter().create_histogram_timedelta(...)`.
- The histogram appeared on the **same** endpoint as the SDK's own metrics.
- The runtime added `activity_type`, `namespace`, `task_queue` and `service_name="temporal-core-sdk"` labels on its own.
- The activity's labels (`device`, `operation`, `outcome`, `use_case`) came through unchanged.

**Decision**: gNMI timing uses `activity.metric_meter()`. The worker gains no metrics dependency, and neither do executor and collector (ADR-0005). The meter is a no-op when the runtime has no metrics configured, so tests and metrics-off workers need no special case.

**Rejected**: `prometheus_client` in the orchestrator. It needs a second port, and it duplicates the runtime's exporter.

## R2 — Binding and reachability (FR-001, Assumptions)

**Measured**: Prometheus in a container scraped a server on the host bound to **127.0.0.1** through `host.docker.internal` (a request came back from it). On OrbStack, a loopback bind is reachable from containers. So the worker binds `127.0.0.1:${WORKER_METRICS_PORT:-18091}` and stays off the LAN.

**Not measured**: Linux Docker. There, `host.docker.internal` needs `extra_hosts: ["host.docker.internal:host-gateway"]`, and it reaches the bridge address, not loopback. The Compose service carries that `extra_hosts` line (harmless on macOS). The docs say a Linux host must set `WORKER_METRICS_BIND=0.0.0.0` (or the bridge IP) itself. This spec doesn't automate it.

## R3 — A taken port fails at `Runtime()` (FR-002)

**Measured**: building a second `Runtime` with a `PrometheusConfig` on a port that is already bound raises `ValueError: Failed starting Prometheus exporter: Address already in use (os error 48)` **at construction**, before any client connects.

**Decision**: `build_runtime()` in `snapl_orchestrator/worker/metrics.py` catches that `ValueError`, logs a warning naming the port and `WORKER_METRICS_PORT`, and returns `None`. `run_worker` then connects without a custom runtime, which is today's behaviour. `WORKER_METRICS_PORT=off` disables metrics without trying.

## R4 — Units and names (FR-003, Assumptions)

**Measured**: a `timedelta` histogram is exported in **milliseconds**. The buckets are the runtime's defaults (`le="50"`, `100`, `500`, `1000`, `2500`, `10000`, …). In the capture, DeployIntent's end-to-end p95 read about 1,300, which is 1.3 s.

**Decision**:
- The metric is `snapl_gnmi_call_duration`, with milliseconds stated in its description and in the dashboard's view units.
- `operation` is `configure` (executor `apply`) or `collect` (collector `collect`). `outcome` is `ok` or `error`.
- `use_case` is the device's `use_case`.

**Rejected**: suffixing `_seconds` while exporting milliseconds. A name that lies about its unit is worse than one that names none.

## R5 — Where the timing is taken (FR-003–FR-005)

`ApplyResult` and `CollectResult` already carry a `duration_ms` measured inside executor and collector, but only when the call returns.

**Decision**: the activities `apply_config` and `collect_drift_state` time the call themselves with `time.monotonic()` in a `try/finally`:
- A raised exception records `outcome=error` and re-raises unchanged (FR-004).
- A returned result records `ok` or `error` from `result.success` (FR-005).

The activity's wall time is what the operator waits for, so it is the right number.

One helper, `snapl_orchestrator/metrics.py:record_gnmi_call(device, operation, started, outcome)`, is the only place that names the metric and its labels. That keeps SC-006 enforceable by one test.

## R6 — Prometheus service (FR-007–FR-009)

- **Image**: `prom/prometheus:v3.10.0`, the version the fixtures were captured from. It is pinned like SR Linux (#125). A bump re-captures the fixtures.
- **Flags**:
  - `--storage.tsdb.retention.size=${PROMETHEUS_RETENTION_SIZE:-2GB}`, and no `retention.time`. Prometheus 3 has no time limit when only size is set.
  - `--web.enable-admin-api`, needed for snapshots (R9). The port is published on `127.0.0.1` only, so the admin API isn't reachable from the LAN.
- **Data**: bind mount `../.snapl/prometheus:/prometheus`. The container runs as `nobody`.
  - On macOS with OrbStack, the measured capture wrote to a host directory with mode 777. `dev.deps` creates `.snapl/prometheus` with mode 0777 before `compose up`.
  - On Linux, files are then owned by uid 65534. `history.purge` deletes through a throwaway container when a plain delete gets a permission error.
- **Scrape config**: `development/prometheus/prometheus.yml`, committed, 15 s interval.
  - Jobs: `snapl-worker` → `host.docker.internal:18091`, `snapl-dashboard` → `host.docker.internal:18030`.
  - A target that isn't running shows as `up == 0`. That isn't an error; it is what US3 scenario 3 reads.
- **Reset safety**: `dev.down --volumes` removes named volumes only, and a bind mount is not a volume. A unit test pins that `demo.reset`'s commands never name `.snapl`.

## R7 — Dashboard metrics API (FR-011–FR-013, FR-016)

- **Client**: `PrometheusSource` in `snapl_presentation/dashboard/prometheus.py`, using httpx (already a dependency). Its `refresh()` probes `GET /-/ready` for `/sources`, which is 010's protocol unchanged. The metrics routes use a separate `query()` method.
  - It is fetched through 010's `_Reader`: the 1.5 s request budget, and a stale last-good answer.
  - The cache is keyed by `(view, start, end, step)`. Entries live 15 s, the scrape interval, so polling never queries faster than data arrives.
- **Named views only**: a dict from view name to a PromQL template is the only place queries live.
  - Range parameters are validated by pydantic (aware datetimes, `start < end`).
  - The step is a positive integer of seconds.
  - Unknown names are 404 through the route's path enum.
- **Points limit**: Prometheus refuses ranges with more than 11,000 points per series (captured: HTTP 400, `exceeded maximum resolution of 11,000 points per timeseries`). The server picks `step = max(requested, ceil((end − start) / 10,000 s))` before asking, and reports the step it used.
- **Errors**: Prometheus's `{"status":"error","errorType":…,"error":…}` (captured: 400 `bad_data`) becomes an unavailable view with that error as the reason. Connection refused becomes an unavailable view with the `dev.deps` remedy.
- **Values**: Prometheus returns sample values as strings, including `"NaN"`, `"+Inf"` and `"-Inf"`. They are parsed to float, and non-finite values become `null`. JSON has no NaN, and the page shows a gap.
- **Source**:
  - `SourceName` gains `prometheus`.
  - 010's contract lists which enums consumers must tolerate unknown values of, and `SourceName` isn't one of them. Its only consumer today is 010's own page, which renders the raw string.
  - This spec amends 010's contract to add `SourceName` to that list, a compatible change made before spec 011 consumes it. It is recorded in 010's contract with a date.

## R8 — Dashboard `/metrics` (FR-014)

**Decision**: the dashboard renders the Prometheus text format itself. The output is two gauge families with one label each:
- `snapl_dashboard_source_up{source}`, 1 or 0;
- `snapl_dashboard_source_fetch_duration_milliseconds{source}`.

The values come from `_Reader`'s last outcome per source, so they cost no extra fetch.

**Rejected**: `prometheus_client`. It would add a dependency for about 20 lines of text, and label values here come from a fixed enum, so there is nothing to escape.

**Route**: `GET /metrics`, outside `/api/v1` because that is where scrapers look. It is excluded from the OpenAPI document. GET-only like every route (010 R2).

## R9 — Archive (FR-017–FR-019)

- **Audit DB**: copied with SQLite's online backup API (`sqlite3.Connection.backup`) from a read-only source connection. That gives a consistent copy while the worker writes, which a file copy doesn't.
- **Metrics snapshot**: `POST /api/v1/admin/tsdb/snapshot`, captured as `{"status":"success","data":{"name":"20261108T203115Z-674e642f920efff4"}}`.
  - The snapshot appears under `.snapl/prometheus/snapshots/<name>/`, and the archive adds that directory.
  - The snapshot is then deleted from the live data directory, so archives don't grow the live store.
- **Dashboard export**: GET each of overview, history, links, sources and every metrics view for the last 7 days from the running dashboard (`DASHBOARD_PORT`), saved as JSON files under `export/`. If the dashboard isn't running, the export is marked missing (FR-018).
- **Manifest**: `manifest.json`. Its fields are listed in data-model.md.
- **Writing**: the tarball is written to `<name>.partial` and renamed when complete, so a crash never leaves a file that looks finished.

## R10 — Purge (FR-010)

`invoke history.purge`:
- **Store running:** if the metrics store answers on `PROMETHEUS_PORT`, it refuses and says `uv run invoke dev.stop`.
- **Confirmation:** otherwise it lists what it will delete (size and path) and asks for `purge` to be typed. `--yes` skips the prompt. Without a TTY and without `--yes`, it refuses.
- **What it deletes:** only `.snapl/prometheus/`. The audit DB and the test runs are not metrics, and 012 doesn't own them.

## R11 — Fixtures (testing)

All dashboard-side tests read real responses captured on 2026-11-08 from Prometheus v3.10.0. Prometheus scraped a real Temporal SDK runtime endpoint under load:
- The load came from workflows named `DeployIntent` and `ScanDrift` on a dev server, with activities recording `snapl_gnmi_call_duration`.
- The `source_up` series came from a stand-in endpoint that reported Temporal down for 20 s.
- The **durations are synthetic** (randomised sleeps); the response **shapes are real**.

The files are in `tests/unit/test_presentation/fixtures/prometheus/`, each holding `{http_status, body}`. The README lists how they were made and what each one shows. A lab task re-captures them against the real stack and the real worker.
