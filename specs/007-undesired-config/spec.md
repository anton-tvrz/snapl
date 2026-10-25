# Feature Specification: Undesired Config — Ownership, Detection, and Removal

**Feature Branch**: `007-undesired-config`
**Created**: 2026-08-01
**Status**: Draft
**Input**: Issues #54 (drift diff is blind to extra live config) and #65 (apply is merge-only; config removed from intent never leaves the device)

## Context

snapl's closed loop converges intent in one direction only. It can add config and change config; it cannot take config away, and it cannot see config it never asked for.

- The diff (`diff_desired_vs_actual`) iterates **desired** entities only. An interface or BGP neighbour present on the device but absent from intent produces zero drift — the report is CLEAN (#54).
- Every apply issues a gNMI SET **update** (merge) at `/`. Merge never removes. Delete an interface from intent, redeploy, and the stale config stays on the device forever (#65).

Together these mean de-provisioning is entirely outside the loop: not executed, not observed, not reported. "We converge to intent" is true only for additions and changes, which `docs/demo-scenarios.md` currently names as a known limitation rather than fixing.

### Why this needs a specification rather than a patch

Both issues have an obvious naive implementation, and both are dangerous. Measured against the live dcfabric fabric, `spine-01` reports **31 interfaces that intent does not name**:

| interface | in intent | device reports |
| --- | --- | --- |
| `ethernet-1/1` | yes | `description="to leaf-01:ethernet-1/49"`, `ip=10.10.1.0/31`, `enabled=true`, `mtu=9214` |
| `ethernet-1/7` | **no** | `description=null`, `ip=null`, `prefix_length=null`, `enabled=false`, `mtu=null` |
| `mgmt0` | **no** | `ip=172.20.21.11/24`, `enabled=true`, `mtu=1514` |

Thirty of those extras are unconfigured physical ports: SR Linux reports every port the chassis has, whether or not anyone configured it. Flagging all extras would emit 31 drift items per spine on every scan — the fabric would never read CLEAN again, and the signal would be buried under hardware inventory. Removing all extras would delete `mgmt0`, whose address is the one snapl dials over, and the device would be unreachable permanently.

The distinction that makes this tractable: **a bare chassis port carries no values, while real config sprawl does.** `ethernet-1/7` is all nulls; a hand-added IP on `ethernet-1/7` would not be. `mgmt0` is the exception that proves the rule — it is shape-identical to legitimate config and can only be excluded by name.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — See Config Nobody Asked For (Priority: P1)

An operator adds an IP address to `ethernet-1/7` by hand to test something, and forgets it. It is not in the Source of Truth. Today every scan reports the fabric CLEAN.

**Why this priority**: This is the detection half, and it is a precondition for removal — nothing should be deleted on the strength of a diff that has never been trusted in production.

**Acceptance Scenarios**:

1. **Given** a device whose intent does not name `ethernet-1/7`, **When** that interface carries a description or an IP address on the device, **Then** the scan reports the device DRIFTED and names the undesired path with `desired=None` and the observed value as `actual`.
2. **Given** a fully converged fabric with no hand-added config, **When** a scan runs, **Then** every device reports CLEAN — the unconfigured chassis ports produce no drift items.
3. **Given** a device with a BGP neighbour configured that intent does not name, **When** a scan runs, **Then** that neighbour is reported as undesired.
4. **Given** a device's `mgmt0` interface, **When** a scan runs, **Then** `mgmt0` is never reported as undesired regardless of what it carries.

---

### User Story 2 — Distinguish Missing From Undesired (Priority: P1)

An operator reading a drift report needs to know which way the difference runs: is intent asking for something the device lacks, or does the device carry something intent never asked for? The remediation differs — one is an apply, the other a delete.

**Why this priority**: A report that conflates the two is actively misleading once removal exists, and the CLI renders these paths on stage.

**Acceptance Scenarios**:

1. **Given** a drift report containing both kinds of difference, **When** it is rendered, **Then** an undesired entity is distinguishable from a missing or mismatched one without inspecting values by eye.
2. **Given** an undesired entity, **When** its drift items are produced, **Then** `desired` is `None` and `actual` carries the observed value.

---

### User Story 3 — Converge By Removal (Priority: P2, deferred)

An operator deletes an interface from the Source of Truth and redeploys. The interface's config leaves the device.

**Why this priority**: This is #65, the execution half. It is specified here so the ownership model is designed once, but it is **explicitly out of scope for the first implementation round** — detection must be proven against the live fabric before anything deletes.

**Acceptance Scenarios**:

1. **Given** an entity reported as undesired, **When** the operator reconciles the device, **Then** the undesired config is removed with a targeted gNMI `delete` on that entity's path.
2. **Given** any protected entity, **When** a reconcile runs, **Then** no delete is ever issued against it.
3. **Given** a removal, **When** it is executed, **Then** it is a targeted delete on the entity subtree — never a `replace` at `/`, which would take the gRPC server config with it and end the session.

---

### Edge Cases

- An interface exists on the device with **only** `enabled=false` and no other values — the unconfigured-port shape. MUST NOT be flagged.
- An interface exists with `enabled=true` and nothing else. `enabled` alone is not evidence of intent-worthy configuration on a port that may simply be up; it MUST NOT be flagged on its own.
- A protected interface carries obviously undesired config. It is still not flagged — protection outranks detection, and the operator is told through documentation, not through a drift item they cannot action safely.
- The device reports an entity whose key cannot be parsed. It is ignored rather than treated as undesired; an unreadable key is not evidence of sprawl.
- Intent and device agree that an entity should exist, but every managed field is null on both sides. No drift, in either direction.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The System MUST compare actual state against desired state in both directions: entities desired but missing or differing (existing behaviour), and entities present on the device but absent from intent (new).
- **FR-002**: The System MUST treat an actual entity as **undesired** only when it carries at least one managed field with a non-default value. An entity whose managed fields are all unset MUST NOT be reported.
- **FR-003**: The System MUST NOT treat `enabled` alone as evidence that an entity is configured. Only the value-bearing fields (`description`, `ip_address`, `prefix_length`, `mtu` for interfaces; `peer_asn`, `peer_group` for BGP neighbours) qualify an entity as configured.
- **FR-004**: The System MUST maintain a **protected set** of entity keys that are never reported as undesired and never removed, containing at minimum the management interface (`mgmt0`) and the system interface (`system0`).
- **FR-005**: The protected set MUST be defined once, next to the entity field map it belongs to, and MUST be consulted by both detection and (when it lands) removal. Two independent lists that can drift apart are prohibited.
- **FR-006**: The System MUST emit undesired findings as `DriftItem` entries with `desired=None` and `actual` set to the observed value, so existing consumers need no new model.
- **FR-007**: The System MUST make an undesired entity distinguishable from a missing or mismatched one in the drift report, without requiring the reader to infer direction from `desired=None` alone.
- **FR-008**: The System MUST keep the diff a pure function of desired state and normalized actual data. No device access, no I/O, no ordering dependence on collection.
- **FR-009**: The System MUST produce a deterministic ordering of drift items, so two scans of an unchanged device yield identical reports.
- **FR-010**: The System MUST NOT change the meaning of an existing CLEAN report for a fabric that has no undesired config. A converged fabric that reads CLEAN today MUST still read CLEAN.
- **FR-011**: Removal (User Story 3) MUST be implemented as targeted gNMI `delete` operations scoped to the undesired entity's own path. A `replace` at `/` is prohibited.
- **FR-012**: Removal MUST NOT be enabled implicitly by this feature's detection half. Detection landing MUST NOT cause any device write.

### Key Entities

- **Managed Field**: A field the executor's templates actually render, and therefore one snapl can be held responsible for. Already enumerated per entity in `ENTITY_FIELD_MAP`.
- **Value-Bearing Field**: The subset of managed fields whose presence indicates deliberate configuration, excluding `enabled`. The test for "is this entity configured at all?"
- **Protected Entity**: An entity key snapl never reports as undesired and never deletes, because losing it costs reachability or the device's identity.
- **Undesired Entity**: An actual entity, not protected, matching a managed entity kind's path shape, absent from intent, carrying at least one value-bearing field.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A converged six-device dcfabric fabric scans CLEAN — 0 drifted, 0 items — despite each spine reporting ~31 interfaces absent from intent.
- **SC-002**: An IP address added by hand to an interface not named in intent is reported as drift on the next scan, naming that interface's path.
- **SC-003**: `mgmt0` is never present in any drift report, on any device, in any of the above.
- **SC-004**: A reader of a drift report can tell an undesired entity from a missing one.
- **SC-005**: Detection adds no device writes: a scan remains read-only, verified by the absence of any gNMI SET during a scan.
- **SC-006**: The scan's wall-clock duration on the six-device fabric is not measurably worse than before (the comparison is over data already collected).

## Assumptions

- The normalized `actual_data` contract is unchanged: a dict keyed by per-entity path strings, values flat dicts of snake_case managed fields. Undesired detection reads the same structure the existing diff reads, so it needs no collector change.
- `DRIFT_PATHS` already collects the full `/interface` container and the BGP neighbour list, so undesired entities are present in collected data today. No new gNMI paths are required.
- The protected set is a property of the platform (SR Linux), not of a use case. `dcfabric` and any future use case on the same platform share it.
- Removal is deferred, so this round's blast radius is a report that says more. Nothing this feature adds can change a device.

## Dependencies

- Issue #54 — the detection half, delivered by this feature.
- Issue #65 — the removal half, specified here and deferred to a follow-on round.
- Issue #36 — `rollback()` is roll-forward, and inherits the same blindness; out of scope here but resolved by the same ownership model.
