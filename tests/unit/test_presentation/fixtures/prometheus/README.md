# Prometheus HTTP API fixtures (spec 012)

These were captured on 2026-11-08 from a real **Prometheus v3.10.0** (`prom/prometheus:v3.10.0`, started with `--web.enable-admin-api`). Each file is `{"http_status": <int>, "body": <parsed JSON>}`; `ready.json` has `body_text` instead.

## What was scraped (2 s interval)

- **`snapl-worker`**: a real Temporal SDK runtime metrics endpoint (temporalio 1.25.0, `PrometheusConfig`), with a worker on a `WorkflowEnvironment.start_local()` dev server running workflows named `DeployIntent` and `ScanDrift` for 75 s.
  - Activities recorded `snapl_gnmi_call_duration` through `activity.metric_meter()` for `spine-01`, `leaf-01` and `leaf-02`, with about 8 % `outcome="error"`.
  - **Durations are synthetic** (randomised sleeps).
  - **`operation` is `get`/`set` here.** The spec later fixed the values as `collect`/`configure`. The shapes are what the tests rely on. Quickstart item 7 re-captures against the real worker.
- **`snapl-dashboard`**: a stand-in serving `snapl_dashboard_source_up{source}` for the five 010 sources, with `temporal` at 0 from +20 s to +40 s.

## Files

| file | request | shows |
| --- | --- | --- |
| `range_workflow_duration_p95.json` | `query_range`, `workflow-duration` query | matrix, one series per `workflow_type` |
| `range_gnmi_duration_p95.json` | `query_range`, `gnmi-duration` query | matrix by `device`, `operation` |
| `range_gnmi_errors.json` | `query_range`, `gnmi-errors` query | matrix by `device` |
| `range_activity_schedule_to_start_p95.json` | `query_range`, `worker-lag` query | matrix, one unlabelled series |
| `range_source_up.json` | `query_range` `snapl_dashboard_source_up` | matrix by `source`, with temporal dropping to 0 |
| `range_absent_metric.json` | `query_range` of a metric that doesn't exist | `result: []` (success, no data) |
| `instant_up.json` | `query` `up` | vector |
| `error_bad_query.json` | malformed PromQL | 400 `bad_data` |
| `error_too_many_points.json` | `query_range` with step 1 since 1970 | 400, `exceeded maximum resolution of 11,000 points per timeseries` |
| `buildinfo.json` | `/api/v1/status/buildinfo` | version 3.10.0 |
| `ready.json` | `/-/ready` | 200 text |
| `admin_snapshot.json` | `POST /api/v1/admin/tsdb/snapshot` | `{"status":"success","data":{"name":…}}` |

The capture scripts aren't committed yet: spec 012's research.md R11 records what they did. Task T036 commits a re-runnable `dev/scripts/capture_prometheus_fixtures.py`, and T043 re-captures these files from the real stack.
