# Orchestrator test fixtures

## `srlinux_spine01_collected.json`

Real `GnmiCollector.collect(device, DRIFT_PATHS)` output captured from a live
dcfabric lab node (`clab-dcfabric-spine-01`, Nokia SR Linux, `ghcr.io/nokia/srlinux:latest` as it
stood on 2026-07-10, before the lab image was pinned in #122), after applying a representative spine config via the real
`GnmiExecutor` (two fabric interfaces with /31 IPs, one eBGP underlay neighbor).

It is the exact shape the collector produces in production — keyed by the
requested gNMI paths (`/interface`, `/network-instance[name=default]/protocols/bgp`,
`/system`) with raw SR Linux `json_ietf` values (module-prefixed container keys,
`admin-state`, `subinterface[].ipv4.address[].ip-prefix`, etc.).

Volumetric, adapter-irrelevant subtrees were pruned to keep the fixture small
(per-interface `statistics`/`traffic-rate`, the full `/system` feature tree);
every field the adapter reads is preserved verbatim as the device returned it.
Regenerate with `dev/scripts/capture_srlinux_state.py` against a running lab.

## `srlinux_v26_3_1_description_only_port.json`

Real `GnmiCollector.collect(device, ["/interface"])` output from `clab-dcfabric-spine-01` on
SR Linux **v26.3.1** (`ghcr.io/nokia/srlinux:26.3.1`), captured 2026-10-28 for #122. It contains two ports:

- `ethernet-1/7`, given only `description hand-added-by-operator` by hand. The device reports `mtu: 9232`
  on it although nobody set one: its default, added once a port carries any config.
- `ethernet-1/8`, untouched, for contrast. It reports no mtu.

Only the same volumetric subtrees as above were pruned (`statistics`, `traffic-rate`, ...); every field the
adapter reads is verbatim. It pins the rule that a device-default value does not read as hand-added config.
