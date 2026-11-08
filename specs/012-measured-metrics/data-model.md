# Data Model: Measured Metrics and Long-Lived History

New pydantic models live in `snapl_presentation/dashboard/models.py`, next to 010's, and follow its rules (frozen, `extra="forbid"`, aware UTC datetimes). They are part of the v1 contract and appear in the OpenAPI snapshot.

## Recorded in the worker

### `snapl_gnmi_call_duration` (histogram, milliseconds)

| label | values | set by |
| --- | --- | --- |
| `device` | device name from the SoT | `record_gnmi_call` |
| `use_case` | the device's `use_case` | `record_gnmi_call` |
| `operation` | `configure` \| `collect` | `record_gnmi_call` |
| `outcome` | `ok` \| `error` | `record_gnmi_call` |
| `activity_type`, `namespace`, `task_queue`, `service_name` | added by the Temporal runtime | runtime (R1) |

No other label is allowed (SC-006). `GnmiOperation` and `CallOutcome` are `StrEnum`s in `snapl_orchestrator/metrics.py`.

### Used from the Temporal SDK (not defined by snapl)

- `temporal_workflow_endtoend_latency` (histogram, ms, by `workflow_type`)
- `temporal_activity_schedule_to_start_latency` (histogram, ms)
- `temporal_workflow_task_schedule_to_start_latency` (histogram, ms)
- `temporal_num_pollers` (gauge, by `poller_type`)

## Exported by the dashboard (`GET /metrics`)

| family | type | labels | value |
| --- | --- | --- | --- |
| `snapl_dashboard_source_up` | gauge | `source` (a `SourceName`) | 1 if the last fetch answered, 0 otherwise |
| `snapl_dashboard_source_fetch_duration_milliseconds` | gauge | `source` | the last fetch's duration |

A source that hasn't been fetched yet is omitted, not reported as 0.

## Dashboard metrics API

### `SourceName` (amended)

010's values, plus `prometheus`.

### `MetricsViewName` (StrEnum)

`workflow-duration`, `gnmi-duration`, `gnmi-errors`, `worker-lag`, `worker-up`, `source-health`

| view | query (template; `[$w]` is the rate window) | unit | series labels |
| --- | --- | --- | --- |
| `workflow-duration` | `histogram_quantile(0.95, sum by (le, workflow_type) (rate(temporal_workflow_endtoend_latency_bucket{namespace="$ns"}[$w])))` | ms | `workflow_type` |
| `gnmi-duration` | `histogram_quantile(0.95, sum by (le, device, operation) (rate(snapl_gnmi_call_duration_bucket{use_case="$uc"}[$w])))` | ms | `device`, `operation` |
| `gnmi-errors` | `sum by (device) (rate(snapl_gnmi_call_duration_count{use_case="$uc",outcome="error"}[$w]))` | calls/s | `device` |
| `worker-lag` | `histogram_quantile(0.95, sum by (le) (rate(temporal_activity_schedule_to_start_latency_bucket{namespace="$ns"}[$w])))` | ms | — |
| `worker-up` | `up{job="snapl-worker"}` | 0/1 | — |
| `source-health` | `snapl_dashboard_source_up` | 0/1 | `source` |

- `$ns` is the dashboard's namespace and `$uc` its use case, both from settings and never from the request.
- `$w` is `max(4 × scrape interval, step)`, so a rate always spans at least four scrapes.

### `MetricPoint`

| field | type |
| --- | --- |
| `t` | `datetime` |
| `value` | `float \| None` (`None` for NaN or ±Inf, R7) |

### `MetricSeries`

| field | type |
| --- | --- |
| `labels` | `dict[str, str]` (only the view's series labels; `__name__`, `job` and `instance` are dropped) |
| `points` | `list[MetricPoint]` |

### `MetricsData`

| field | type |
| --- | --- |
| `view` | `MetricsViewName` |
| `unit` | `Literal["ms", "per_second", "up"]` |
| `start`, `end` | `datetime` |
| `step` | `int` (seconds, as used: may be coarser than requested, FR-013) |
| `series` | `list[MetricSeries]` (empty when there is no data: a note, not an error) |
| `note` | `str \| None` (for example "no data in range", "step raised to 60 s to stay under the point limit") |

### `MetricsResponse`

`Envelope + { metrics: View[MetricsData] }`. The view's `source` is `prometheus`.

## Archive manifest (`manifest.json`)

| field | type |
| --- | --- |
| `format` | `"snapl-history/1"` |
| `created_at` | ISO 8601 UTC |
| `snapl_commit` | `str \| null` (`git rev-parse HEAD` of the checkout) |
| `use_case` | `str` |
| `parts` | `list[ArchivePart]` |

### `ArchivePart`

| field | type |
| --- | --- |
| `name` | `"audit_db"` \| `"metrics_snapshot"` \| `"test_runs"` \| `"dashboard_export"` |
| `path` | path inside the archive |
| `present` | `bool` |
| `reason` | `str \| null` (set when `present` is false) |
| `source` | where it came from (file path or URL) |
| `bytes` | `int \| null` |

The manifest is plain JSON, written by the invoke task with `json.dumps`. It is not a pydantic model in the package, because the archive is produced by a task, not served by the API. Spec 011's archive viewer will define the reading model.
