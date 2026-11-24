# Feature Specification: Remove Undesired Config

**Feature Branch**: drafted on `ccr-a14a9b05-ogrz9i`; give it its own branch (`013-remove-undesired-config`) when it moves to a PR
**Created**: 2026-11-15
**Status**: Planned 2026-11-24 (`plan.md`). Clarified 2026-11-15; live captures C1 to C5 run on 2026-11-24 (`captures.md`).
**Input**: Issue #65: "apply is merge-only gNMI update at '/' — config removed from intent never leaves the device". Spec 007's deferred User Story 3 ("Converge by removal").

## Context

Every apply is a gNMI `update` at `/`: a merge. It adds and changes, and never removes. Spec 007 (#54) made the other half visible: config on a device that intent does not mention is now reported as **undesired** drift, with its own paths (`undesired_paths` in the audit trail, `undesired` in the dashboard). Nothing acts on it. Today the loop converges for additions and changes only, and the README says so.

The constitution sets the frame (Principle VIII.5): removing undesired config is a destructive change. It is **never automatic, always preceded by `dry_run()`, and never applied to the protected set**. Spec 007 adds that removal is a targeted gNMI `delete` on the undesired entity's own path, never a `replace` at `/` (spec 007 FR-011), and that detection and removal share one protected set (spec 007 FR-005).

This spec covers the execution half: an operator previews what would be removed from a device, confirms, and snapl removes exactly that, records it and verifies it.

### Why a spec and not a patch

It is a new kind of write to a device (the constitution's test for a spec), the first one that deletes. A wrong path in a delete can take a device's interfaces or its management session with it. The exact gNMI delete paths SR Linux accepts, and what each leaves behind, are not yet known from the lab.

## Clarifications

### Session 2026-11-15

- Q: The removal entry point: a new command, or an option on reconcile? → A: A new command, `snapl remove <device>`, previewing by default, with `--apply` to execute (FR-013).
- Q: The first cut's scope: whole undesired entities and extra addresses only, or also undesired fields on intent-managed entities? → A: Whole entities and extra addresses only (FR-014).
- Q: All of a device's plan or a chosen subset? → A: All or nothing per device (FR-015).

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Preview What Would Be Removed (Priority: P1)

An operator sees a device reported with undesired config and asks what removing it would do. snapl shows, for that device, each delete it would issue (the exact path), the drift item each one answers, and anything it would refuse to touch and why. Nothing is written.

**Why this priority**: VIII.5 requires a dry run before any removal. The preview is also useful alone: it turns "undesired config exists" into "these are the exact deletes that would fix it", which an operator can act on by hand.

**Independent Test**: Hand-add a description to a port outside intent and a BGP neighbour outside intent on one lab device, scan, run the preview. It lists one delete per undesired entity, names no protected entity, and the device's running config is byte-identical before and after.

**Acceptance Scenarios**:

1. **Given** a device whose latest scan reports undesired config, **When** the operator previews removal, **Then** snapl lists each delete path and the drift paths it answers, and makes no device write.
2. **Given** a device with no undesired config, **When** the operator previews removal, **Then** snapl says there is nothing to remove and makes no device write.
3. **Given** a device whose undesired items include an entity in the protected set, **When** the operator previews removal, **Then** that entity appears as refused, with the reason, and no delete is planned for it.
4. **Given** a device with forward drift (missing or differing config) as well as undesired config, **When** the operator previews removal, **Then** only the undesired config is planned for removal, and the preview says the forward drift is healed by a reconcile, not by this.

---

### User Story 2 — Remove It, Confirmed (Priority: P1)

The operator confirms the preview. snapl issues exactly the deletes it showed, then scans the device again and reports whether the undesired config is gone.

**Why this priority**: This is #65. Without it, "converge to intent" stays true for additions and changes only.

**Independent Test**: From User Story 1's state, confirm the removal. The next scan of that device reports no undesired items, the rest of its config is unchanged (its intent-managed interfaces and sessions still compare clean), and the audit trail records each delete.

**Acceptance Scenarios**:

1. **Given** a confirmed preview, **When** the removal runs, **Then** snapl issues the planned deletes and nothing else, then verifies with a fresh scan of that device.
2. **Given** the device's config changed between the preview and the confirmation (a new scan's plan differs from the one shown), **When** the removal would run, **Then** snapl refuses, shows the new plan, and writes nothing.
3. **Given** a delete the device rejects, **When** the removal runs, **Then** snapl stops, reports which delete failed with the device's reason, and records what was and was not removed.
4. **Given** a deploy to the same device in progress, **When** the operator starts a removal, **Then** the removal never runs alongside it: per-device writes stay serialized, as for deploys. Whether it waits or refuses is a planning decision (reconcile skips a device with a deploy in flight, #35).
5. **Given** an operator without a terminal (scripted use), **When** the removal is started without an explicit confirmation flag, **Then** snapl refuses and writes nothing.

---

### User Story 3 — See Removals in the Record (Priority: P2)

Every removal shows in `snapl audit`, the dashboard's run list and the device's history, told apart from deploys: who confirmed it, what was deleted, and what the verifying scan found.

**Why this priority**: A delete is the write an operator most needs to account for afterwards. The audit trail and the dashboard already carry deploys; a removal that does not show there is invisible history.

**Independent Test**: After User Story 2, `snapl audit --device <name>` lists the removal with its deletes and outcome, and the dashboard shows it as its own run kind, with the drift episode it closed attributed to it.

**Acceptance Scenarios**:

1. **Given** a completed removal, **When** the operator reads the device's audit trail, **Then** it shows the removal, the actor, each delete path and the verification result.
2. **Given** a removal that closed an undesired-only drift episode, **When** the dashboard shows that episode, **Then** what closed it is the removal, not `unknown`.
3. **Given** a device with undesired config, **When** the operator opens it on the dashboard, **Then** it shows `snapl remove <device>` as a command to copy, beside `snapl reconcile <device>` when intent is also missing from the device.
4. **Given** a completed removal, **When** the operator opens that run on the dashboard, **Then** it shows the paths deleted, the device's response, what the verifying scan found, and who confirmed it; for a removal that wrote nothing, it says why.
5. **Given** a device that has been previewed, **When** the operator opens it on the dashboard, **Then** it shows the latest preview's time, its number of deletes and the entities it refused, so config left on the device by design is told apart from config nobody has looked at.

*Scenarios 3 to 5 added 2026-11-24 (maintainer). The dashboard is for visibility only: it shows removals and the command to run, and never starts one (FR-016).*

---

### Edge Cases

- **The protected set.** `mgmt0` and `system0` are never planned for removal, whatever the scan says, and the preview says so. The list is the one spec 007 defined next to the entity field map, not a copy.
- **An undesired port that only carries `enabled: true`.** A leaf-level delete of the listed fields would leave `enabled`, and the port would still read as undesired, so the delete targets the interface entry. Whether deleting the entry returns the port to the unconfigured state the scan ignores (disabled, no config) is capture C2. If SR Linux leaves defaults behind that the diff reads as config, this edge case needs another answer before User Story 2 can pass. See Q2. *C2 (2026-11-24): it does not. A deleted entry is absent from config, its state reads only `admin-state disable`, and the next scan is clean.*
- **An extra address on an intent-managed interface (#193).** The interface is wanted; only the address is not. The delete targets the address under its subinterface, never the interface.
- **A neighbour outside intent that is the BGP session the operator is logged in over.** The management session uses `mgmt0`, not BGP, so removing a BGP neighbour cannot cut snapl off. A neighbour removal can still drop traffic; the preview says that removing a neighbour tears its session down.
- **The device is unreachable at confirmation time.** Nothing is removed; the error says which dial failed (#194).
- **The scan the preview was built on is old.** Removal always re-scans first (User Story 2, scenario 2), so a plan is never applied to state nobody looked at recently.
- **The schedule is in remediate.** Scheduled runs never remove (VIII.5: never automatic). A removal is only ever operator-started, whatever the schedule's response.
- **The device has left intent.** It has no intent to compare against, so nothing about it is "undesired". Removal refuses: decommissioning a device is a different operation.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The System MUST offer a removal preview for one device that makes no device write and lists, for each planned delete, the exact gNMI path and the drift paths it answers.
- **FR-002**: The preview MUST be computed from a scan taken at the time of the preview, not from an earlier stored scan.
- **FR-003**: The System MUST plan removals only for drift items marked undesired. Forward drift MUST NOT produce a delete.
- **FR-004**: The System MUST NOT plan, and the Executor MUST NOT issue, a delete on any entity in the protected set. The protected set MUST be the one defined in the entity field map (spec 007 FR-005). A test MUST hold detection and removal to the same list.
- **FR-005**: Every delete MUST be scoped to one undesired entity's path or a leaf under it. A delete or `replace` at `/`, or at a container above an entity (`/interface`, `/network-instance[name=default]/protocols/bgp`), is prohibited.
- **FR-006**: Removal MUST run only after an explicit operator confirmation of the plan it executes. Without a terminal, it MUST require an explicit flag, as `reconcile --yes` does.
- **FR-007**: Before writing, the System MUST re-scan the device and compare the new plan with the confirmed one. If they differ, it MUST write nothing and show the new plan.
- **FR-008**: The deletes for one device MUST be sent in one gNMI Set request, so the device applies all or none of them.
- **FR-009**: After the deletes, the System MUST scan the device again and report whether each planned entity is gone, and whether any intent-managed config changed.
- **FR-010**: A removal MUST be serialized with deploys to the same device (the per-device workflow id family) and MUST NOT run while a deploy to it is in flight.
- **FR-011**: A removal MUST be recorded in the audit trail as its own workflow type, with the actor, the planned paths, the device's response and the verification result.
- **FR-012**: Scheduled runs (spec 009) MUST NOT remove config, in any response mode.
- **FR-013**: The removal entry point MUST be a command of its own, `snapl remove <device>`, which previews by default and executes only with `--apply`. `snapl reconcile` stays a merge and never deletes.
- **FR-014**: The first cut MUST remove whole undesired entities (an interface entry outside intent, a BGP neighbour outside intent) and extra addresses on intent-managed interfaces (#193). Individual undesired fields on an entity intent still manages (a description removed from intent, for example) are out of scope for this cut. *Amended 2026-11-24 (maintainer, from C1): the whole entities include a subinterface's binding to the default network instance (`routed_interface`, #315), which must be deleted in the same Set as its port; a routing policy outside intent (#317) is listed as refused, not removed (research R3).*
- **FR-015**: A removal MUST apply a device's whole plan or nothing. The operator confirms the plan as shown and cannot pick a subset of it in this cut.
- **FR-016**: The dashboard MUST show removals and previews read-only: the `snapl remove <device>` command to copy for a device with undesired config, each removal's deletes, response, verification and actor, and each device's latest preview. It MUST NOT offer any way to start a preview or a removal. *Added 2026-11-24 (maintainer): the dashboard is for visibility, not operations.*

### Key Entities

- **Removal Plan**: For one device at one moment: the scan it was built from, the planned deletes (path and the drift paths each answers) and the refused entities with reasons. It is what the operator confirms, and what the re-scan is compared with (FR-007).
- **Planned Delete**: One gNMI delete path and the undesired drift paths it answers. Its path is an entity path or a leaf under one, never above (FR-005).
- **Removal Result**: The deletes sent, the device's response, and the verifying scan's verdict per planned entity.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On the dcfabric lab, with a hand-added port description outside intent, a hand-added BGP neighbour outside intent and an extra address on an intent-managed interface (#193) on one leaf, one confirmed removal leaves that leaf's next scan with no undesired items and its intent-managed config still clean.
- **SC-002**: Across the same run, the preview causes zero device writes: the device's running config is identical before and after it.
- **SC-003**: No test, preview or removal issues a delete at `/`, at a container above an entity, or on a protected entity. A unit test enforces it over every path the planner can produce.
- **SC-004**: A removal whose device changed between preview and confirmation writes nothing (checked by changing the device in between).
- **SC-005**: Every removal appears in `snapl audit --device` and the dashboard's run list with its deletes and verification result. *Extended 2026-11-24:* the dashboard also shows the remove command for a device with undesired config, and the device's latest preview (FR-016).

## Assumptions

- Spec 007's detection is correct for the entities it reports. A removal is only as safe as the undesired items it is built from; the protected set and value-bearing-field rules carry over unchanged.
- The deletes go in one gNMI Set request with only `delete` entries. SR Linux applies a Set as one transaction. *Confirmed by C1 (2026-11-24): a Set with one rejected delete changes nothing, and the order of deletes inside a Set does not matter.*
- Deleting an interface entry returns the port to the state the scan already ignores (disabled, no config). *Confirmed by C2 (2026-11-24).*
- `dry_run()` in the Executor ABC grows a removal counterpart, or the plan is computed outside the Executor and only the Set is the Executor's. The plan decides; the constitution only requires that a dry run precedes the write.
- Single vendor. Delete paths are vendor-specific and live in the vendor seams (the drift field map or the gNMI executor), never in workflows.

## Out of Scope

- `replace` semantics on any subtree.
- Removal from a schedule, in any mode.
- Decommissioning a device that has left intent.
- Any other entity kind than interfaces, their IPv4 addresses, their default network-instance bindings and BGP neighbours. Routing policies (#317) are detected but not removed in this cut (research R3).
- Config the scan does not report and a removal leaves behind: a peer group with no neighbours left, a subinterface's `ipv4 admin-state` (captures C3, C4; research R4).
- Promoting undesired config into intent (VIII.1: an explicit, reviewed operation, not this one).

## Questions for planning (research, not decisions)

These need the lab and belong in `research.md`. They do not block the spec. *All five were answered on the lab on 2026-11-24; see `captures.md`, which also raises two questions for the plan (the `routed_interface` binding, and leftovers the scan does not see).*

- **C1**: Does one SR Linux Set with several `delete` paths apply atomically, and what does a partial rejection return?
- **C2**: What does `/interface[name=ethernet-1/7]` look like in a gNMI GET after a delete of its entry: absent, or present with defaults the diff would read as config?
- **C3**: Is a delete of `/interface[name=X]/subinterface[index=0]/ipv4/address[ip-prefix=…]` accepted with the prefix as written in the path (the `/` inside the key), or does it need escaping?
- **C4**: Does deleting a BGP neighbour need anything else removed with it (a peer group left with no members, a policy reference)?
- **C5**: How long after a delete does a GET reflect it? The verifying scan must not race the device.

## Dependencies

- Spec 007 (detection, the protected set, `undesired` on drift items).
- #193 (extra addresses on intent-managed interfaces are reported with their own path).
- Spec 006 (the CLI's confirmation conventions), spec 005 (workflows and the audit trail), spec 011 (dashboard run kinds and episode closing).
