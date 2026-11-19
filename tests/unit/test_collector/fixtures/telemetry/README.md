# SR Linux telemetry captures (spec 016)

Real sample-mode gNMI subscriptions from the dcfabric lab, captured on 2026-11-19 from SR Linux `v26.3.1-410-g98ed16c774c` with `dev/scripts/capture_srlinux_telemetry.py`. Every subscription samples every 10 s, in `json_ietf`, and each file holds 35 s of messages. Each entry is `{"t_rel": <seconds since the subscription started>, "msg": <pygnmi's message>}`.

| file | node | paths |
| --- | --- | --- |
| `srlinux_v26_3_1_leaf01_sample.json` | leaf-01 (172.20.21.13) | `ethernet-1/49` oper-state and statistics, `lo0` oper-state and statistics, CPU `total/instant`, memory `utilization` |
| `srlinux_v26_3_1_spine01_sample.json` | spine-01 (172.20.21.11) | `ethernet-1/1` oper-state and statistics, the BGP neighbour `10.10.1.1`'s `session-state`, `last-state` and ipv4-unicast `received-routes`, CPU, memory |

What they show (spec 016 research R1):

- The first message carries every path at once with `sync_response`. After that the device sends one message per path at each interval.
- An update's `path` comes back module-prefixed and cut at the list entry: subscribing to `/interface[name=ethernet-1/49]/oper-state` returns path `srl_nokia-interfaces:interface[name=ethernet-1/49]` with `val` `{"oper-state": "up"}`.
- Counters are decimal strings (`"in-octets": "2925084"`). CPU and memory are integers.
- `lo0`'s statistics are an empty object. A loopback has no counters, only a state.
- spine-01's neighbour `10.10.1.1` is `active` with 0 received routes, because no leaf has BGP config (#311). That is the lab's real state, not a capture fault.
- Memory reads `94` on every node, from the same 16.8 GB `physical`: the containers report the VM's memory.

Not captured yet (lab tasks in spec 016's tasks.md): an interface going down and up, a session establishing and dropping once #311 is fixed, and a paused device.
