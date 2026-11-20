# Live Evidence: Device Telemetry

Recorded on 2026-11-20 on the dcfabric lab (SR Linux 26.3.1, six devices), with the worker started from main at b4369e9, the merge of #340. Raw output is in `.snapl/lab/2026-11-20/t026/`.

The lab had already been brought into line with #316, #318 and #340 before their merges (spec 016 research, and the #317 run earlier the same morning: scan 6/6 clean, each leaf learning the other five loopbacks). So this run is after #311 and #317, and item 3 below records the healthy fabric, not the flagged spines the quickstart was written for.

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
