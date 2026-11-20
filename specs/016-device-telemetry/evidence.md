# Live Evidence: Device Telemetry

Recorded on 2026-11-20 on the dcfabric lab (SR Linux 26.3.1, six devices), with the worker started from main at b4369e9, the merge of #340. Raw output is in `.snapl/lab/2026-11-20/t026/`.

The lab had already been brought into line with #316, #318 and #340 before their merges (spec 016 research, and the #317 run earlier the same morning: scan 6/6 clean, each leaf learning the other five loopbacks). So this run is after #311 and #317, and item 3 below records the healthy fabric, not the flagged spines the quickstart was written for.

## Item 1: the fault captures (T057)

Two captures with `dev/scripts/capture_srlinux_telemetry.py`, every 10 s, while the worker from item 2 kept running. Timelines are in `.snapl/lab/2026-11-20/t057/timeline.txt`.

**leaf-01, `ethernet-1/49` disabled and re-enabled** (`srlinux_v26_3_1_leaf01_interface_down_up.json`, 150 s from 07:47:45 UTC). Disabled at +15 s, enabled at +46 s, with `sr_cli -ec`.

| t (s) | `ethernet-1/49` | BGP `10.10.1.0` (spine-01) | routes received |
|---:|---|---|---:|
| 0, 10 | up | established | 4 |
| 20 to 40 | down | active | 0 |
| 50 to 80 | up | active | 0 |
| 90 on | up | established | 4 |

The session takes about 45 s after the link to come back. `in-octets` stands still while the link is down. The running worker flagged leaf-01 and spine-01 at 07:48:19, 19 s after the disable, changed leaf-01's reasons when the link came back and only the session was left, and cleared both at 07:49:20.

**spine-01 while leaf-01 is paused** (`srlinux_v26_3_1_spine01_peer_paused.json`, 240 s from 07:50:35 UTC). `docker pause clab-dcfabric-leaf-01` at +15 s, `docker unpause` at +115 s.

| t (s) | `ethernet-1/1` | BGP `10.10.1.1` (leaf-01) | routes received |
|---:|---|---|---:|
| 0 to 90 | up | established | 2 |
| 100, 110 | up | connect | 0 |
| 120 | up | established | 0 |
| 130 on | up | established | 2 |

The link never goes down: a paused container keeps its veth up. The session drops only when the 90 s hold timer runs out, 85 s into the pause, so the quickstart's 60 s pause would have shown nothing here. The quickstart now asks for 100 s. The worker read leaf-01 `unknown` 30 s into the pause (never `unhealthy`), flagged spine-02 and spine-01 when their hold timers ran out (07:52:21, 07:52:31), and cleared all three at 07:52:41, 11 s after the unpause.

Both captures are read by `TestStreamTelemetryFaultCaptures` (the collector: the state sequence, routes, counters) and `TestLabCaptures` (through the collector and the runner: the health changes above). With the debounce set to three intervals instead of one, both `TestLabCaptures` tests fail.

## Item 2: the worker streams (T026, SC-004)

No worker was running. `uv run invoke orchestrator.start` started at 07:20:26 UTC. Each of the six fabric devices had its first sample between 07:20:31 and 07:20:41, 5 to 15 s after start: each one's `health_changed` event carries `last_sample_at` from 07:20:31.91 (leaf-03) to 07:20:41.83 (leaf-02). At 07:20:41 the runner logged all six `unknown → healthy`. No stream for a fabric device broke during the run.

The worker never logs a stream's state (`connecting`, `streaming`, `broken` live only inside the runner), so the quickstart's "its log names six streams `streaming`" can't be checked as written. The first sample time and the absence of `broke` warnings show the same thing. The quickstart now says so.

`curl -s localhost:18091/metrics | grep -c '^snapl_\(interface\|bgp\|device\)_'` gave **162**, the quickstart's figure after #311:

