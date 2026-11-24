# Research: Remove Undesired Config

**Feature**: 013-remove-undesired-config
**Date**: 2026-11-24

The findings below were checked against `main` at `1c52b3a`. The device behaviour comes from the live captures in [captures.md](captures.md), run on the dcfabric lab's leaf-04 (SR Linux v26.3.1-410) earlier the same day. Nothing else in this document was run against the lab.

## R1: Where the plan is computed

**Decision**: The Observer computes the plan. `Observer.plan_removal(report) -> RemovalPlan` is a new method on the Observability ABC. `StructuralObserver` implements it in a new module, `structural/removal.py`, beside `diff.py`. The module reads `ENTITY_FIELD_MAP`.

**Rationale**:
- The diff builds every undesired item's path from `ENTITY_FIELD_MAP[kind]["path_template"]`, and these templates are already gNMI paths. Turning an undesired item into a delete path is the diff run backwards, so it belongs to the same seam: the drift field map (Principle VI).
- The protected set lives in the field map (`ENTITY_FIELD_MAP["interface"]["protected"]`). Spec 013 FR-004 requires detection and removal to use the same list, and planning inside the field map's module makes that hold by construction.
- Workflows stay vendor-free. A workflow calls an activity that calls the Observer, which is how `detect_drift` already works. `test_vendor_seams.py` keeps checking this.

**Alternatives considered**:
- **The Executor plans.** The Executor would have to read drift reports and the field map, which reverses its dependency (`executor → intent` only). Rejected.
- **The workflow plans.** Path shapes are vendor knowledge, so they would put a vendor into a workflow. Rejected.
- **The CLI plans.** The CLI would plan from data it collected itself, outside Temporal and outside the audit trail. FR-002 also requires the plan to come from a scan taken at preview time, and scans run as workflows. Rejected.

## R2: From undesired items to deletes

**Decision**: The planner groups a report's `undesired` items by entity and gives each entity exactly one delete. It never plans anything for forward items (FR-003).

