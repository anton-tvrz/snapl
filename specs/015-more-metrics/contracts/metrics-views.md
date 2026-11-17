# Contract: More Metrics (additions to v1)

**Feature**: 015-more-metrics | **Extends**: [012's metrics API](../../012-measured-metrics/contracts/metrics-api.md) and [010's dashboard API](../../010-dashboard-read-api/contracts/dashboard-api.md)

Every addition is **compatible** under 010's versioning rules: new enum values, a new route and new models. Consumers already render unknown enum values as raw strings.

## `MetricsViewName` (new values)

`$ns`, `$uc` and `$w` are as in spec 012. `rate()` below means spec 012's rate, or PR #241's first-observation form once it has merged. `increase()` over `$w` is not scaled: a point is the events in the window ending at it, so when `$w` is longer than the step, neighbouring points overlap (research R4).

| view | query (template) | unit | series labels | reads |
| --- | --- | --- | --- | --- |
| `workflow-mean` | `sum by (workflow_type) (rate(temporal_workflow_endtoend_latency_sum{namespace="$ns"}[$w])) / sum by (workflow_type) (rate(temporal_workflow_endtoend_latency_count{namespace="$ns"}[$w]))` | ms | `workflow_type` | SDK |
| `gnmi-mean` | `sum by (device, operation) (rate(snapl_gnmi_call_duration_sum{use_case="$uc"}[$w])) / sum by (device, operation) (rate(snapl_gnmi_call_duration_count{use_case="$uc"}[$w]))` | ms | `device`, `operation` | snapl |
| `workflows-completed` | `sum by (workflow_type) (increase(temporal_workflow_completed{namespace="$ns"}[$w]))` | count | `workflow_type` | SDK |
| `activity-time` | `sum by (activity_type) (rate(temporal_activity_execution_latency_sum{namespace="$ns"}[$w])) / sum by (activity_type) (rate(temporal_activity_execution_latency_count{namespace="$ns"}[$w]))` | ms | `activity_type` | SDK |
| `worker-slots` | `sum by (worker_type) (temporal_worker_task_slots_used{namespace="$ns"})` | count | `worker_type` | SDK |
| `server-request-time` | `sum(rate(temporal_request_latency_sum{namespace="$ns"}[$w])) / sum(rate(temporal_request_latency_count{namespace="$ns"}[$w]))` | ms | — | SDK |
| `source-fetch-time` | `snapl_dashboard_source_fetch_duration_milliseconds` | ms | `source` | dashboard |
| `scan-results` | `sum by (device, state) (increase(snapl_scan_results_total{use_case="$uc"}[$w]))` | count | `device`, `state` | snapl, new |
| `drift-paths` | `sum by (device, kind) (increase(snapl_drift_paths_total{use_case="$uc"}[$w]))` | count | `device`, `kind` | snapl, new |
| `deploy-outcomes` | `sum by (outcome, reason) (increase(snapl_deploy_ends_total{use_case="$uc"}[$w]))` | count | `outcome`, `reason` | snapl, new |

The label sets of the SDK families were read from the live metrics store on 2026-11-15 (research R2).

