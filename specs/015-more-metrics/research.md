# Research: More Metrics

Each entry gives the decision, why, and what was set aside.

## R1. What is exported and not drawn (measured 2026-11-15)

Read from the worker's endpoint (`:18091/metrics`) on the live lab, `main` at 872d143. The worker exports 23 families:

`snapl_gnmi_call_duration`, and from the SDK `temporal_activity_execution_latency`, `temporal_activity_poll_no_task`, `temporal_activity_schedule_to_start_latency`, `temporal_activity_succeed_endtoend_latency`, `temporal_activity_task_received`, `temporal_long_request`, `temporal_long_request_latency`, `temporal_num_pollers`, `temporal_request`, `temporal_request_latency`, `temporal_sticky_cache_hit`, `temporal_sticky_cache_size`, `temporal_worker_start`, `temporal_worker_task_slots_available`, `temporal_worker_task_slots_used`, `temporal_workflow_completed`, `temporal_workflow_endtoend_latency`, `temporal_workflow_task_execution_latency`, `temporal_workflow_task_queue_poll_empty`, `temporal_workflow_task_queue_poll_succeed`, `temporal_workflow_task_replay_latency`, `temporal_workflow_task_schedule_to_start_latency`.

The six views of spec 012 read three of them, plus `up` and `snapl_dashboard_source_up`. The dashboard also exports `snapl_dashboard_source_fetch_duration_milliseconds`, which no view reads.

Failure families appear only after a first failure. The only one present was `temporal_long_request_failure`.

## R2. Label sets of the families this feature reads (measured 2026-11-15)

From the metrics store's series API, leaving out `job`, `instance`, `namespace`, `service_name` and `task_queue`:

| family | series | labels |
| --- | --- | --- |
| `temporal_workflow_completed` | 4 | `workflow_type`: DeployIntent, DriftResponse, ReconcileDevices, ScanDrift |
| `temporal_activity_execution_latency` | 12 | `activity_type`: apply_config, collect_drift_state, detect_drift, fetch_desired_state, fetch_devices_for_use_case, record_audit_event; `workflow_type` |
| `temporal_worker_task_slots_used` | 3 | `worker_type`: ActivityWorker, LocalActivityWorker, WorkflowWorker |
| `temporal_request_latency` | 5 | `operation` |
| `snapl_dashboard_source_fetch_duration_milliseconds` | 6 | `source` |

**Decision**: `activity-time` sums over `workflow_type` and keeps `activity_type`: six series, which is the palette's limit. `server-request-time` sums over `operation`, because the operations are the SDK's own and grow with its version.

## R3. Means beside percentiles

**Decision**: add `workflow-mean` and `gnmi-mean` as `rate(_sum) / rate(_count)`.

**Why**: measured on the lab over one hour, the drawn 95th percentiles were 480 ms (gNMI), 95 ms (worker wait) and 1.48 s (ScanDrift), against means of 212 to 275 ms, 41 ms and 1.58 s. A percentile below the mean shows it is an interpolation inside a bucket. PR #241 narrows the buckets; the mean is exact with any buckets and needs no worker change.

**Set aside**: replacing the percentile views (they are in the v1 contract and become useful once #241 lands).

## R4. Counts per step, and the `count` unit

**Decision**: event views use `increase(...[$w])` and the unit `count`.

**Why**: on a six-device lab a rate reads `0.024/s`, which nobody can use. `increase` over the window answers "how many". Prometheus extrapolates `increase`, so a value can be fractional; the page rounds for display and the table keeps the value.

**To check in the slice**: the window `$w` is at least four scrapes and at least the step. When `$w` is longer than the step, neighbouring points overlap and their sum overstates the total. The view's note must say "events in the last `$w`" when that is so, and the panel must not print a sum across points.

## R5. Where the audit-trail figures come from

**Decision**: extend `project_series` with the four series and add `project_drift_summary`, both over the events and the drift episodes `project_timeline` already builds.

| figure | from |
| --- | --- |
| `in_sync_devices`, `open_drifts` | the timeline's scan points and episodes, one value per scan end |
| `runs` | `_split_runs` with `_trigger` and `_state`, bucketed by start time |
| `repair_duration` | closed episodes: `duration_s` at `closed_at` |
| drift summary | episodes grouped by device, clipped to the range |

**Why**: one definition of an episode and of a run's trigger. The page, `snapl status` and these figures cannot disagree.

**Performance**: the projections run 4 to 8 times slower under coverage in CI. Budget by traced lines and read the measured value before changing a test's limit.

## R6. Counters through the SDK's meter

**Decision**: record the scan and path counters in the `detect_drift` activity with `activity.metric_meter().create_counter(...)`, and the deploy counter in the `DeployIntent` workflow with `workflow.metric_meter()`, which is replay-safe. All three live in `snapl_orchestrator/metrics.py` beside `record_gnmi_call`, with the same never-raise rule.

**Why**: spec 012 research R1 chose the SDK's meter so the worker has one endpoint and no new dependency. That still holds.

**To check in the slice**: read the installed `temporalio`'s `MetricMeter.create_counter` signature and whether the exported name gains a `_total` suffix, before fixing the names in the contract. Test against `WorkflowEnvironment.start_local()` with a metrics runtime on a free port, as spec 012's tests do.

**Checked in PR E** (temporalio 1.25.0): `MetricMeter.create_counter(name, description=None, unit=None)` returns a `MetricCounter` whose `add(value: int, additional_attributes=None)` takes a non-negative int. The runtime's Prometheus exporter writes the name exactly as given: a counter created as `snapl_probe_a` is served as `snapl_probe_a`, one created as `snapl_probe_b_total` as `snapl_probe_b_total`, and the unit is not appended. The SDK's own counters follow the same rule (`temporal_workflow_completed` has no suffix). So the worker creates the three counters under their full contract names, `_total` included, and the contract's family names stand.

## R7. Scan steps without changing any figure

**Decision**: `ScanDrift` writes `activity_completed` or `activity_failed` events for `fetch_devices_for_use_case` (target: the use case) and `collect_drift_state` (target: the device), next to the `detect_drift` event it writes today.

**Why**: `project_steps` maps activity names to blocks through `STEP_BLOCKS`, so the strip needs only the events and two map entries. Every projection that reads scans filters on `activity_name == "detect_drift"` (`_is_scan_record`), so the new events are invisible to them. FR-019 is checked by running the existing projection tests on a trail with the new events added.

**Cost**: one more audit write per device per scan. Audit writes are serial today (#139), so a scan of six devices does seven more. Measure the scan's duration before and after in the slice's lab task.

**Set aside**: inferring Intent and Collector as done from a `detect_drift` event (the trail would be saying something it did not record).

## R8. Snapshot and archive size

**Decision**: export every view, and measure. The snapshot was 2.8 MB for seven days with six views (#235). Ten more views at the same step would more than double the metrics part.

**To decide in the slice, by measuring**: if SC-006's limit is passed, the snapshot exports the new views at a coarser step (at most 120 points per series) and says so in each view's note.

## R9. PR #241

PR #241 (draft, the maintainer's) rewrites `worker/metrics.py` (bucket overrides), `dashboard/prometheus.py` (a rate that counts a first observation), adds `workflows/audit.py` (one `record_event` helper) and changes `scan_drift.py`.

**Decision**: slices that touch those files are marked **After #241** and are not started until it has merged. The slices before them touch `models.py`, `projections.py`, `app.py` and the UI, which #241 does not.

**Why**: building the counters and the scan steps on the old audit code would be redone the day #241 merges.
