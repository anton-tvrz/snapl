# Live Evidence: Remove Undesired Config

## US1: the preview (T026, quickstart steps 1 and 2)

Recorded on 2026-11-24, 20:05 to 20:07 UTC, on the dcfabric lab's leaf-04 (SR Linux v26.3.1-410), with the worker started from branch `013-us1-preview` at 81eb802. The worker ran on its own task queue (`snapl-013-live`) with a scratch audit DB, telemetry and metrics off. No other worker was polling, so the report-only drift schedule ran nothing during the run and was left as it was. Raw output is in `.snapl/lab/2026-11-24/spec013-us1/` in the main checkout.

**Before.** `snapl scan`: 6 devices, 6 clean, exit 0.

**Step 1: config added by hand** with `sr_cli -ec`:

```
set / interface ethernet-1/7 description spec013-probe
set / interface ethernet-1/7 admin-state enable
set / network-instance default protocols bgp neighbor 192.0.2.0 peer-as 65099 peer-group underlay-ipv4@leaf-04
set / interface ethernet-1/49 subinterface 0 ipv4 address 192.0.2.1/31
```

`snapl scan`: leaf-04 drifted with five undesired items, exit 2: the extra address on `ethernet-1/49`, `ethernet-1/7`'s description and `enabled`, and the neighbour's `peer_asn` and `peer_group`.

**Step 2: preview twice (SC-002).**

```
$ snapl remove leaf-04
leaf-04: 3 deletes planned from a scan at 20:05:58Z

  delete /interface[name=ethernet-1/49]/subinterface[index=0]/ipv4/address[ip-prefix=192.0.2.1/31]
  delete /interface[name=ethernet-1/7]
         answers …/description, …/enabled
  delete /network-instance[name=default]/protocols/bgp/neighbor[peer-address=192.0.2.0]
         answers …/peer_asn, …/peer_group
         removing a neighbour tears its session down; its peer group stays configured

Nothing was written. To remove: snapl remove leaf-04 --apply
exit 2
```

`snapl remove leaf-04 --json` gave the same three deletes, no refusals and no forward drift, exit 2 (`plan.json`).

`sr_cli "info flat"` before the first preview and after the second: identical, 684 lines. **SC-002 holds.**

Each preview is one `PlanRemoval` run in the audit trail, with its steps in order (`fetch_desired_state`, `collect_drift_state`, `detect_drift`, `plan_removal`, `dry_run_remove`) and a terminal event carrying `plan_paths` (the three deletes) and `refused_paths` (none).

**The plan, applied by hand.** The cleanup deleted exactly the three planned entries with `sr_cli -ec "delete / …"`. Then:

- `snapl scan`: 6 of 6 clean, exit 0.
- `snapl remove leaf-04`: `leaf-04: nothing to remove`, exit 0.
- `ethernet-1/49` subinterface 0 keeps its intended address `10.10.1.7/31` and `ipv4 admin-state enable` (the R4 leftover, which the scan does not report).
- Both fabric sessions (`10.10.1.6`, `10.10.2.6`) stayed established throughout, up 8 h 50 m.
- No `spec013` or `192.0.2.` config is left on leaf-04.
