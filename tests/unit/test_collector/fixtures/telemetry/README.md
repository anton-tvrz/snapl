# SR Linux telemetry captures (spec 016)

Real sample-mode gNMI subscriptions from the dcfabric lab, captured from SR Linux `v26.3.1-410-g98ed16c774c` with `dev/scripts/capture_srlinux_telemetry.py`. Every subscription samples every 10 s, in `json_ietf`, the two `_sample` files hold 35 s each (2026-11-19), and the two fault captures are longer (2026-11-20, below). Each entry is `{"t_rel": <seconds since the subscription started>, "msg": <pygnmi's message>}`.

| file | node | paths |
| --- | --- | --- |
| `srlinux_v26_3_1_leaf01_sample.json` | leaf-01 (172.20.21.13) | `ethernet-1/49` oper-state and statistics, `lo0` oper-state and statistics, CPU `total/instant`, memory `utilization` |
| `srlinux_v26_3_1_leaf01_interface_down_up.json` | leaf-01 (172.20.21.13), 2026-11-20, 150 s | `ethernet-1/49` oper-state and statistics, the BGP neighbour `10.10.1.0` (spine-01)'s `session-state` and `received-routes`. `ethernet-1/49` disabled on the device at +15 s and enabled at +46 s. |
| `srlinux_v26_3_1_spine01_peer_paused.json` | spine-01 (172.20.21.11), 2026-11-20, 240 s | `ethernet-1/1` oper-state and statistics, the BGP neighbour `10.10.1.1` (leaf-01)'s `session-state` and `received-routes`. `docker pause clab-dcfabric-leaf-01` from +15 s to +115 s. |
| `srlinux_v26_3_1_spine01_sample.json` | spine-01 (172.20.21.11) | `ethernet-1/1` oper-state and statistics, the BGP neighbour `10.10.1.1`'s `session-state`, `last-state` and ipv4-unicast `received-routes`, CPU, memory |

What they show (spec 016 research R1):

- The first message carries every path at once with `sync_response`. After that the device sends one message per path at each interval.
- An update's `path` comes back module-prefixed and cut at the list entry: subscribing to `/interface[name=ethernet-1/49]/oper-state` returns path `srl_nokia-interfaces:interface[name=ethernet-1/49]` with `val` `{"oper-state": "up"}`.
- Counters are decimal strings (`"in-octets": "2925084"`). CPU and memory are integers.
- `lo0`'s statistics are an empty object. A loopback has no counters, only a state.
- spine-01's neighbour `10.10.1.1` is `active` with 0 received routes, because no leaf has BGP config (#311). That is the lab's real state, not a capture fault.
- Memory reads `94` on every node, from the same 16.8 GB `physical`: the containers report the VM's memory.

What the two fault captures show (spec 016 T057, read by `TestStreamTelemetryFaultCaptures` in `test_collector.py` and `TestLabCaptures` in `test_orchestrator/test_telemetry_runner.py`):

- Disabling `ethernet-1/49` shows at the next sample: `oper-state` `down`, and the session over it `active` with 0 routes in the same sample. Enabling it brings the link `up` at the next sample, but the session takes about 45 s more to re-establish (+90 s). The counters stand still while the link is down.
- A paused peer leaves the link `up`: the veth stays up. The session stays `established` until the 90 s hold timer runs out (+100 s, 85 s into the pause), then reads `connect` with 0 routes, and is `established` again 5 s after the unpause. A pause shorter than the hold time shows nothing on the spine, which is why the pause is 100 s and not the quickstart's original 60 s.
