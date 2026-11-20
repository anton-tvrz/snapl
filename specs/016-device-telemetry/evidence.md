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
