# Prometheus HTTP API fixtures (spec 012)

These were captured on 2026-11-09 (task T043) from the real dev stack: **Prometheus v3.10.0** (`prom/prometheus:v3.10.0`, `--web.enable-admin-api`, 15 s scrape) scraping the snapl worker (temporalio runtime metrics on 18091) and `snapl dashboard` (18030), on `main` at 6baff39. Each file is `{"http_status": <int>, "body": <parsed JSON>}`; `ready.json` has `body_text` instead.

## What the hour before the capture held

- **Real gNMI calls** against the dcfabric lab (SR Linux 26.3.1): scheduled report-mode scans every 5 minutes, an `invoke test-e2e` run, and the spec 010 and 012 quickstart walks (deploys, scans, reconciles, one remediate-mode scheduled run).
  - `range_gnmi_duration_p95.json`: 9 series. All six devices have `collect`, and leaf-01, leaf-02 and spine-01 also have `configure`. Most points are `"NaN"`: `histogram_quantile` over a zero rate between calls. Nearly every finite value is 480 ms because of the default buckets (#192). The exceptions are 1634 ms on leaf-01 and spine-02 (the first scheduled scan after the worker started, at 20:10) and 9625 ms on leaf-04, where the paused device's failed dials (each scan took about 5.9 s) count towards the p95, since the view doesn't split by `outcome`.
  - `range_gnmi_errors.json`: one series, `leaf-04`, which was paused (`docker pause`) for four scans to produce real `outcome="error"` calls.
- **Workflow types** in `range_workflow_duration_p95.json`: `DeployIntent`, `DriftResponse`, `ReconcileDevices` and `ScanDrift`.
- **Source health** in `range_source_up.json`: six sources (`audit`, `ci`, `lab_runs`, `prometheus`, `sot`, `temporal`), all up for the hour.

The first capture (2026-11-08) was synthetic: a stand-in worker with randomised durations and `get`/`set` operations. Its shapes still hold. The counts the tests assert changed with this capture.

## Files

| file | request | shows |
| --- | --- | --- |
| `range_workflow_duration_p95.json` | `query_range`, `workflow-duration` query | matrix, one series per `workflow_type` |
| `range_gnmi_duration_p95.json` | `query_range`, `gnmi-duration` query | matrix by `device`, `operation` |
| `range_gnmi_errors.json` | `query_range`, `gnmi-errors` query | matrix by `device` |
| `range_activity_schedule_to_start_p95.json` | `query_range`, `worker-lag` query | matrix, one unlabelled series |
| `range_worker_up.json` | `query_range`, `worker-up` query | matrix, one unlabelled series (added in T043) |
| `range_source_up.json` | `query_range` `snapl_dashboard_source_up` | matrix by `source`, with temporal dropping to 0 |
| `range_absent_metric.json` | `query_range` of a metric that doesn't exist | `result: []` (success, no data) |
| `instant_up.json` | `query` `up` | vector |
| `error_bad_query.json` | malformed PromQL | 400 `bad_data` |
| `error_too_many_points.json` | `query_range` with step 1 since 1970 | 400, `exceeded maximum resolution of 11,000 points per timeseries` |
| `buildinfo.json` | `/api/v1/status/buildinfo` | version 3.10.0 |
| `ready.json` | `/-/ready` | 200 text |
| `admin_snapshot.json` | `POST /api/v1/admin/tsdb/snapshot` | `{"status":"success","data":{"name":…}}` |

## Re-capturing

`dev/scripts/capture_prometheus_fixtures.py` asks a running Prometheus everything above, using the dashboard's own view queries (`render_query`), and rewrites these files:

```bash
uv run invoke dev.deps                       # Prometheus on 18090; run the worker and snapl dashboard too
uv run python dev/scripts/capture_prometheus_fixtures.py   # --address, --out, --namespace, --use-case
```

The admin snapshot it requests stays in Prometheus's data directory (`.snapl/prometheus/snapshots/`). Delete it by hand. Task T043 re-captured these files on 2026-11-09. Re-run it after a change to the queries or the worker metrics, and update the tests if a shape changes.
