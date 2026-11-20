# Prometheus HTTP API fixtures (spec 012)

These were captured on 2026-11-09 (task T043) from the real dev stack: **Prometheus v3.10.0** (`prom/prometheus:v3.10.0`, `--web.enable-admin-api`, 15 s scrape) scraping the snapl worker (temporalio runtime metrics on 18091) and `snapl dashboard` (18030), on `main` at 6baff39. Each file is `{"http_status": <int>, "body": <parsed JSON>}`; `ready.json` has `body_text` instead.

## What the hour before the capture held

- **Real gNMI calls** against the dcfabric lab (SR Linux 26.3.1): scheduled report-mode scans every 5 minutes, an `invoke test-e2e` run, and the spec 010 and 012 quickstart walks (deploys, scans, reconciles, one remediate-mode scheduled run).
  - `range_gnmi_duration_p95.json`: 9 series. All six devices have `collect`, and leaf-01, leaf-02 and spine-01 also have `configure`. Most points are `"NaN"`: `histogram_quantile` over a zero rate between calls. Nearly every finite value is 480 ms because of the default buckets (#192). The exceptions are 1634 ms on leaf-01 and spine-02 (the first scheduled scan after the worker started, at 20:10) and 9625 ms on leaf-04, where the paused device's failed dials (each scan took about 5.9 s) count towards the p95, since the view doesn't split by `outcome`.
  - `range_gnmi_errors.json`: one series, `leaf-04`, which was paused (`docker pause`) for four scans to produce real `outcome="error"` calls.
- **Workflow types** in `range_workflow_duration_p95.json`: `DeployIntent`, `DriftResponse`, `ReconcileDevices` and `ScanDrift`.
- **Source health** in `range_source_up.json`: six sources (`audit`, `ci`, `lab_runs`, `prometheus`, `sot`, `temporal`), all up for the hour.

The first capture (2026-11-08) was synthetic: a stand-in worker with randomised durations and `get`/`set` operations. Its shapes still hold. The counts the tests assert changed with this capture.

## Spec 015's views (captured 2026-11-18, task T049)

The ten `range_<view>.json` files for spec 015 were captured from the same lab on main 2279d70, an hour after the worker restarted with the new counters (19:29 UTC). That hour held:

- scheduled report-mode scans every 5 minutes, and until 19:41 the integration tests' `query-leaf-01` and `query-spine-01` in intent, scanned as `error` (#307);
- one undesired interface on leaf-01 (`description` and `enabled`), scanned twice and repaired by an operator's reconcile, so `range_deploy_outcomes.json` holds one series, `succeeded`/`none`;
- spine-01's ethernet-1/1 disabled for two scans and repaired, so `range_drift_paths.json` holds `leaf-01`/`undesired` and `spine-01`/`missing`.

`test_each_spec_015_view_reads_its_live_capture` checks each file's unit and labels against `contracts/metrics-views.md`. The spec 012 files above were not re-captured: this hour had no gNMI errors, and they hold the paused leaf-04's.

## Spec 016's views (captured 2026-11-20, task T062)

The six `range_<view>.json` files for "The network" (`interface_traffic`, `interface_errors`, `interface_discards`, `bgp_established`, `device_cpu`, `device_memory`) were captured at 10:07 UTC on main c12053e, from the worker that had streamed the lab since 07:20. The hour before held:

- leaf-03 paused for 60 s at 09:56 (no session dropped: the pause is shorter than the BGP hold time);
- spine-01's neighbour leaf-01 disabled twice (09:58 and 10:01) and reconciled each time;
- leaf-01's `ethernet-1/49` disabled at 10:05:04 and reconciled at 10:06.

So `range_bgp_established.json` has leaf-01 between 1 and 2 sessions and spine-01 between 3 and 4, with the other four level, and `test_the_live_capture_shows_the_sessions_the_lab_lost` checks that. `test_each_spec_015_view_reads_its_live_capture`'s twin for spec 016 checks each file's unit and labels. Memory reads 92 to 94 % on every node: it is the OrbStack VM's, not a switch's. The other files were not re-captured.

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

`dev/scripts/capture_prometheus_fixtures.py` asks a running Prometheus everything above, using the dashboard's own view queries (`render_query`), and rewrites these files. It also asks spec 015's ten views and writes them as `range_<view>.json` (for example `range_workflow_mean.json`). Re-running it rewrites every file, so to refresh one set, restore the others with `git checkout`:

```bash
uv run invoke dev.deps                       # Prometheus on 18090; run the worker and snapl dashboard too
uv run python dev/scripts/capture_prometheus_fixtures.py   # --address, --out, --namespace, --use-case
```

The admin snapshot it requests stays in Prometheus's data directory (`.snapl/prometheus/snapshots/`). Delete it by hand. Task T043 re-captured these files on 2026-11-09. Re-run it after a change to the queries or the worker metrics, and update the tests if a shape changes.
