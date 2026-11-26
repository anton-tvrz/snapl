# Contract: `snapl remove`

```text
snapl remove DEVICE [--apply] [--yes] [--json] [--use-case USE_CASE]
```

`DEVICE` is a device name as it appears in the SoT, resolved with `resolve_device` as `deploy` does. A device that is not in intent is refused before anything starts ("decommissioning a device is a different operation").

## Preview (default)

The command runs `PlanRemoval` and renders the plan. It makes no device write (FR-001, SC-002).

Human output, for example:

```text
leaf-04: 3 deletes planned from a scan at 14:05:12Z

  delete /interface[name=ethernet-1/7]
         answers …/description, …/enabled
  delete /interface[name=ethernet-1/49]/subinterface[index=0]/ipv4/address[ip-prefix=192.0.2.1/31]
  delete /network-instance[name=default]/protocols/bgp/neighbor[peer-address=192.0.2.0]
         answers …/peer_asn, …/peer_group
         removing a neighbour tears its session down; its peer group stays configured

  refused /routing-policy/policy[name=c-probe] — not removable in this version

  2 paths are missing or differ from intent: snapl reconcile leaf-04 heals those, not this.

Nothing was written. To remove: snapl remove leaf-04 --apply
```

- **JSON** (`--json`): `{"plan": RemovalPlan}`, as the model's JSON.
- **Nothing undesired**: "leaf-04: nothing to remove". If there are refusals, they are still listed.
- **Exit codes**:
  - 0: nothing to remove.
  - 2: there is something to remove; exit 2 already means "drift found" (spec 006 FR-011).
  - 1: an error (unreachable, intent unavailable, no worker).

## Apply (`--apply`)

1. **No terminal**: if stdin is not a TTY and `--yes` is missing, the command refuses with "--apply needs --yes when there is no terminal to confirm at" before connecting (FR-006). `--json` without `--yes` is refused as `_confirm` already does.
2. It runs the preview and renders it as above. If there are no deletes, it says so and exits 0 without starting a removal.
3. It asks for confirmation, unless `--yes` was given: `Remove N config entries from leaf-04? This deletes them from the device.`
4. It starts `RemoveConfig` with `RemovalRequest(device_id, actor=_actor(), confirmed_paths=plan.delete_paths)` under the id `remove-config-{device_id}`, with conflict policy FAIL, and renders the result:
   - **`succeeded`**: "removed N entries from leaf-04; the verifying scan finds none of them", plus any `still_refused`. Exit 0.
   - **`plan_changed`**: "leaf-04 changed since the preview; nothing was removed", then the new plan. Exit 1.
   - **`deploy_in_flight`**: "a deploy to leaf-04 is running; nothing was removed. Run this again when it ends." Exit 1.
   - **`remove_failed`**: "leaf-04 rejected the removal; nothing was removed", plus the device's reason. Exit 1.
   - **`verification_failed`**: the deleted paths, then `remaining` and `new_forward`. Exit 1.
   - **`collect_failed` after the write**: "deleted N entries; the verifying scan could not read leaf-04", plus the cause and a hint to run `snapl scan`. Exit 1.
   - **Workflow already started**: "a removal of leaf-04 is already running". Exit 1.

`snapl reconcile` does not change, and still never deletes (FR-013).

## Audit rendering

`snapl audit --device leaf-04` lists `PlanRemoval` and `RemoveConfig` runs through the existing table. The renderer gains one line for a `RemoveConfig` terminal event:
- `removed N: <paths>; verified`, or the reason when it was not;
- the actor, from the start event.

## Tests

`tests/unit/test_presentation/test_cli_remove.py` mocks the Temporal client as the other CLI tests do. It covers:
- a preview with deletes (exit 2), with nothing to remove (exit 0), and with refusals only (exit 0);
- `--json`;
- `--apply` with `--yes`;
- `--apply` with a prompt answered yes and answered no;
- a non-TTY without `--yes`, which is refused and never connects;
- `--json` without `--yes`;
- each result reason's text and exit code;
- the workflow-already-started message;
- the device not in intent;
- that the request's `confirmed_paths` equal the shown plan's `delete_paths`.

`test_render.py` adds the plan and result views, human and JSON, and the audit line.