| family | series |
|---|---:|
| `snapl_interface_up` | 22 |
| `snapl_interface_octets_total` | 32 |
| `snapl_interface_errors_total` | 32 |
| `snapl_interface_discards_total` | 32 |
| `snapl_bgp_session_established` | 16 |
| `snapl_bgp_prefixes_received` | 16 |
| `snapl_device_cpu_percent` | 6 |
| `snapl_device_memory_percent` | 6 |

By device: 39 series for each spine, 21 for each leaf.

**Two devices from outside dcfabric were streamed too.** Infrahub also holds `edge-01` and `edge-02` (`use_case=test_edge`, no `lab_node_name`), which the integration tests seed and keep. The runner reads every use case in intent, so it dialled them at their management addresses (10.200.0.1 and .2), which nothing answers. Each logged `telemetry from edge-0N broke (… FutureTimeoutError)` with the backoff doubling, and stayed `unknown`. They add no series and are not in dcfabric's health, `snapl status` or the page, which read one use case. Tracked in #341.

## Item 3: the fabric's health (T026, SC-003)

After #311 and #317 the fabric has no fault to flag:

- `snapl status`: `fabric health   6 healthy, 0 unhealthy, 0 unknown`.
- `snapl audit --device <name> --json` for each of the six: exactly one `health_changed`, `unknown → healthy`, with no reasons.
- The dashboard's `/api/v1/health` (dashboard started from the same commit): all six `healthy`, no reasons, no episodes in the last 24 h.
- `snapl_bgp_session_established` is 1 for all 16 session ends. `snapl_bgp_prefixes_received` is above 0 for every one: 4 or 5 on each leaf session, 2 or 5 on each spine session.
- `snapl_interface_up` is 1 for all 22 interfaces in intent.
- CPU reads 3 % and memory 91 % on every node. Both are the OrbStack VM's, the same on all six, and neither crosses a threshold. They say nothing about a switch.

The quickstart's "both spines in sync and unhealthy, four `BGP … active` reasons each" was the lab before #311 and no longer reproduces. The flagged path is still to be shown on the lab, with a fault made on purpose: item 4 (an interface, T058) and item 7 (a BGP neighbour, T061).

## Item 4: an interface fault (T058, SC-001)

Same worker, with `snapl dashboard` from main at d741172. A poller read the page's own APIs, `/api/v1/network` (the drawer and the network views) and `/api/v1/health` (the fabric panel), every 2 s and logged each change. The page itself wasn't opened in a browser. Raw output is in `.snapl/lab/2026-11-20/t058/`.

| UTC | what happened | after the disable |
|---|---|---:|
| 08:48:01 | `docker exec clab-dcfabric-leaf-01 sr_cli -ec 'set / interface ethernet-1/49 admin-state disable'` | 0 s |
| 08:48:18 | `health_changed` for leaf-01 and spine-01: `unhealthy` | 17 s |
| 08:48:20 | `/api/v1/health`: leaf-01 `ethernet-1/49 down (expected up)`, `BGP 10.10.1.0 active (expected established)`; spine-01 `ethernet-1/1 down (expected up)`, `BGP 10.10.1.1 active (expected established)` | 19 s |
| 08:48:25 | `/api/v1/network`: leaf-01 `ethernet-1/49` and spine-01 `ethernet-1/1` down, both sessions not established, 0 routes | 24 s |
| 08:49:16 | `snapl scan` exits 2: `5 clean, 1 drifted`, leaf-01 `/interface[name=ethernet-1/49]/enabled True → False`. `snapl status`: `4 healthy, 2 unhealthy (leaf-01, spine-01)` | |
| 08:49:33 | `snapl reconcile --drifted --yes`: `1/1 succeeded` | |
| 08:49:38 | `health_changed`: both still `unhealthy`, the interface reason gone, the session's left | |
| 08:49:46 | `/api/v1/network`: both interfaces up | |
| 08:50:19 | `health_changed`: both `healthy` | 46 s after the reconcile |
| 08:50:21 | `/api/v1/health`: both healthy. `snapl scan`: `6 clean`, `snapl status`: `6 healthy` | 48 s after the reconcile |

