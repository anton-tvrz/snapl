# Orchestrator test fixtures

## `srlinux_spine01_collected.json`

Real `GnmiCollector.collect(device, DRIFT_PATHS)` output captured from a live
dcfabric lab node (`clab-dcfabric-spine-01`, Nokia SR Linux, `ghcr.io/nokia/srlinux:latest`)
on 2026-07-10, after applying a representative spine config via the real
`GnmiExecutor` (two fabric interfaces with /31 IPs, one eBGP underlay neighbor).

It is the exact shape the collector produces in production — keyed by the
requested gNMI paths (`/interface`, `/network-instance[name=default]/protocols/bgp`,
`/system`) with raw SR Linux `json_ietf` values (module-prefixed container keys,
`admin-state`, `subinterface[].ipv4.address[].ip-prefix`, etc.).

Volumetric, adapter-irrelevant subtrees were pruned to keep the fixture small
(per-interface `statistics`/`traffic-rate`, the full `/system` feature tree);
every field the adapter reads is preserved verbatim as the device returned it.
Regenerate with `dev/scripts/capture_srlinux_state.py` against a running lab.