| Undesired item (`entity_kind`, path shape) | Planned delete | Captures |
|---|---|---|
| `interface`, `/interface[name=X]/<field>` (an entity outside intent) | `/interface[name=X]`, the entry. One delete answers every field item of X. | C2: no defaults remain, and the scan is clean |
| `interface`, `/interface[name=X]/subinterface[index=N]/ipv4/address[ip-prefix=P]` (an extra address on an interface in intent, #193) | The item's own path, exactly as written | C3: the `/` inside the key needs no escaping |
| `bgp_session`, `/network-instance[name=default]/protocols/bgp/neighbor[peer-address=A]/<field>` | `/network-instance[name=default]/protocols/bgp/neighbor[peer-address=A]` | C4: a neighbour deletes on its own |
| `routed_interface`, `/network-instance[name=default]/interface[name=X.N]/name` | `/network-instance[name=default]/interface[name=X.N]` | C1: the binding has to go in the same Set as its port (R3) |
| `routing_policy`, `/routing-policy/policy[name=P]/<field>` | none; refused as `not_removable` | R3 |
| any other shape | none; refused as `not_removable` | FR-014 |

The entity path is the item's path with its trailing `/<field>` removed. The planner then checks that path against the entity's `path_template` key pattern; `diff._key_pattern` already compiles these patterns. An item whose path matches no row in the table is refused, not guessed at. Today the diff produces no such items, but the guard keeps the planner safe when the diff gains a new kind.

**Refusals.** A refused entity appears in the plan with its drift paths and a reason, and nothing is deleted for it (US1 scenario 3). There are two reasons:
- `protected`: the interface key is in `ENTITY_FIELD_MAP["interface"]["protected"]`. For a `routed_interface`, the planner checks the parent interface, the part of `X.N` before the last `.`. The diff never reports a protected interface. It would, however, report a binding of `system0.0` that intent lacks, because `routed_interface` has no protected list of its own. On the lab today `system0` has no config and `default` binds only `ethernet-1/49.0`, `ethernet-1/50.0` and `lo0.0` (read on leaf-04 on 2026-11-24). The planner refuses such a binding instead of deleting it.
- `not_removable`: the kind or shape is outside this cut (FR-014, R3).

**Order.** Deletes are sorted by path, so two previews of an unchanged device show the same plan. A Set does not care about order (C1).

**Alternatives considered**:
- **One delete per drift item (leaf deletes).** For a port outside intent that carries only `enabled: true`, the item is `/interface[name=X]/enabled`. Deleting that leaf leaves the entry, which then defaults to enabled, so the port still reads as undesired (spec edge case). C2 shows that deleting the whole entry leaves nothing behind. Rejected.

## R3: The removable kinds: `routed_interface` in, `routing_policy` out

**Decision** (the maintainer agreed on 2026-11-24): `routed_interface` (#315) joins the kinds this cut removes, and `routing_policy` (#317) stays out. The spec's FR-014 and Out of Scope are amended to match.

**Rationale**:
- C1: a Set that deletes a port while `default` still binds its subinterface is rejected as a whole (`FailedPrecondition … subinterface ethernet-1/7.0 not found`). Every routed port outside intent comes with an undesired binding, so without the binding no such port could ever be removed. SC-001's hand-added port carries only a description and has no binding. A port added by hand with an address and a binding would still fail.
- A binding without its port, on a port that intent manages but gives no address, deletes on its own. Deleting the binding drops the subinterface from routing, which is exactly what intent says.
- The pairing needs no special rule. A port outside intent has no binding that intent wants (`_routed_interfaces` derives bindings only from interfaces in intent), so its binding is always undesired too, and both deletes go in the device's one Set (FR-008).
- `routing_policy`: C4 shows that SR Linux refuses to delete an object a remaining neighbour still names. A removable policy would need a rule about which neighbours still name it, from intent and from the device, in both directions. Spec 013 does not ask for this, so it is not added now (Principle VII). An undesired policy shows in the preview as refused, with the reason `not_removable`.

## R4: Leftovers the scan does not see

**Decision**: These are accepted and documented, and the diff gains no new kinds for them. Two leftovers are known:
- an empty peer group after its last undesired neighbour is removed (C4), and
- `ipv4 { admin-state enable }` on a subinterface after an extra address is removed (C3).

**Rationale**: The diff does not report either one (`ENTITY_FIELD_MAP` has no group entity and no `ipv4 admin-state` field). Neither carries traffic: a group with no neighbours forms no session, and a subinterface with no address routes nothing. SC-001 asks only that the next scan has no undesired items, and with these leftovers it does. Detecting them would mean new diff kinds, a new protected-set question and new fixtures. They can come in a later change if an operator needs them. The preview says that removing a neighbour leaves its peer group configured, and README's limitations list both leftovers.

## R5: The Executor contract

**Decision**: The Executor ABC gains two methods, `dry_run_remove(device, paths)` and `remove(device, paths)`. Both return `RemoveResult`.
- `dry_run_remove` checks the paths against the Executor's guard and makes no connection. It is the `dry_run()` that constitution VIII.4 and VIII.5 require before a destructive change. A preview calls it, and so does a removal immediately before it writes.
- `remove` checks the paths with the same guard. If any path fails the check, it writes nothing. If all pass, it sends **one gNMI Set whose only entries are `delete`s** (FR-008). C1 shows that SR Linux commits a Set as one transaction: a rejection leaves the device unchanged, and the error names the path.

**The guard**, in `GnmiExecutor` (the vendor seam), runs before any dial:
- **Shape**: each path matches one of the four entity shapes in R2's table. This rejects `/`, `/interface`, `/network-instance[name=default]/protocols/bgp` and anything else above an entity, as well as leaf paths under an entity other than an address (FR-005, SC-003).
- **Protected**: the interface key, or the parent of a binding, is not in the protected set.
- **Non-empty and unique**: an empty list or a repeated path is a programming error and raises `ValueError`, as `apply_batch` does.

**Where the protected set comes from**: The worker bootstrap passes `ENTITY_FIELD_MAP["interface"]["protected"]` to `GnmiExecutor(protected_interfaces=…)`. When that argument is not given, `remove` and `dry_run_remove` refuse every path, so a GnmiExecutor built without the set cannot delete anything. The Executor never imports Observability behaviour, so the dependency arrows stay as they are. There is still only one list, the field map's, which keeps FR-004's "not a copy". A unit test builds the executor the way the bootstrap does and checks that it refuses every protected name the field map holds.

**Metrics**: a removal's Set is recorded as `snapl_gnmi_call_duration{operation="configure"}`, the same as an apply. It is a Set like an apply's, and a new label value would change the families spec 012 fixed.

**Alternatives considered**:
- **Plan inside the Executor and skip a separate guard.** The plan needs drift knowledge (R1). Rejected.
- **Copy the protected names into the executor, held together with the field map by a test.** That copy is exactly what FR-004 rules out. Rejected.
- **Move the protected names into a shared models module.** Models must never name a vendor (Principle VI). Rejected.
- **`replace` with an empty value.** Prohibited by FR-005 and spec 007 FR-011. Rejected.

## R6: Two workflows: `PlanRemoval` and `RemoveConfig`

**Decision**:
- **`PlanRemoval(device_id) -> RemovalPlan`** is the preview. It is read-only: fetch intent, collect, detect, plan, then `dry_run_remove`. It never writes, and it records its start and end in the audit trail like any other run.
- **`RemoveConfig(RemovalRequest) -> RemovalResult`** is the confirmed removal. In order:
  1. check that no deploy is in flight (R8);
  2. take a fresh scan and plan, the same steps as `PlanRemoval`;
  3. compare the new plan with the confirmed one (R7);
  4. check for a deploy again, then run `dry_run_remove`;
  5. `remove` (one Set);
  6. take a verifying scan (R9);
  7. audit.

The steps shared by both workflows are one helper in `workflows/removal.py`. Neither workflow runs the other as a child, so a removal is one run in the run list, and its steps show in the run's step view.

**Rationale**:
- FR-002 needs a scan at preview time, and FR-007 needs another one at confirmation. Only a workflow reaches the devices: the CLI never dials, and activities run in the worker.
- Keeping preview and removal as two types means the run list and `snapl audit` never show a preview as a write. A single type with a "preview" flag would need every projection that counts writes to check that flag.
- `ScanDrift` stays untouched, and so does every projection that reads ScanDrift scans (episodes, scan results). A removal's scans are its own steps and never count as fabric scans.

**Alternatives considered**:
- **Run `ScanDrift` for the use case and plan from its report.** It scans every device to remove from one, and the dashboard would count those scans. Rejected.
- **One workflow that waits for a confirmation signal.** The CLI would have to hold a workflow open across a human prompt, and an abandoned prompt would leave the run open. A second workflow that re-scans covers FR-007 anyway. Rejected.

## R7: What "the same plan" means

**Decision**: Two plans are the same exactly when their sorted lists of delete paths are equal. `RemovalRequest.confirmed_paths` carries the list the operator was shown. If the fresh plan's list differs, `RemoveConfig` writes nothing and returns `PLAN_CHANGED` with the new plan (FR-007, SC-004). If the fresh plan has no deletes left (somebody already cleaned up by hand), the run also writes nothing, and it reports `PLAN_CHANGED` with an empty plan.

**Rationale**: The deletes are what gets written, and they are what the operator agreed to. Refusals never write, so a change in refusals alone does not make the confirmation stale. A refusal that appears or disappears always comes with a delete that changes too, because an entity is either planned or refused.

## R8: Serialized with deploys (FR-010): refuse, don't wait

**Decision**:
- A removal runs under the workflow id `remove-config-{device_id}`, with `WorkflowIDConflictPolicy.FAIL`. Two removals of the same device therefore cannot run at once.
- `RemoveConfig` runs a new activity, `deploy_in_flight(device_id)`, at its start and again immediately before the Set (step 4 in R6). The activity uses the worker's Temporal client (`activity.client()`, as `stand_down_schedule` does) to describe `deploy-intent-{device_id}`. If a deploy is running, the removal writes nothing and ends `DEPLOY_IN_FLIGHT`, and the CLI says to run it again once the deploy ends. This matches how reconcile skips a device that has a deploy in flight (#35).

**A deploy that starts during a removal**: `DeployIntent` and `ReconcileDevices` are left unchanged. In the window that remains, the two writes cannot undo each other:
- Both Sets go through the same `GnmiExecutor` in the one worker, which serializes Sets per dial host (`_set_locks`). SR Linux also runs one exclusive config session at a time.
- A deploy merges intent at `/`. Its payload never names an entity outside intent, and for an interface in intent it names only the intended address. It therefore cannot bring back anything a removal deleted.
- A removal deletes only undesired entities and extra addresses, so it cannot remove anything a deploy writes.
- The removal's verification (R9) looks only for forward drift that wasn't there before. A deploy can only take forward drift away, so it cannot fail a removal's verification.

**Alternatives considered**:
- **Run the removal under `deploy-intent-{device_id}`.** This is true mutual exclusion, and Temporal enforces it. But `snapl deploy` starts with `USE_EXISTING`, so a deploy started during a removal would attach to the removal and get a result of the wrong type. Every reader of that id family would also have to handle a second type. Rejected.
- **Wait for the deploy to finish instead of refusing.** The operator confirmed a plan built before the deploy. Waiting would apply that plan to state nobody has looked at, which is what FR-007 forbids. Rejected.
- **Patch `DeployIntent` so it checks for a running removal.** That changes a workflow that has histories in flight (it would need a patch id) to close a window in which the writes commute anyway. Rejected.
- **A per-device lock workflow.** It is a new mechanism, and the id family and the executor lock already cover the case. Rejected.

## R9: Verification

**Decision**: Right after the Set returns, the removal takes one scan of the device, with no wait. C5 showed that a GET reflects a delete at once, in 10 of 10 rounds. From the pre-delete report and the post-delete report it computes:
- `gone`: each planned delete path under which the new report has no undesired item;
- `remaining`: each planned delete path under which it still has one;
- `new_forward`: forward drift paths that are in the new report but were not in the earlier one.

It passes when `remaining` and `new_forward` are both empty (FR-009). Otherwise the run ends `VERIFICATION_FAILED`, and the result and the audit trail keep the lists. Undesired items the plan refused stay, are not counted against the removal, and are listed as `still_refused`.

**Rationale**: SC-001 is checked against the next scan. A removal that reports "gone" from a scan of its own, taken the same way, gives the operator SC-001's answer straight away. Comparing forward drift before and after the delete answers the spec's question "did intent-managed config change" without asking for a clean device: a device that already had forward drift can still be cleaned of undesired config.

## R10: The record (FR-011, US3)

**Decision**:
- **Audit trail.** `RemoveConfig` and `PlanRemoval` write the usual `WORKFLOW_STARTED`/`WORKFLOW_TERMINATED` events, with `target_id` set to the device. They also write `ACTIVITY_COMPLETED` for each step. A `RemoveConfig` start requires `actor` (the operator, from `snapl remove`, the same `_actor()` that schedule changes use), and its payload includes `confirmed_paths`. The terminal payload holds `deleted_paths`, `device_response`, the verification lists and the reason. An `AuditEvent` validator enforces the actor rule, as it already does for `SCHEDULE_CHANGED`. `snapl audit --device` shows these events through the existing renderer. The renderer gains one line for a removal's terminal event.
- **Dashboard runs.** The run list keys on `workflow_type`, so both types appear without changes. A removal's trigger is `operator`: `_trigger` gives every run that isn't a `DeployIntent` the trigger `operator` unless a schedule started it, and a schedule never starts a removal. Its step records show in the run's step list. `_STEPWISE` stays `{"DeployIntent"}`: a removal's steps are not in the loop strip's intent → executor → collector → observability order, so the strip shows them as not recorded rather than in a wrong order.
- **Episodes.** `EpisodeClose.kind` gains `"removal"`. In `project_timeline`, the one place an episode's closer is chosen, the closers are now successful `DeployIntent` runs and successful `RemoveConfig` runs that targeted the device. A `RemoveConfig` closer has the kind `removal`. An undesired-only episode therefore closes at the next ScanDrift scan that finds the device clean, and the removal is named as what closed it (US3 scenario 2). The designed page and the plain page label it "closed by a removal". The OpenAPI schema and `schema.d.ts` are regenerated.

- **Visibility, not operations (FR-016, added 2026-11-24 with the maintainer).** The dashboard never starts a preview or a removal; it shows them, and shows the command to run. All three additions are projected from the audit trail on the server, so archives and snapshots carry them unchanged.
  - **The command.** `DeviceView` gains `remove_command` (`snapl remove <name>`), set whenever the latest scan found undesired paths. `suggested_command` keeps `snapl reconcile <name>` for forward drift, so a device with both shows both. `UNDESIRED_NOTE` stops saying removal doesn't exist. The Undesired panel and the drawer show the command with the existing `Command` component; the plain page shows it as text.
  - **A removal's detail.** `RunView` gains `removal`, read from a `RemoveConfig` run's start and terminal events: the actor, `confirmed_paths`, `deleted_paths`, `device_response`, the four verification lists and the reason. The run's detail shows it; for a run that wrote nothing it shows the reason in words (plan changed, deploy in flight, rejected by the device).
  - **The latest preview.** `DeviceView` gains `last_removal_preview`: the time, workflow id, `plan_paths` and `refused_paths` of the device's latest successful `PlanRemoval`. The drawer shows "last preview HH:MM: N deletes, M refused" and lists the refused entities, so config left in place by design does not read as config nobody has looked at.
  - Considered and deferred: an "undesired since" age per device, and removal counts in the history views. Both read the same trail and can follow once there is removal history to look at. A Prometheus counter for removals is not added (the metric families are fixed by spec 015).

**Scheduled runs never remove (FR-012)**: `DriftResponse` keeps its current imports and is not given access to `RemoveConfig`. A unit test parses `drift_response.py` and `reconcile_devices.py` and fails if either imports the removal workflow or the `remove_config` activity. That makes VIII.5's "never automatic" a test, not only a convention. `ExclusionReason.UNDESIRED_ONLY` and its text ("removal is never automatic") stay as they are.

## R11: `snapl remove`

**Decision**: `snapl remove <device> [--apply] [--yes] [--json] [--use-case]` (FR-013).
- **Without `--apply`**: it runs `PlanRemoval` and renders the plan. That means each delete path with the drift paths it answers, the refusals with their reasons, a note on forward drift ("N paths are missing or differ; `snapl reconcile` heals those, not this"), and the leftover notes from R4. It exits 0 when there is nothing to remove and 2 when there is (exit 2 already means "drift found", spec 006 FR-011).
- **With `--apply`**: it previews as above. Then it confirms with `_confirm` (prompt, or `--yes`), starts `RemoveConfig` with the shown paths, and renders the result. It exits 0 when the removal is verified and 1 for everything else: plan changed, deploy in flight, rejected by the device, verification failed, or unreachable.
- **No terminal (FR-006)**: when stdin is not a TTY and `--yes` is missing, `--apply` refuses before anything starts. `_confirm` already refuses `--json` without `--yes`. The TTY check is new, and so is its test. It applies only to `remove`, so existing commands keep their behaviour.
- `snapl reconcile` does not change, and still never deletes (FR-013).

## R12: Live evidence

**Decision**: The implementation PR runs SC-001 to SC-005 on leaf-04, the same way the captures were run. The steps are in [quickstart.md](quickstart.md):
1. Hand-add a port description outside intent, a BGP neighbour outside intent, and an extra address on `ethernet-1/49`.
2. Preview twice, checking the running config before and after each preview (SC-002).
3. Change the device at the prompt and confirm (SC-004).
4. Preview and apply again (SC-001), then read the audit trail and the dashboard (SC-005).

A unit test enumerates every path the planner can produce from the fixture reports and checks each one against the guard (SC-003). Captured payloads from the run become fixtures (Principle III).
