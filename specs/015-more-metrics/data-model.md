# Data Model: More Metrics

The served models are defined in [contracts/metrics-views.md](contracts/metrics-views.md): the new `MetricsViewName`, `MetricUnit` and `SeriesName` values, `DriftSummary`, `DeviceDriftSummary`, the three worker counters and the two audit step names. They live in `snapl_presentation/dashboard/models.py` and follow its rules (frozen, `extra="forbid"`, aware UTC datetimes).

This file adds what the contract does not say.

## Enums in the worker (`snapl_orchestrator/metrics.py`)

| enum | values | used as |
| --- | --- | --- |
| `ScanState` | `in_sync`, `missing`, `undesired`, `missing_and_undesired`, `error`, `unknown` | `state` on `snapl_scan_results_total` |
| `PathKind` | `missing`, `undesired`, `unclassified` | `kind` on `snapl_drift_paths_total` |
| `DeployOutcome` | `succeeded`, `failed` | `outcome` on `snapl_deploy_ends_total` |

`state` is what the scan found, named as the dashboard names a device's drift state (US3 scenario 1: one undesired path is state `undesired`). PR E corrected this from "the observability block's drift status value", whose three values (`clean`, `drifted`, `error`) cannot say `undesired`. `reason` is the workflow's `WorkflowReason` value, or `none` when the deploy succeeded. A value outside the enum is recorded as `unknown`, never as raw text.

## Rules for the audit-trail series

- **Devices in intent.** A scan's `in_sync_devices` counts only devices that are in intent at the time shown, by the rule `project_devices` uses.
- **One point per scan.** The point's time is the scan's end event. A scan with an error on a device counts that device as not in sync.
- **Open drifts.** The number of episodes with `opened_at <= t` and no `closed_at <= t`, on devices in intent.
- **Runs.** A run belongs to the bucket of its start. Its key is `<trigger>.<outcome>`, with outcome one of `succeeded`, `failed`, `running`. Unknown triggers use the trigger's raw value.
- **Repair duration.** One point per episode closed inside the range, at `closed_at`, with `duration_s`. The marker list carries what closed it.
- **Empty ranges.** No scan, no points. `first_event_at` still reports when the trail starts.

## UI view models (`packages/presentation/ui/src/lib/`)

### `MetricGroup` (`metrics.ts`)

| group | views |
| --- | --- |
| The loop's timing | `workflow-duration`, `workflow-mean`, `gnmi-duration`, `gnmi-mean`, `activity-time` |
| What the loop did | `workflows-completed`, `scan-results`, `drift-paths`, `deploy-outcomes`, `gnmi-errors` |
| The worker | `worker-up`, `worker-lag`, `worker-slots`, `server-request-time` |
| The dashboard | `source-health`, `source-fetch-time` |

A view a newer server adds is shown in a last group, "Other", by its raw name.

Settled in PR D2 (#286):
- **Titles** end with what the value is: `(95th percentile)`, `(mean)`, `(count)` or `(current value)`, and `(rate)` for `gnmi-errors`, which is none of those four. The up/down views (`worker-up`, `source-health`) have no suffix: up or down names itself.
- **"What the loop did"** holds `workflows-completed` and `gnmi-errors` until PR E adds its three views. The view list is a `satisfies Record<MetricsViewName, …>` map, so the types PR E regenerates fail the type check until its views have a group.
- **A count's lead** is the highest latest point, with the window from the server's note ("events in the last 60 s", only in an archive made before spec 017's #308), or else the step; "in the last 60 s" under two minutes, "in the last 6 min" above. A gauge count (`worker-slots`) has no window. A count's axis has whole ticks only.

### `NumberCard` (`numbers.ts`, spec 014)

The fourth card's id becomes `runs`: its value is the runs in the last 24 hours, and its context names the failures ("2 failed") or says none failed.
