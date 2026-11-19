# Live Evidence: More Metrics

Recorded on 2026-11-18 on the dcfabric lab (SR Linux 26.3.1, six devices), with the worker and `snapl dashboard` started from main at 2279d70, Prometheus v3.10.0 scraping both. Quickstart items 5 to 8; tasks T037, T044, T049 and T050 (#304).

The lab had run on a worker from 872d143 since 2026-11-15. That worker was stopped at 19:29 UTC and the new one started, so every counter below starts at 19:29. Until 19:41, intent also held the integration tests' two `query-*` devices (#307). They had no dial target, so every scan counted them as `error`.

## Item 5: the counters (T037)

After the first scans, `curl -s localhost:18091/metrics | grep '^snapl_'` showed `snapl_scan_results_total` with one series per device. The labels were `device`, `use_case` and `state`, plus the runtime's `namespace`, `task_queue`, `service_name` and `activity_type`, which is exactly the contract's set. The states were `in_sync` for the six fabric devices and `error` for the two `query-*` devices. `snapl_drift_paths_total` and `snapl_deploy_ends_total` appear only once something happens, a differing path or a deploy's end, as the contract says.

## Item 6: a drift, counted (T037)

Right after the 19:35 scheduled scan, at 19:35:04 to 19:35:12:

1. `docker exec clab-dcfabric-leaf-01 sr_cli -ec 'set / interface ethernet-1/7 description snapl-t037'`
2. `snapl scan`: leaf-01 drifted, with two paths not in intent, `description` and `enabled`. Setting a description on an unconfigured port creates the interface.
3. `snapl reconcile --drifted --yes`: 1 of 1 succeeded.
4. `sr_cli -ec 'delete / interface ethernet-1/7'`. Reconcile doesn't remove undesired config (#65).
5. `snapl scan`: 6 clean.

The counters moved by:

| counter | labels | change |
|---|---|---:|
| `snapl_scan_results_total` | `device="leaf-01"`, `state="undesired"` | +2 |
| `snapl_drift_paths_total` | `device="leaf-01"`, `kind="undesired"` | +4 |
| `snapl_deploy_ends_total` | `device="leaf-01"`, `outcome="succeeded"`, `reason="none"`, `workflow_type="DeployIntent"` | +1 |

Leaf-01 is counted `undesired` twice, not once, because `reconcile --drifted` runs its own scan to find the drifted devices. Each of the two scans found two paths, giving 4. The quickstart's item 6 now says so.

The dashboard's `scan-results`, `drift-paths` and `deploy-outcomes` views read the same values from Prometheus: 2, 4 and 1 at their peaks. Their points come from `increase()` over 60 s at a 15 s step, so they are extrapolated, such as 3.9999 for 3 scans, and one event shows in four points in a row (#308).

## Item 7: scan steps (T044)

A scheduled scan now records its steps. The 19:45 run wrote `fetch_devices_for_use_case`, one `collect_drift_state` per device and one `detect_drift` per device, all `success`, between its start and end events. That's 9 more events per scan with eight devices: 19 instead of 10.

The loop strip shows them. For the 19:40 scheduled scan it showed Intent done, the Collector failed with `query-spine-01`'s reason, and Observability done. After the `query-*` devices were removed, every later scan showed Intent, Collector and Observability done.

Durations, from each ScanDrift's first to last audit event, same lab and same hour:

| | scans | median | range |
|---|---:|---:|---|
| before (worker at 872d143, 18:00 to 19:25) | 18 | 1.44 s | 1.26 to 2.54 s |
| after (worker at 2279d70, 19:29 to 19:40) | 7 | 1.54 s | 1.46 to 1.97 s |

The extra audit activities cost about 0.1 s per scan, within the run-to-run spread.

## Item 8: fixtures, archive and snapshot (T049)

- **Fixtures.** `capture_prometheus_fixtures.py` captured the ten new views (`range_<view>.json`), each a 200 with the contract's labels. A test now reads each one through the dashboard's parser. The capture also rewrites spec 012's files, and those were kept as they were: they hold real gNMI errors from a paused leaf-04 (T043), which this hour did not have.
- **Archive.** `invoke history.archive` wrote a 4.2 MB tarball: an 8.7 MB audit DB, a 13.8 MB metrics snapshot, test runs and a 1.7 MB dashboard export. `snapl dashboard --archive` served it. The page measured 7155 px, and `workflow-mean` had 4 series. `scan-results`, `drift-paths` and `deploy-outcomes` read "no data in range", because the export's seven-day range ends at 19:06, rounded down to its 42-minute step, before the counters existed (#306).
- **Snapshot.** `snapl dashboard --snapshot --days 7` wrote 3,211,634 bytes: a 2,147,470-byte data bundle plus the page's code. The ten new views take 47,100 bytes at their 3-hour step. With the fabric history, the drift summary and the run steps, spec 015's parts come to at most 11% of the bundle. The bundle is mostly the drift timeline (1,318,841 bytes), which holds the lab's history of 26 devices. The new views end at 18:00, so the counters are not in it either (#306).

## What this does not show

- A counter's first appearance with only one observation, which `rate()` and `increase()` miss (#191): every series here was scraped at least twice.
- The SC-006 growth on the lab: there is no snapshot from before spec 015 on this lab to compare with. The fixture measurement in research R8 stands: +46%.
