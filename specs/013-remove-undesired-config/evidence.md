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

## US2: the removal (T039, quickstart steps 3, 4 and 6)

Recorded on 2026-11-26, 08:48 to 08:51 UTC, on the dcfabric lab's leaf-04 (SR Linux v26.3.1), with the worker started from `main` at 27851e5 in a worktree. It polled the default queue (`snapl-orchestrator`) and wrote to the main checkout's audit trail, so the dashboard reads this run. No other worker was running. The report-only drift schedule was paused for the run (`snapl schedule pause --reason "spec 013 evidence (T039, T053)"`) and resumed afterwards. Raw output is in `.snapl/lab/2026-11-26/spec013-us2/` in the main checkout.

**Before.** `snapl scan`: 6 devices, 6 clean, exit 0. Step 1's four `sr_cli -ec` commands (as in US1) gave leaf-04 the same five undesired items, exit 2.

**Step 3: the device changes at the prompt (SC-004).** `snapl remove leaf-04 --apply` ran in a pseudo-terminal. When the prompt appeared, `set / interface ethernet-1/8 description spec013-late` went to leaf-04, and the answer was `y`:

```
leaf-04: 3 deletes planned from a scan at 08:49:39Z
  …
Nothing was written.
Remove 3 config entries from leaf-04? This deletes them from the device. [y/N]: y
leaf-04 changed since the preview; nothing was removed

leaf-04: 4 deletes planned from a scan at 08:49:42Z

  delete /interface[name=ethernet-1/49]/subinterface[index=0]/ipv4/address[ip-prefix=192.0.2.1/31]
  delete /interface[name=ethernet-1/7]
         answers …/description, …/enabled
  delete /interface[name=ethernet-1/8]
         answers …/description, …/enabled
  delete /network-instance[name=default]/protocols/bgp/neighbor[peer-address=192.0.2.0]
         answers …/peer_asn, …/peer_group
         removing a neighbour tears its session down; its peer group stays configured

Nothing was written. To remove: snapl remove leaf-04 --apply
exit 1
```

`sr_cli "info flat"` before the command and after it differ by one line, the late description. **SC-004 holds.** The `RemoveConfig` run ended `plan_changed`, with the three confirmed paths at its start, the four-delete plan at its end, and no device response.

A first attempt piped the answer through macOS `script`, which sent an end-of-file to the prompt before the `y`. The CLI answered `Aborted.`, started nothing and exited 1. It is kept as `03a-script-pipe-aborted.txt` and is not part of the evidence.

**Step 4: remove (SC-001).** The plan now held four deletes, `ethernet-1/8` among them.

```
$ snapl remove leaf-04 --apply --yes
leaf-04: 4 deletes planned from a scan at 08:49:59Z
  …
Nothing was written.
removed 4 entries from leaf-04; the verifying scan finds none of them
  deleted /interface[name=ethernet-1/49]/subinterface[index=0]/ipv4/address[ip-prefix=192.0.2.1/31]
  deleted /interface[name=ethernet-1/7]
  deleted /interface[name=ethernet-1/8]
  deleted /network-instance[name=default]/protocols/bgp/neighbor[peer-address=192.0.2.0]
exit 0
```

- `snapl scan`: 6 of 6 clean, exit 0. **SC-001 holds.**
- `info flat` of `ethernet-1/7`, `ethernet-1/8` and neighbour `192.0.2.0`: nothing.
- `ethernet-1/49` subinterface 0 keeps its intended address `10.10.1.7/31`, plus `ipv4 admin-state enable` (the R4 leftover).
- The device answered the one Set with one `DELETE` per path (`tests/unit/test_executor/fixtures/remove_response_leaf04_four_entities.json`).
- Both fabric sessions (`10.10.1.6`, `10.10.2.6`) were established before and after, with continuous uptime: 4 m 14 s at 08:49:58, 4 m 22 s at 08:50:05. They last came up at about 08:45:44Z, before the run wrote anything. The probe neighbour `192.0.2.0` was `active` before and is gone.

**Step 6: a port and its binding in one Set (R3).** `ethernet-1/7` with a description, subinterface 0 with `192.0.2.5/31`, and `default` binding `ethernet-1/7.0`. The scan read five undesired items. Then:

```
$ snapl remove leaf-04 --apply --yes
leaf-04: 2 deletes planned from a scan at 08:50:26Z

  delete /interface[name=ethernet-1/7]
         answers …/description, …/ip_address, …/prefix_length, …/enabled
  delete /network-instance[name=default]/interface[name=ethernet-1/7.0]
         answers …/name

Nothing was written.
removed 2 entries from leaf-04; the verifying scan finds none of them
  deleted /interface[name=ethernet-1/7]
  deleted /network-instance[name=default]/interface[name=ethernet-1/7.0]
exit 0
```

C1 showed the port alone is rejected while the binding stays. Together they went in one Set and were accepted (`remove_response_leaf04_port_and_binding.json`). `snapl scan`: 6 of 6 clean.

**Afterwards.** leaf-04's `info flat` is identical to its config before step 1: no `spec013`, `192.0.2.`, `ethernet-1/7` or `ethernet-1/8` line is left. The schedule was resumed.

**Fixtures (constitution III).**
- The two Set responses are in `tests/unit/test_executor/fixtures/`, read back from the audit trail.
- C1's rejection text is in the same folder (`remove_rejection_c1.txt`).
- The three runs' audit trail is `tests/unit/test_presentation/fixtures/remove_config_leaf04_trail.json`.
- `test_gnmi_remove.py` and `test_dashboard_projections.py::TestLiveRemovalTrail` use them.

## US3: the record (T053, quickstart step 5)

The same run, read back right after step 6.

**`snapl audit --device leaf-04`** lists each `PlanRemoval` and `RemoveConfig` step under the workflow's id, with actor `anton` on every `RemoveConfig` row. The last removal's end reads:

```
remove-config-18e67b75-…  workflow_terminated  removed 2: /interface[name=ethernet-1/7],
                                               /network-instance[name=default]/interface[name=ethernet-1/7.0];
                                               verified                                         anton
```

**The dashboard** (`snapl dashboard` from the same worktree; screenshots in `screens/`, light and dark):

- **Run list** (`runs-*.png`): three `RemoveConfig` runs, trigger `operator (anton)`, each with its removal detail.
  - The two removals: confirmed by anton, the deleted paths, "The verifying scan finds none of them.", and the device's response.
  - The `plan_changed` run: "The device changed after the preview; nothing was removed." and the new plan.
- **Closed episodes** (`/api/v1/drift-timeline`): both of leaf-04's episodes today, 76 s and 5 s, have `closed_by.kind = "removal"`. The number row (`number-row-latest-repair-*.png`) reads "Latest repair 5 s, leaf-04, scan to scan, repaired by an operator's removal". They closed at the next scan, run by hand here rather than by the paused schedule.
- **leaf-04's drawer** (`drawer-leaf-04-*.png`): in sync, "Last removal preview: 2 deletes, 0 refused · 1 min ago", and the removal runs among its recent runs. Its buttons are Close, the copy controls and the existing Loop buttons, which choose a run to show. None starts anything.
- **The plain page** (`/plain`, `plain-runs.png`): the same three runs with the same lines, and leaf-04's row reads "last removal preview: 2 deletes, 0 refused".

**SC-005 holds.**

**Found here.** A `plan_changed` run said "leaf-04 changed since the preview; nothing was removed" three times on both pages: in the outcome line, as the reason in words, and as the detail. The detail line is now shown only when it says more than the outcome line. Fixed in the PR that records this evidence.
