# Research: Site Edge — A Segmentation Rule as Intent

**Feature**: 008-site-edge
**Date**: 2026-10-28
**Method**: desk research against the current code on `main` (026f631), with no lab. The lab, Docker, Infrahub and Temporal were not used. Everything below about SR Linux's ACL model is **provisional until captured on the live 26.3.1 lab** (R1, R16). Nothing here is presented as observed device behaviour.

The spec carries three open scope questions (Q1 to Q3). The decisions below take each question's proposed default, so the plan is buildable as written. R17 and R18 say what changes if the maintainer answers the other way.

---

## R1: The SR Linux ACL model on 26.3.1

**Decision (provisional)**: Target the `acl-filter` model with a `type` key.

- Interface filter entries live at `/acl/acl-filter[name=<acl>][type=ipv4]/entry[sequence-id=<n>]`.
- Each entry has `match/ipv4/source-ip/prefix`, `match/ipv4/destination-ip/prefix`, `action/{accept|drop}` and `description`.
- Bindings live at `/acl/interfaces/interface[interface-id=<ifname>.<sub>]/input/acl-filter[name=<acl>][type=ipv4]`, with an `interface-ref` naming the interface and subinterface.

**Rationale**: As recalled from SR Linux documentation, this model replaced the per-family `ipv4-filter` containers in the 24.x releases. The lab pins 26.3.1 (#122). **This is recalled, not verified.** Spec 007 showed the cost of assuming device shapes: the "31 extra interfaces per spine" finding only came from a live capture.

**Consequence for the plan**: No template, adapter or fixture is written from this description. Capture C2 (R16) decides the real paths and JSON. If the capture shows the older `ipv4-filter` model, or different binding containers, only the four seams change: the template, the adapter, the field map path templates and the fixtures. The intent model and workflows are unaffected. That is Principle VI working as designed.

**Alternatives considered**:
- Write the template from the documentation and fix it live: rejected. This is the pattern behind #59, #72, #78 and #84, where an assumed shape became permanent phantom drift or a rejected SET.

## R2: Where the rule is enforced

**Decision**: An **ingress** filter on the OT segment's interface (subinterface 0), with two entries:

| seq | action | source | destination | description |
| --- | --- | --- | --- | --- |
| 10 | accept | `10.40.20.0/24` (OT) | `10.50.10.0/24` (allowed subnet) | OT to historian |
| 20 | drop | `0.0.0.0/0` | `0.0.0.0/0` | deny OT to anything else |

**Rationale**:
- Filtering closest to the source is the common practice, and ingress interface filters are the most broadly supported filter direction on SR Linux platforms.
- One binding on one interface keeps the binding entity trivial.
- The explicit final drop means the rule's meaning does not depend on the device's implicit default action, which is still to be confirmed (capture C2). Spelling "any" as `0.0.0.0/0` on both prefixes, rather than leaving them unset, means every in-place weakening is a *value change* that merge-only apply can heal (spec Edge Cases). Capture C2 must show that the device keeps `0.0.0.0/0` as written rather than normalizing it away. If it normalizes it away, the seed falls back to unset prefixes, and "prefix added to an any-match" becomes a documented unhealable case.

**Side effect, recorded as a spec assumption**: OT also cannot reach the office segment. If the maintainer wants that path open, it is one more accept entry in the seed.

**Alternatives considered**:
- **Egress filter on the WAN uplink** (`accept src OT dst allowed`, `drop src OT`, `accept any`). This reads more literally as "OT cannot reach the WAN", but it needs a third explicit accept-all entry. Egress filter support and semantics also vary across 7220 platforms, and that would need its own capture. Rejected for the first cut.
- **A routing-based fence** (OT in its own VRF with no route to the WAN). It would demonstrate nothing about drift on a security control. Rejected.

## R3: Intent model shape

**Decision**: Two new flat lists on `DesiredState`, both defaulting to empty:

- `acl_entries: list[AclEntry]`. Each entry carries its own `acl_name` and `sequence`.
- `acl_bindings: list[AclBinding]`. Each is `(interface, acl_name)`, with ingress implied.

`Interface` and `BGPSession` are unchanged.

**Rationale**:
- `DesiredState` is `extra="forbid"`, so adding fields with empty defaults keeps every existing constructor and every dcfabric payload valid (FR-005).
- Flat lists match how `interfaces` and `bgp_sessions` already flow through the diff, which iterates entities one kind at a time. The template regroups entries by `acl_name` with Jinja's `groupby`.
- A **separate binding entity**, rather than an `ingress_acl` field on `Interface`, is what gives correct drift semantics:
  - A filter bound by hand to an interface where intent declares no binding becomes an **undesired** binding (spec 007). It is reported and is not fatal to verification (#124).
  - As a field on `Interface`, the same change would be forward drift with `desired=None` that merge cannot remove, so verification would fail on every reconcile.
  - It also keeps dcfabric's interface field map byte-for-byte unchanged (SC-005).

**Alternatives considered**:
- **Nested `AccessList(entries=[...])` model**: rejected. The diff would need a flattening step anyway, and nesting adds a third shape to the intent contract for no consumer.
- **`Interface.ingress_acl`**: rejected for the semantic reason above.
- **A direction field on the binding**: rejected. Only ingress exists, so it would be speculative configurability (Principle VII).

## R4: Source of Truth schema

**Decision**: A new Batch 3 file, `schemas/network_acl.yml`, that defines the following and keeps the whole ACL concept in one file:

- `SecurityAccessList`: `name`, `description`, and a relationship to `DcimDevice`. It is unique per device, with uniqueness constraint `[device, name]`.
- `SecurityAccessListEntry`: `sequence` (Number), `action` (Dropdown: accept, drop), `source_prefix` and `destination_prefix` (IPNetwork, optional), `description`, and a parent relationship to its list. It is unique per list, with uniqueness constraint `[access_list, sequence]`.
- An `extensions` block giving `InterfacePhysical` an optional, cardinality-one `ingress_acl` relationship to `SecurityAccessList`. The binding lives on the interface, because an interface takes at most one ingress IPv4 filter, and the schema then enforces the device's own constraint.

**Rationale**: `base/` and `extensions/` must stay unmodified (schemas README). Project-specific kinds belong in Batch 3. All Batch 3 files are loaded in a single `schema.load` call, so the `InterfacePhysical` extension can reference `SecurityAccessList` from the same file. That is still to be confirmed live (capture C7).

**Alternatives considered**:
- **Extend `BusinessFirewallRuleSet`** (`business_intent.yml`): rejected. That stub is explicitly reserved ("Do NOT expand these in the current feature") for a later *business-level* intent design. A device ACL is the opposite altitude.
- **Vendor the schema-library's `experimental/security/security.yml`**: rejected. Checked on 2026-10-28 via the GitHub API, it is a firewall zone and policy model (`SecurityZone`, `SecurityPolicyAssignment`, address and service groups, `SecurityService`, ...). It sits under `experimental/` rather than `extensions/`, so it is not stable. Vendoring it unmodified would force its shape onto a two-entry interface ACL.

**Risk**: The schema is global. Provisioning *any* use case loads every Batch 3 file, so dcfabric instances gain these kinds too. The change is additive (new kinds plus one optional relationship), which Infrahub accepts on a live instance. The dropped-default problem in the live-loop notes was a *removal*. Capture C7 confirms the additive load on an already-seeded dcfabric instance.

## R5: Schema readiness poll covers new kinds (#87)

**Decision**: Extend `collect_extension_attributes` (or add a sibling) so `SchemaLoader._await_schema_ready` also waits for the brand-new `nodes[]` kinds (`Security` + `AccessList`, and so on) and their attributes, not only extension attributes.

**Rationale**: Today the poll only watches attributes added to existing kinds. A seed that races registration of a new kind is the #87 failure (the SDK silently drops unknown attributes) in a new place. A fresh instance that is provisioned and immediately seeded with site_edge would hit it.

## R6: Composite keys in the drift field map

**Decision**: Generalize `ENTITY_FIELD_MAP` from `key_field: str` to `key_fields: tuple[str, ...]`, and make `_key_pattern` compile one capture group per placeholder.

- Existing kinds become one-tuples, with no behaviour change.
- `_undesired_entities` compares tuples of keys.
- The `acl_entry` path template is `/acl/acl-filter[name={acl_name}][type=ipv4]/entry[sequence-id={sequence}]`. This is provisional, per R1.

**Rationale**: An ACL entry is identified by (list, sequence). Nothing short of two keys names its path. `_key_pattern` and `_diff_entity` assume exactly one `{placeholder}` and one `str.format` key today.

**Alternatives considered**:
- **A synthetic single key** (for example an `entry_key = "ot-ingress/20"` attribute on the model): rejected. It puts a vendor-path concern into a vendor-neutral model (Principle VI: models never name a vendor), and the path still has two bracketed keys to match.
- **Collapse the ACL to one entity whose field is the whole entry list**: rejected. Drift would read "`entries` differs", not "entry 20's action differs", which loses the "we name the path" property the CLI is built around.

## R7: Templates for the new use case

**Decision**: `templates/site_edge/` contains `interfaces.j2` and `bgp.j2` as **copies** of dcfabric's, plus a new `acl.j2`.

- `ConfigRenderer.render` renders `acl.j2` only when `acl_entries` or `acl_bindings` is non-empty, and merges it under the payload's `acl` key.
- A use case with ACL intent but no `acl.j2` fails loudly with a `TemplateError`.
- New render guards, in the style of #72:
  - a binding that names an undeclared ACL
  - duplicate `(acl_name, sequence)`
  - an action outside {accept, drop}

**Rationale**: The renderer loads templates per use case from `templates/<use_case>/`, with `interfaces.j2` and `bgp.j2` hardcoded. Principle VII: two small copies are simpler than inventing a shared-template loader for a second use case. The render→diff contract test, parametrized over both use cases (R12), is the guard against the copies diverging in a way that breaks the loop. Divergence on purpose stays allowed, and R18 might need it.

**Alternatives considered**:
- **A shared `templates/_srlinux/` fallback through a Jinja `ChoiceLoader`**: deferred. It is a refactor of dcfabric's template loading, and that is out of scope (spec: no dcfabric changes). Revisit at the third use case.
- **Symlinks**: rejected. They are fragile under packaging and invisible in review.

## R8: Collect paths stay global

**Decision**: Add `"/acl"` to the single `DRIFT_PATHS` tuple. Every device of every use case collects it.

**Rationale**:
- `collect_drift_state` deliberately resolves paths in the adapter seam without use-case knowledge (#119, #123). Branching on `device.use_case` there would leak use-case knowledge into a vendor seam.
- Collecting `/acl` on dcfabric is the point of SC-005: a hand-added ACL on a leaf *should* be reported as undesired (FR-010), and factory ACL content *must not* be.

**Risk**: The extra GET payload on 6 dcfabric devices is the `/acl` state tree, including per-entry statistics. Spec 007 SC-006 (no measurable scan slowdown) is re-checked live.

## R9: Protected set and false positives for ACLs

**Decision**: `ENTITY_FIELD_MAP["acl_entry"]["protected"]` and `["acl_binding"]["protected"]` start empty. **Capture C1 sets their membership** before any undesired-ACL test is written.

- If factory control-plane filters sit in a container the `acl_entry` path template does not match (for example a separate CPM filter container), they are excluded by shape and no name is needed.
- If they share the `acl-filter` list, they are protected by filter name. Like `mgmt0`, they keep the device reachable, so they are never reported and never removed (FR-011, Principle VIII.5).
- If `mgmt0.0` carries a factory binding, it is protected by interface id.

**Rationale**: This mirrors spec 007's method: measure the factory baseline first, then decide the rule. Guessing the set in advance is how a fabric ends up never reading CLEAN.

**Value fields**:
- For `acl_entry`: `action`, `source_prefix` and `destination_prefix`. Like a BGP neighbour, an entry only exists because somebody created it.
- For `acl_binding`: `acl_name`.

## R10: Restoring a weakened rule with merge-only apply

**Decision**: Apply stays a gNMI SET **update** at `/` (merge). The site edge reuses it unchanged.

**Expected behaviour (to confirm by capture)**:
- **C3, entry deleted**: merge re-creates the entry.
- **C5, action flipped from drop to accept**: `action` is a YANG choice. Per RFC 7950, creating a node in one case removes the nodes of the other cases, so merging `drop {}` should leave no `accept`.
- **C6, binding deleted**: merge re-creates it.

If C5 shows both cases present, or the SET rejected, merge cannot heal an action flip. The fallback is a targeted `replace` on that one entry's path. That is a scoped write the executor does not do today, so it would come back to the maintainer as a plan change, not be slipped in.

## R11: The hand-inserted permit (Q3, FR-016)

**Decision (proposed default)**: Report it, do not remove it.

- Detection reuses spec 007 unchanged: an `acl_entry` whose `(acl_name, sequence)` is not in intent, inside any filter, is undesired.
- Reconcile restores what intent declares. The inserted entry survives, and post-reconcile verification passes because it fails only on forward drift (#124).
- The reconcile summary and `docs/demo-scenarios.md` must say this in so many words.

**If the maintainer answers "remove it"**: This is the first slice of #65, scoped to undesired entries **inside an ACL that snapl declares on that device**.

1. `Executor` gains a targeted-delete operation, which is a contract change to `abc.py` (Principle V). Its `dry_run` lists the paths it would delete.
2. Deletion is a gNMI SET `delete` on `/acl/acl-filter[name][type]/entry[sequence-id]` only. It is never a `replace` at `/` (spec 007 FR-011), and never touches anything in the protected set.
3. It is operator-triggered and confirmed, for example by a reconcile flag that shows the dry-run first. It never happens by default (Principle VIII.2 and VIII.5).
4. Spec 007's User Story 3 is the design of record. This feature would implement it for one entity kind.

The cost is a new executor write path, a new CLI confirmation flow and their live evidence: roughly the size of this feature again. That is why the default leaves it in #65.

## R12: Extending the render→diff contract test

**Decision**: Parametrize `tests/unit/test_orchestrator/test_render_diff_contract.py` by use case.

- Each use case gets its own "full desired state" fixture: dcfabric's as today, and site_edge's adding ACL entries (with set and unset prefixes) and a binding.
- The field-coverage meta-guard checks the **union** across use cases, so every `ENTITY_FIELD_MAP` field is rendered by at least one use case's templates. This also fixes today's `entities[kind]` lookup, which would `KeyError` on a new kind.
- `_replay_through_adapter` learns the `acl` payload key.

**Rationale**: As written, the test renders only `use_case="dcfabric"` and indexes a fixed `{interface, bgp_session}` dict. Adding `acl_entry` without this change breaks the suite, which is correct, because that test is the guard.

## R13: Lab design

**Decision**: `containerlab/site_edge.yml`.

- Lab name `site-edge`, so container names are `clab-site-edge-<node>`.
- Its own management network, `clab-snapl-site-edge` on **172.20.22.0/24**. This is the next free /24 after dcfabric's 172.20.21.0/24, registered in `dev/knowledge/host-resource-registry.md` in the same commit.
- The SR Linux image is pinned to the same `26.3.1` as dcfabric.

| node | kind / type | mgmt pin | purpose (seed device role per R20) |
| --- | --- | --- | --- |
| `site-edge-01` | nokia_srlinux / ixr-d2 | 172.20.22.11 | site edge |
| `carrier-a` | nokia_srlinux / ixr-d2 | 172.20.22.21 | carrier (stands in for the underlay) |

Links: `site-edge-01:e1-1 ↔ carrier-a:e1-1` (WAN). In the config-level first cut, the office (`e1-2`) and OT (`e1-3`) ports have no link partner. Their config applies and they are simply oper-down. Hosts are added only under R18.

**Rationale**:
- One /24 per lab is the registry rule (#90).
- Separate pins let both labs run at once (FR-015).
- `ixr-d2` is the type already proven on 26.3.1 in dcfabric. Whether this emulated type accepts interface ACL config is itself part of capture C2. If it does not, the type changes, which is a one-line lab change.

**Guard**: a sibling of `tests/unit/test_containerlab/test_topology.py`, asserting the same invariants for the new lab:

- dedicated bridge, pins inside 172.20.22.0/24
- no overlap with 172.20.20.0/24 or 172.20.21.0/24
- seed `lab_node_name` equals the topology pin for every node
- image pinned

## R14: Addressing, ASNs and names

| what | site_edge | must not collide with |
| --- | --- | --- |
| Device names | `site-edge-01`, `carrier-a` | dcfabric `spine-*`/`leaf-*`; test_edge `edge-01`/`edge-02` |
| Intent management_ip (intent-only) | `10.40.254.11/24`, `10.40.254.21/24` | dcfabric `10.0.0.0/24` |
| Loopbacks | site `10.40.0.1/32`, carrier `10.41.0.1/32` | dcfabric `10.1.0.0/24` |
| Office segment | `10.40.10.0/24` (gateway `.1` on `site-edge-01:ethernet-1/2`) | |
| OT segment | `10.40.20.0/24` (gateway `.1` on `site-edge-01:ethernet-1/3`) | |
| WAN link | `10.41.1.0/31` (site `.0`, carrier `.1`) | dcfabric `10.10.0.0/16`; test_edge `10.210.0.0/30` |
| Allowed subnet (historian/MES) | `10.50.10.0/24` on `carrier-a:ethernet-1/2` | |
| Other WAN destination | `10.50.99.0/24` on `carrier-a:ethernet-1/3` | |
| ASNs | site edge `65210`, carrier-a `64610` | dcfabric `65000–65014`; test_edge `65100–65101` |
| Organization / location | `snapl Site Edge` / `site-edge-lab` | dcfabric `snapl Lab` / `local-lab` |

The two carrier-side subnets are configured on carrier-a even in the config-level first cut. That way the allowed subnet in the rule names something that exists, and R18 needs no re-addressing.

## R15: Lab tasks become use-case aware

**Decision**: `tasks/dev.py` grows a `use_case` parameter, defaulting to `dcfabric`, on `lab_deploy` and `lab_destroy`. It selects the topology file, node list and container prefix. `_strip_gnmi_tls` takes the lab's prefix and nodes. `demo.up --use-case site_edge` passes it through.

**Rationale**: Today `LAB_TOPOLOGY`, `LAB_NODES` and the `clab-dcfabric-` prefix are module constants, and `demo.up` already accepts `use_case` but always deploys dcfabric. The other `demo.*` tasks and every `snapl` command already take a use case: `snapl scan --use-case site_edge`, and `deploy`/`reconcile` resolve device names through the SoT. No CLI or workflow change is needed.

## R16: Live evidence, the capture list

Every row is captured on the live site_edge lab (SR Linux 26.3.1) during implementation, not tonight. Captures C1 and C8 use the dcfabric lab. Each capture is stored under `tests/unit/test_orchestrator/fixtures/` as `srlinux_v26_3_1_acl_<name>.json`, with its provenance recorded in that directory's README, following the existing convention.

Collector outputs are `GnmiCollector.collect(device, DRIFT_PATHS)` (or `["/acl"]` alone), with only volumetric subtrees pruned.

| # | Capture | On | Why it is needed |
| --- | --- | --- | --- |
| C1 | `/acl` on an **unconfigured** node | a dcfabric leaf and a fresh `site-edge-01` | False-positive baseline (R9). It decides the protected set, as 007's ports table did. |
| C2 | `/acl` after `GnmiExecutor.apply` of the site_edge intent | `site-edge-01` | The real entry and binding JSON. It decides R1's paths, whether `0.0.0.0/0` is kept as written (R2), and the implicit default action. It must also confirm the SET is accepted on `ixr-d2`. |
| C3 | `/acl` after **deleting entry 20 by hand** | `site-edge-01` | User Story 2 input. It confirms an absent entry simply disappears from the GET. |
| C4 | `/acl` after **inserting an accept entry at sequence 5 by hand** | `site-edge-01` | User Story 4 input (undesired entry). |
| C5 | `/acl` after **flipping entry 20 to accept by hand**, then again after `apply` | `site-edge-01` | Choice semantics under merge (R10). It is the one capture that can force a plan change. |
| C6 | `/acl` after **removing the OT binding by hand**, and after **binding the ACL to `ethernet-1/2` by hand** | `site-edge-01` | Binding shape, missing binding and undesired binding (User Story 3). |
| C7 | Infrahub responses for `SecurityAccessList`, `SecurityAccessListEntry` and `InterfacePhysical.ingress_acl` after provision and seed, including the type the SDK returns for IPNetwork attributes | dev stack | The SoT boundary shape for the store mapper (R4). It also shows whether a live IPNetwork attribute comes back as an `ipaddress` object or a string, as `management_ip` did. Includes the additive provision on an already-seeded dcfabric instance. |
| C8 | Full dcfabric scan after the change | dcfabric lab | SC-005 regression: 6 devices, 0 drifted, 0 items. |

**Tooling**: `dev/scripts/capture_srlinux_acl_state.py`, a sibling of `capture_srlinux_state.py`. It applies the site_edge intent through the real `GnmiExecutor`, optionally performs one of the hand changes over `sr_cli` (exact CLI syntax confirmed at capture time), collects, prunes and writes the fixture.

Every PR that lands a seam change (template, adapter, field map, store mapper) states in its body which of these it ran and what it observed (Principle III).

## R17: Single vs dual carrier (Q1, FR-014)

**Decision (proposed default)**: A **single carrier**.

**Rationale**: A second carrier adds one interface, one eBGP session and one link: entity kinds snapl already handles. For drift it demonstrates nothing new. What it would really add is path selection and failover, which snapl does not verify. Claiming dual-homing without verifying failover would be an implied claim (constitution Vision).

**If "dual"**: Add `carrier-b` (pin `172.20.22.22`, AS `64620`, WAN link `10.41.2.0/31` on `site-edge-01:ethernet-1/4`). It is seed, topology and one guard-test row. No seam changes, and the ACL is unchanged because it is bound on OT ingress.

## R18: The traffic test (Q2, FR-017)

**Decision (proposed default)**: A **follow-on**, not the first cut.

**Rationale**: Behavioural evidence needs routed forwarding through `site-edge-01` and `carrier-a`. Desk reading of the templates shows dcfabric renders neither of the following:

- **Subinterface membership in the `default` network-instance.** `bgp.j2` renders only `protocols/bgp`.
- **BGP import or export policy.** Neighbours have no policy.

On SR Linux, both are expected to be needed for forwarding and route exchange. Routed subinterfaces must belong to a network-instance, and eBGP without policy is expected to exchange no routes. **Both are unconfirmed here**, as is whether dcfabric's sessions establish today. Verification compares config only, so nothing in snapl currently checks.

The traffic test therefore needs, at minimum:

- a network-instance interface list and a routing policy, *as intent*. They would be new rendered and compared fields, or the lab's behaviour is not from intent.
- Linux hosts (for example `ghcr.io/srl-labs/network-multitool`) on OT and behind carrier-a.
- a way to run the check from the loop or from the e2e suite.
- confirmation that the virtual SR Linux dataplane actually enforces interface ACLs.

That is a second feature's worth of boundary changes.

**If "in the first cut"**: Add hosts `ot-host` (`10.40.20.10`), `historian` (`10.50.10.10`) and `wan-host` (`10.50.99.10`), with pins in `172.20.22.31+`. Add network-instance interface membership and a minimal export policy to intent, templates and the field map. Add an e2e test asserting SC-008. The capture list gains state after routing converges and a forwarding check of the ACL. The observation about dcfabric BGP state above should then become its own issue whatever is decided, since it touches the dcfabric claim.

## R19: Documentation and claims

**Decision**: When site_edge ships:

- `docs/demo-scenarios.md` gains a site_edge scenario (User Story 2's arc) and states its limits: the inserted permit per Q3, unmanaged match criteria, and config-level only per Q2.
- `AGENTS.md`'s "dcfabric is the only implemented use case" line is updated.
- The constitution's "Site Edge (next)" heading becomes current through the amendment process (ADR plus patch bump), in the maintainer's PR.

Until the live arc runs green, none of these claim anything.

## R20: Device and interface role values in the seed

**Decision**:
- Seed `site-edge-01` with device role `edge` and `carrier-a` with device role `core`. Both are existing choices of the vendored `DcimDevice.role` Dropdown (`base/dcim.yml`: core, edge, cpe, spine, leaf, tor). There is no `carrier` choice, and `base/` stays unmodified.
- The office and OT interfaces use interface role `access`.
- The WAN uplink uses `upstream`, which is a choice of the base `DcimInterface.role`.

**Risk, confirmed by capture C7**: `network_interface.yml` (Batch 3) re-declares `role` on `InterfacePhysical` with only `fabric`, `loopback`, `management` and `access`. How Infrahub resolves that against the base Dropdown, whether by merge or by override, is not established from the desk. If the extension overrides it, `upstream` is rejected at seed time. The fix is then to add a `wan` choice to the project-owned `network_interface.yml` extension, which is allowed because Batch 3 is snapl's own file, and seed that instead.

**Rationale**: The seed must use values the provisioned schema accepts, or the seed fails. dcfabric only exercises `fabric` and `loopback`, so the base/extension interplay has never been tested with another value.