SC-001 holds: shown down within 30 s (24 s), flagged within 60 s (19 s), and cleared within 60 s of the repair (48 s). The clear has the narrowest margin. The link is up 13 s after the reconcile, and the rest is the BGP session coming back, as in item 1's capture.

Drift and health are two separate marks: during the fault, the scan reported leaf-01 drifted and only leaf-01, and health reported leaf-01 and spine-01 unhealthy. spine-01 is in sync the whole time: nothing on it differs from intent, but its link and session are down.

The quickstart said spine-01's "reasons grow by one". That was written for the lab before #311, when spine-01 was already flagged. Now it starts healthy and gets two reasons, its interface and its session. The quickstart now says so.

An earlier attempt at 08:45:31 was dropped: the page poller had failed to start (`python3` on the host is 3.9), so that run has no page timeline. The interface was re-enabled at 08:46, and the run above started from a healthy fabric.

## Item 8: an archive and a snapshot across a fault (T062, SC-008)

Item 4 again: leaf-01's `ethernet-1/49` was disabled at 10:05:04 and reconciled at 10:06:11. leaf-01 was flagged 10:05:22 to 10:07:03 and spine-01 10:05:32 to 10:07:03. `snapl dashboard` was running on 18030, which `history.archive` needs: a first try with it on another port gave an archive with no dashboard export, which was deleted. Raw output is in `.snapl/lab/2026-11-20/t062/`.

`uv run invoke history.archive` at 10:08:38: `snapl-history-20261120T100838Z.tar.gz`, 9.3 MB (audit DB 12.4 MB, metrics snapshot 27.0 MB, test runs 79.6 KB, dashboard export 2.2 MB, before compression).

It was opened with `snapl dashboard --archive … --port 18061` and `PROMETHEUS_ADDRESS`, `INFRAHUB_ADDRESS` and `TEMPORAL_HOST` pointed at port 1 and `SNAPL_AUDIT_DB` at a path that doesn't exist. The stack itself was left running, since other work shares it. Every source the page would read live was unreachable.

**The health history: SC-008 holds.** `/api/v1/health` came from the archive's trail (`source: audit`) and listed every episode of the day with its start, end and reasons: six for leaf-01 (07:48 to 10:07), seven for spine-01, and spine-02's 20 s at 07:52 when its hold timer ran out during item 1. The fault above is the last one: leaf-01 `10:05:22 → 10:07:03`, `ethernet-1/49 down (expected up)` and `BGP 10.10.1.0 active (expected established)`. leaf-03 has no episode: its 60 s `unknown` in item 5 opens none, as data-model.md says.

**The network views: US5 scenario 1 doesn't hold.** The archive exports the six views over 7 days at a 2520 s (42 min) step. `bgp-established` has instant points at 07:30, 08:12, 08:54 and 09:36, and leaf-01 reads 2 sessions at each, so none of the day's faults shows. The export ends at 09:36, 32 min before the archive was made (#306), so the 10:05 fault isn't in it at all, and any other range is refused. SC-008 asks only for the health history, so it holds. The scenario's "the network views show the interface down over that time" does not. Tracked in #351.

**The snapshot.** `snapl dashboard --snapshot … --days 7` at 10:09: **3.50 MB**, against spec 015's 3.2 MB. It contains device health and the network, as its summary says.

**The Prometheus fixtures.** `dev/scripts/capture_prometheus_fixtures.py` at 10:07 wrote the six views' `range_<view>.json`. The other files it rewrites were restored, and the admin snapshot it leaves in `.snapl/prometheus/snapshots/` was deleted. Each view has all six devices. `bgp-established` has leaf-01 between 1 and 2 sessions and spine-01 between 3 and 4, from item 7 and the fault above. New tests read each file.