Settled in PR D (#284):
- **Rates are #241's first-observation form**, in the means as in the percentiles. `increase()` is that rate times the window (`increase` is `rate` times the range in Prometheus), so a first workflow completion after a worker restart counts as one instead of showing nothing (#191).
- **The window note.** `$w` is `max(60 s, step)`. When it is longer than the step, a view that counts events (`workflows-completed`, and PR E's three) says `events in the last 60 s` in its `note`, after any step note and before "no data in range". When `$w` is the step, a point is the events in its own step and there is no window note. `worker-slots` is a `count` but a gauge, so it has no window note.
- **Checked live** on 2026-11-17: each of the seven queries answered `success` from the lab's Prometheus, with the series labels above. `source-fetch-time` also carries `__name__`, `job` and `instance`, which the parser drops, as for `source-health`.

## `MetricUnit` (new value)

`count`: a number of events in one step, or a current count for a gauge. Consumers that don't know it show the raw unit.

## `SeriesName` (new values)

| series | points | value | `by_outcome` |
| --- | --- | --- | --- |
| `in_sync_devices` | one per scan | devices in intent that the scan found in sync | — |
| `open_drifts` | one per scan | drift episodes open after the scan, on devices in intent | — |
| `runs` | one per bucket | runs that started in the bucket | keys `<trigger>.<outcome>`, for example `schedule.succeeded`, `operator.failed` |
| `repair_duration` | one per repaired drift, at its `closed_at` | seconds, scan to scan | — |

`GET /api/v1/history` keeps its default of the original four series, so existing callers get the same answer. The new series are returned when named with `series=`.

Settled in PR A (#270):
- **`runs` outcomes.** The history reads no Temporal, so `succeeded` is a run that recorded success, `running` is a run with no recorded end, and `failed` is everything else: a failure, a cancellation, and a run that a later start under its id shows never finished. A bucket where no run started has `value: null` and `by_outcome: {}`. `bucket` is the requested bucket.
- **`bucket` is `null`** for `in_sync_devices`, `open_drifts` and `repair_duration`.
- **Devices in intent** for `in_sync_devices` and `open_drifts` follow `project_devices`: the SoT's devices, or with the SoT down the devices it last named. A server that has never read the SoT counts every device the trail mentions. `/history` reads the SoT only when one of these two is asked for.
- **`repair_duration` is not limited to devices in intent**, like the timeline's episodes: a device that has left intent keeps the repairs it had.

## `GET /api/v1/drift-summary`

| query | default | |
| --- | --- | --- |
| `start` | `end − 24h` | aware datetime |
| `end` | now | aware datetime |
| `use_case` | the server's | `422` if it isn't the server's |

- **Responses:** `422` when `start >= end` or the range exceeds the drift timeline's limit.
- **Response body:** `DriftSummaryResponse`: `Envelope + { summary: View[DriftSummary] }`, source `audit`.

### `DriftSummary`

| field | type |
| --- | --- |
| `start`, `end` | `datetime` |
| `devices` | `list[DeviceDriftSummary]`, most episodes first, then longest drifted, then name |

### `DeviceDriftSummary`

| field | type | |
| --- | --- | --- |
| `device_id` | `UUID` | |
| `name` | `str` | |
| `in_intent` | `bool` | |
| `episodes` | `int` | drift episodes that overlap the range |
| `open` | `int` | of those, still open at `end` |
| `drifted_s` | `int` | total time drifted inside the range, scan to scan |
| `most_missing` | `int` | the most missing paths any scan in the range saw |
| `most_undesired` | `int \| None` | `None` when the scans predate the undesired split (010 FR-012a) |

A device with no episode in the range is left out.

Settled in PR B (#272):
- **`drifted_s` for an episode still open at `end`** runs to its latest scan, not to `end`: scan to scan, like every duration from the trail. An episode seen by one scan has `drifted_s: 0` and `open: 1`.
- **Scans before the undesired split** (010 FR-012a) make `most_undesired` `None` and add nothing to `most_missing`, since their paths have no direction.
- **`in_intent`** follows `project_timeline`: the SoT's devices, or with the SoT down the devices it last named.
- **Archives**: the summary and the series of PR A come from the archived audit trail, so an archive made before spec 015 answers them too. "Not in this archive" applies to the metrics views of PR D, which come from the exported Prometheus data.

## Worker metrics (new families)

| family | type | labels |
| --- | --- | --- |
| `snapl_scan_results_total` | counter | `device`, `use_case`, `state` (a drift state) |
| `snapl_drift_paths_total` | counter | `device`, `use_case`, `kind` (`missing` \| `undesired` \| `unclassified`) |
| `snapl_deploy_ends_total` | counter | `device`, `use_case`, `outcome` (`succeeded` \| `failed`), `reason` (a workflow reason, or `none`) |

The Temporal runtime adds `namespace`, `task_queue`, `service_name` and, inside an activity, `activity_type`. No other label is allowed.

## Audit trail (new step names)

Existing event types (`activity_completed`, `activity_failed`) with these `activity_name` values, written by `ScanDrift`:

| `activity_name` | target | block |
| --- | --- | --- |
| `fetch_devices_for_use_case` | the use case | Intent |
| `collect_drift_state` | the device | Collector |
| `detect_drift` (exists) | the device | Observability |

Projections that select scan records keep selecting `detect_drift` only.

## Archive and snapshot

`history.archive` and `--snapshot` export every `MetricsViewName`, the new series and the drift summary. An archive made before this feature answers the new views and the summary as "not in this archive".
