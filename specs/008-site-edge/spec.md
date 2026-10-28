# Feature Specification: Site Edge — A Segmentation Rule as Intent

**Feature Branch**: `008-site-edge`
**Created**: 2026-10-28
**Status**: Draft. Written now, built later: implementation starts only after the dcfabric MVP epic (#103) ships. This is the second use case, not part of the first demo.
**Input**: Issue #117. A manufacturing site edge on SR Linux with office and OT segments and a WAN uplink over eBGP to a carrier router. Intent includes a segmentation rule: the OT segment cannot reach the WAN except one named subnet. Drift on that rule is detected and reconciled like any other drift.

## Context

snapl proves its closed loop on one use case, a datacenter fabric. Its strongest property is Principle VIII.1: a manual change that "works" is still drift. So far snapl has only shown this on interface and BGP config. It has never shown it on a **security control**, which is where it matters most. A firewall rule removed by hand to "just get something working" is the classic case of drift that nobody notices until an audit or an incident.

The constitution already lists Site Edge as the next use case. This feature makes it real. It is deliberately small:

- **One site edge router** with three segments: office, OT (operational technology) and a WAN uplink.
- **A carrier router** reached over eBGP. It stands in for the MPLS or SD-WAN underlay.
- **One segmentation rule**: OT traffic may reach one named subnet across the WAN (for example a historian or MES server) and nothing else. It is rendered as an access-control list on the site edge.

Interfaces and eBGP config already run end to end on dcfabric. The new capability is the **access-control list (ACL) as an intent entity**. That means it is modelled in the Source of Truth, rendered and deployed, collected back, compared field by field, and reported when it differs. Every one of those is a system boundary snapl has not crossed before.

### What this feature lets snapl claim, and what it does not

| Claim | After this feature |
| --- | --- |
| A segmentation rule is held as intent in the Source of Truth and deployed to an SR Linux site edge | Yes |
| Removing or weakening that rule by hand is reported as drift, naming the rule and the field | Yes |
| Reconcile restores a removed or altered rule | Yes |
| A permit rule inserted by hand ahead of the deny is reported as drift | Yes |
| Reconcile removes a permit rule inserted by hand | Depends on Q3 (FR-016) |
| The rule actually blocks OT traffic in the lab | Only if Q2 brings the traffic test into scope (User Story 5) |
| Multi-vendor, firewall or zone-policy support | No. Single vendor, one interface ACL (Principle VI) |

## User Scenarios & Testing *(mandatory)*

### User Story 1: Declare and Deploy the Segmentation Rule (Priority: P1)

A network engineer at a manufacturing site wants the OT segment fenced off from the WAN, except for the one server the plant floor must report to. They record the rule in the Source of Truth as part of the site edge's intent. Then they run the normal deploy for the site edge. The rule reaches the device bound to the OT segment, verification passes, and a scan of the use case reads clean.

**Why this priority**: Nothing else in this feature exists without it. It is also the first time snapl's intent model carries anything other than interfaces and BGP. So it proves the new entity end to end: in the Source of Truth, through rendering, on the device, and back through collection to a clean comparison.

**Independent Test**: Seed the site_edge use case. Deploy the site edge on the lab. Confirm that the deploy succeeds, that verification reports no drift, and that a use-case scan reports every device clean.

**Acceptance Scenarios**:

1. **Given** a seeded site_edge use case and a freshly deployed lab, **When** the operator deploys the site edge, **Then** the deploy succeeds and its verification step reports no drift for the ACL entries, the ACL binding, the interfaces or the eBGP session.
2. **Given** a deployed site edge, **When** the operator scans the site_edge use case, **Then** every device reports clean.
3. **Given** the deployed rule, **When** the operator reads the device's ACL config, **Then** the rule's entries appear in the order intent declares, bound to the OT segment's interface in the direction intent declares.
4. **Given** the same intent deployed twice, **When** the second deploy completes, **Then** the device's state is unchanged (Principle VIII.4).

---

### User Story 2: A Deny Rule Removed by Hand Is Drift, and Reconcile Restores It (Priority: P1)

Someone on site deletes the deny rule from the site edge by hand, perhaps to troubleshoot a connection from the plant floor. Nothing breaks, and from their side the change "works". The next scan reports the site edge as drifted and names the missing rule. The operator reconciles. The rule is back on the device, and the following scan is clean.

**Why this priority**: This is the demo beat in #117 and the point of the feature. It shows Principle VIII.1 on a security control.

**Independent Test**: Deploy a converged site edge. Delete the deny entry on the device by hand. Run a scan, reconcile the site edge, then scan again. The first scan reports the deny entry missing. The second is clean.

**Acceptance Scenarios**:

1. **Given** a converged site edge, **When** the deny entry is deleted on the device and the use case is scanned, **Then** the site edge reports drifted, and the report names the deny entry's path with each managed field's desired value and an absent actual value.
2. **Given** that drifted site edge, **When** the operator reconciles it, **Then** the deny entry is restored with every managed field matching intent, and reconcile reports the device succeeded.
3. **Given** the reconciled site edge, **When** the use case is scanned again, **Then** every device reports clean.
4. **Given** the drift in scenario 1, **When** the scan completes, **Then** nothing has been written to any device (scans stay read-only).

---

### User Story 3: A Rule Weakened in Place Is Drift (Priority: P2)

A deny rule can be defeated without deleting it. Its action can be flipped to accept, its matched prefix narrowed, the allowed subnet widened, or the whole ACL detached from the OT interface. Each of these is reported as drift on the next scan, naming the field that changed, and reconcile puts each one back.

**Why this priority**: Without it, the only bypass caught would be the most obvious one. P2 because it reuses User Story 2's mechanics and only extends which fields are compared.

**Independent Test**: For each kind of in-place change below, start from a converged site edge, make the change by hand, scan, reconcile and scan again.

**Acceptance Scenarios**:

1. **Given** a converged site edge, **When** the deny entry's action is changed to accept by hand, **Then** the scan names the entry's action field with desired "drop" and actual "accept", and reconcile restores "drop".
2. **Given** a converged site edge, **When** the allowed entry's destination prefix is widened by hand, **Then** the scan names that entry's destination prefix with both values, and reconcile restores the intended prefix.
3. **Given** a converged site edge, **When** the ACL is unbound from the OT interface by hand, **Then** the scan reports the binding as missing, and reconcile restores it.
4. **Given** a converged site edge, **When** the ACL is bound to a different interface by hand in addition to OT, **Then** the scan reports the extra binding as undesired.

---

### User Story 4: A Permit Rule Inserted by Hand Is Reported (Priority: P2)

The more realistic bypass does not touch the deny rule at all. Someone adds a new "accept everything" entry ahead of it, so the deny never matches. The next scan reports the inserted entry as undesired: config the device carries that intent never asked for (spec 007). A reader can tell it apart from a missing rule.

**Why this priority**: It is the bypass a careful insider would actually use. Detecting it costs little because spec 007 already built undesired detection. What reconcile then does about it is the open scope question in FR-016.

**Independent Test**: Deploy a converged site edge. Insert an accept entry with a lower sequence number than the deny entry. Scan, and confirm the report names the inserted entry as undesired. Then reconcile and record the outcome FR-016 settles on.

**Acceptance Scenarios**:

1. **Given** a converged site edge, **When** an accept entry is inserted ahead of the deny entry by hand, **Then** the scan reports the site edge drifted and marks the inserted entry's fields as undesired.
2. **Given** the same report, **When** it is rendered, **Then** the inserted entry is visibly distinct from a missing or mismatched rule, with no need to compare values by eye (spec 007 FR-007).
3. **Given** the drifted site edge, **When** the operator reconciles it, **Then** the outcome is what FR-016 specifies, and the reconcile summary and the demo documentation say so plainly.

---

### User Story 5: Behavioural Evidence That the Rule Blocks Traffic (Priority: P3, stretch)

Config that matches intent is not the same as a control that works (Principle VIII.3). With traffic endpoints in the lab, verification shows the effect of the rule as well as its presence. A host on the OT segment can reach the allowed subnet across the WAN but nothing else across the WAN. After the deny rule is removed by hand, the blocked traffic gets through. After reconcile, it is blocked again.

**Why this priority**: It is the strongest form of the claim, and #117 lists it as optional. Its cost is real: routed forwarding through the site edge and carrier, and confirmation that the lab's virtual dataplane enforces interface ACLs. Whether it belongs in the first cut is open (FR-017).

**Independent Test**: Use a lab with hosts on the OT segment and behind the carrier. Run the traffic checks against a converged site edge, again after removing the deny rule, and again after reconcile.

**Acceptance Scenarios**:

1. **Given** a converged site edge, **When** an OT host sends traffic to the allowed subnet, **Then** it is delivered.
2. **Given** a converged site edge, **When** an OT host sends traffic to a WAN destination outside the allowed subnet, **Then** it is not delivered.
3. **Given** the deny rule removed by hand, **When** the blocked check is repeated, **Then** the traffic is delivered. The bypass is real, not only a config difference.
4. **Given** the site edge reconciled, **When** the blocked check is repeated, **Then** the traffic is not delivered again.

---

### Edge Cases

- **Factory ACL content.** A factory-fresh SR Linux node may carry ACL config of its own, such as control-plane protection filters. It must never be reported as undesired. That would put drift on every dcfabric device on every scan, and those filters protect the device's own reachability. They belong with `mgmt0` in the protected set.
- **A dcfabric device has no ACL in intent.** Collecting ACL state from it must produce no drift items. A converged dcfabric fabric that scans clean today still scans clean (SC-005).
- **Entry order is the rule's meaning.** Two ACLs with the same entries in a different order are different rules. Entries are identified by their sequence number. The same entry under a different sequence number counts as one entry missing and one undesired, not as a match.
- **An ACL entry with no source or destination prefix** matches everything. An unset prefix in intent means "match any". Comparing it against an unset prefix on the device is not drift.
- **A prefix added by hand to a match that intent leaves unset** narrows the entry. It is reported as drift. Merge-only apply cannot take the added prefix away, though (#65), so reconcile would not heal it. To keep every in-place weakening of the lab's rule healable, the lab's seeded rule spells out "any" as an explicit all-addresses prefix rather than leaving it unset. Live capture confirms the device keeps it as written.
- **The deny entry's action flipped by hand.** On the device, accept and drop are mutually exclusive choices. Reconcile must leave exactly one action, drop, on the entry, not both (to be confirmed live; see the plan's capture list).
- **A hand-added match criterion that snapl does not manage** (for example a protocol or port added to the deny entry, which narrows it). It is not compared in the first cut, so it is not detected. This is a known limitation and is documented as one (see Assumptions).
- **The ACL bound to the OT interface in the wrong direction** (egress instead of ingress). This reads as the intended ingress binding missing. The egress binding itself is not compared in the first cut, because only ingress is managed.
- **The ACL bound by hand to an interface where intent declares no binding.** This is reported as an undesired binding. It is not a mismatch on that interface, so it never blocks verification.
- **The site edge is unreachable.** The scan reports it as errored, as for any device. The carrier is still evaluated.

## Requirements *(mandatory)*

### Functional Requirements

**Intent**

- **FR-001**: The System MUST let a use case's intent declare, per device, one or more named access-control lists. Each has an ordered set of entries, and each entry has a sequence number, an action (accept or drop), an optional source prefix, an optional destination prefix and an optional description.
- **FR-002**: The System MUST let intent declare which interface an access-control list is bound to, and in which direction. The first cut supports ingress only.
- **FR-003**: The System MUST hold the segmentation rule as intent data in the Source of Truth, seeded like every other site_edge entity. It MUST NOT be hardcoded in templates, lab config or code.
- **FR-004**: The System MUST provide a self-contained `site_edge` use case: seed data, lab topology and rendering. It MUST be seedable into the same Source of Truth as dcfabric without colliding with dcfabric's devices, addresses, ASNs or lab management network.
- **FR-005**: The System MUST keep `dcfabric` intent and behaviour unchanged. A dcfabric device's intent has no access-control lists, and needs none.

**Deploy**

- **FR-006**: The System MUST render a site edge's access-control lists and their bindings into the device's configuration alongside its interfaces and eBGP, and deploy them through the existing deploy path.
- **FR-007**: Deploying a site edge's access-control lists MUST be idempotent (Principle VIII.4).
- **FR-008**: Deploying MUST restore every managed field of a snapl-declared entry or binding that was deleted or changed on the device.

**Detect**

- **FR-009**: The System MUST compare every managed field of every declared access-control-list entry and binding against the device, and report each difference as a drift item naming the entry or binding and the field.
- **FR-010**: The System MUST report an access-control-list entry or binding present on the device but absent from intent as undesired, using spec 007's undesired-config contract.
- **FR-011**: The System MUST maintain a protected set of access-control config that is never reported as undesired and never removed. It holds at minimum any filters the device ships with to protect its own control plane. The live baseline capture decides the exact membership. Like the interface protected set (spec 007 FR-005), it is defined once, next to the field map.
- **FR-012**: The System MUST produce no access-control drift items for a device whose intent declares no access-control lists and whose ACL config is only factory content.
- **FR-013**: Collecting access-control state MUST NOT write to any device. Scans stay read-only.

**Lab**

- **FR-014**: The System MUST provide a lab for the site_edge use case. It contains one SR Linux site edge with an office segment, an OT segment and a WAN uplink over eBGP to [NEEDS CLARIFICATION: one carrier router, or two (the site edge dual-homed to two carriers)?]. Every router in it is SR Linux.
- **FR-015**: The site_edge lab MUST be able to run on the same host as the dcfabric lab without sharing its management network or address block.

**Scope decisions**

- **FR-016**: When an access-control-list entry is present on the device but absent from intent, inside an ACL that snapl declares (the hand-inserted permit of User Story 4), reconcile MUST [NEEDS CLARIFICATION: remove it in this feature? That would pull targeted removal of undesired config forward from #65 for snapl-owned ACLs only: dry-run first, operator-confirmed, never against the protected set (Principle VIII.5). Or only report it, leaving removal to #65?].
- **FR-017**: Verification of the site_edge use case MUST [NEEDS CLARIFICATION: include the behavioural traffic test of User Story 5 (Linux hosts in the lab; OT can reach the allowed subnet and nothing else across the WAN), or is the first cut config-level only, with User Story 5 as a follow-on?].

**Documentation**

- **FR-018**: The demo documentation MUST include a site_edge scenario that runs User Story 2 end to end with `snapl` commands. It MUST state which bypasses are detected, which are healed and which are known limitations, in the constitution's "claim only what is demonstrated" terms.

### Key Entities

- **Access-Control List**: A named, ordered set of rules on one device. It belongs to that device's intent. Its order is part of its meaning.
- **Access-Control Entry**: One rule in a list, identified within its list by its sequence number. Managed fields: action, source prefix, destination prefix, description.
- **Access-Control Binding**: The attachment of a list to an interface in a direction. Detaching or moving it defeats the rule as completely as deleting it, so it is compared like any other entity.
- **Segmentation Rule**: The operator's view: "OT may reach X and nothing else across the WAN." In this feature it is expressed directly as an access-control list plus its binding, not as a higher-level policy that is compiled into one (see Assumptions).
- **Protected ACL Config**: Device-owned access-control config, such as control-plane protection, that snapl never reports and never touches.
- **Site Edge / Carrier**: The two router roles in the site_edge use case. Both are SR Linux and both are modelled in intent.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a freshly deployed site_edge lab, deploying intent and then scanning reads clean: 0 drifted devices and 0 drift items.
- **SC-002**: After the deny entry is deleted by hand, the next scan reports the site edge drifted and names the deny entry. After one reconcile, the next scan is clean. The whole arc takes no more than 4 operator commands (scan, reconcile, scan, plus the hand-made change).
- **SC-003**: Each in-place weakening in User Story 3 (action flipped, prefix changed, binding removed) is reported on the next scan, naming the changed field. Each is clean after one reconcile.
- **SC-004**: A permit entry inserted by hand ahead of the deny is reported as undesired on the next scan in 100% of runs. A reader can distinguish it from a missing rule.
- **SC-005**: A converged six-device dcfabric fabric still scans clean, with 0 drifted and 0 items, after ACL collection is added. No dcfabric device reports any ACL drift item.
- **SC-006**: A scan of either use case writes nothing to any device.
- **SC-007**: Every new boundary shape (device ACL config, the Source of Truth's response for ACL entities) is pinned by a fixture captured from the live lab, not written by hand.
- **SC-008** *(only if FR-017 brings User Story 5 into scope)*: Before and after reconcile, traffic from OT to a non-allowed WAN destination is blocked in 100% of checks. Traffic to the allowed subnet is delivered in 100% of checks. With the deny removed, the blocked check is delivered.

## Assumptions

- **Sequencing.** Implementation starts after #103 (the dcfabric MVP demo epic) closes. The spec can be reviewed now. Nothing in it changes dcfabric behaviour.
- **One rule, one direction, IPv4.** The first cut has one ACL, bound ingress on the OT interface, and matches on IPv4 source and destination prefixes only. Protocol and port matching, egress binding, IPv6 and multiple ACLs per interface are out of scope. The intent model does not anticipate them (Principle VII).
- **How "cannot reach the WAN except X" is enforced.** The lab enforces it as an ingress filter on the OT segment with two entries: accept to the named subnet, then drop everything else. As a side effect, OT also cannot reach the office segment, which matches common IT/OT segmentation practice. If the maintainer wants OT to reach the office, that is one more accept entry in the seed, not a design change.
- **The final drop is explicit.** The rule ends with an explicit drop-all entry, so its meaning does not depend on the device's implicit default action.
- **The rule is an ACL, not a zone policy.** Intent holds the access-control list directly. A higher-level "segment A may reach segment B" policy compiled into ACLs is deferred until a second rule or a second device needs it.
- **The carrier is in intent.** The carrier router belongs to the site_edge use case and snapl deploys it like any other device, the way dcfabric models its spines. A real enterprise would not manage its provider's router. The lab does it so that one config path builds the whole lab, and the demo documentation notes the simplification.
- **Hand-added match criteria are a known limitation.** If someone adds a match criterion snapl does not manage, such as a protocol or port on the deny entry, the entry is narrowed and this goes undetected in the first cut. This is listed as a known limitation in the demo documentation, not implied away.
- **Merge-only apply still holds (#65).** Unless FR-016 says otherwise, reconcile restores what intent declares and does not remove anything. Verification after reconcile fails only on forward drift. Undesired config is reported, not fatal (#124). A hand-inserted permit therefore survives a "successful" reconcile, and the reconcile summary must not imply otherwise.
- **Operational BGP state is not verified.** As on dcfabric today, verification compares configuration. Whether the eBGP session to the carrier is established is not a pass/fail criterion unless FR-017 brings the traffic test in, which needs working routing.
- **Device names, addresses and ASNs are new.** They do not reuse dcfabric's or test_edge's (test_edge already seeds `edge-01` and `edge-02`).
- **The platform is unchanged.** SR Linux at the version the dcfabric lab pins (26.3.1). The protected set, like the interface one, is a property of the platform, not the use case.

## Dependencies

- **#103**: the dcfabric MVP demo epic. Must close first.
- **Spec 007 / #54**: undesired-config detection. Reused for User Story 4 and FR-010.
- **#65**: targeted removal of undesired config. FR-016 decides whether part of it comes forward for snapl-owned ACLs.
- **#124**: verification fails only on forward drift. It defines what "reconcile succeeded" means while an undesired entry remains.
- **#117**: the use-case issue this spec answers.

## Out of Scope

- A second vendor, firewall appliances, or zone and security-policy models (Principle VI).
- Higher-level segmentation policy compiled into ACLs.
- Protocol and port matching, IPv6, egress filters, and multiple ACLs per interface.
- Scheduled or automatic remediation. Reconcile stays operator-triggered (Principle VIII.2).
- Any change to dcfabric's intent, templates or lab.
