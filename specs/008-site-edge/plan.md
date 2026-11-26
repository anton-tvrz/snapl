# Implementation Plan: Site Edge — A Segmentation Rule as Intent

**Branch**: `008-site-edge` | **Date**: 2026-10-28 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/008-site-edge/spec.md`

> **Revised 2026-11-26.** The draft assumed Q1 single carrier, Q2 traffic test as a follow-on, Q3 report-only. The maintainer answered Q1 single carrier, **Q2 traffic test in**, **Q3 extend `snapl remove`**, plus telemetry for every use case from one worker (spec Clarifications). Specs 009 to 018 shipped meanwhile, so several costs the draft priced are paid: `Executor.remove` and the removal planner (spec 013), network-instance bindings and routing policy from intent (#315, #317), health (spec 016), schedules per use case (spec 009). See "What changed since the draft" below.

## Summary

Add a second use case, `site_edge`: one SR Linux site edge with office, OT and WAN segments, and an SR Linux carrier reached over eBGP. Its intent carries one segmentation rule, an ingress ACL on the OT interface that accepts traffic to one named subnet and drops everything else. The new capability is the **access-control list as an intent entity**, carried through every block boundary the closed loop already has:

1. **Intent**: new Infrahub kinds and seed data, plus `AclEntry` and `AclBinding` on `DesiredState`.
2. **Executor**: a new `templates/site_edge/acl.j2`, and the renderer merges it under `acl`.
3. **Collector and adapter**: `/acl` joins `DRIFT_PATHS`, and the adapter normalizes entries and bindings.
4. **Observability**: two new field-map kinds, and composite keys in the diff (an entry is keyed by list and sequence).
5. **Lab**: `containerlab/site_edge.yml` on its own `172.20.22.0/24`, and use-case-aware lab tasks.

6. **Removal** (spec 013): `acl_entry` and `acl_binding` join the planner's removable kinds, and the executor's delete guard admits their two path shapes.
7. **Telemetry** (spec 016): the worker streams every use case's devices, not one.
8. **Traffic test**: hosts in the lab and an e2e test of the rule's effect.

No ABC changes, no workflow changes and no new CLI command. The SR Linux ACL shape is **not** known from this desk: it is captured on the live lab first (research R16), and every seam is written against the captures.

## What changed since the draft

| Area | Draft (2026-10-28) | Now (main at 3930f69) | Effect on this plan |
| --- | --- | --- | --- |
| Removal | did not exist; Q3 "remove" meant a new `Executor` ABC method | `Executor.remove`, `PlanRemoval`/`RemoveConfig`, `snapl remove` (spec 013) | Q3 costs two seams: `_REMOVABLE_KINDS` in `removal.py` and `_DELETE_SHAPES` in `gnmi/executor.py`. No ABC change |
| Forwarding | dcfabric rendered no network-instance membership and no policy (R18) | both rendered from intent (#315, #317); kinds `routed_interface`, `routing_policy` | Q2 reuses them; no new forwarding entity |
| Templates | `interfaces.j2`, `bgp.j2` | plus `routing_policy.j2` | `templates/site_edge/` copies three |
| Health | did not exist | spec 016: every interface intent expects up, every eBGP session | hosts needed on office and OT; runner widened (R21) |
| Schedules | did not exist | spec 009; `demo.up` creates the use case's report-only schedule | `demo.up --use-case site_edge` gets one for free once the lab tasks take a use case |
| Dashboard | did not exist | specs 010 to 018, `snapl dashboard --use-case` | no new view (FR-021); checked live |
| Address registry | dcfabric only | dcfabric only (172.20.21.0/24) | unchanged: site_edge takes 172.20.22.0/24 |
| Field map key | `key_field` | still `key_field` | the `key_fields` refactor is still needed |

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: Existing workspace only: pydantic>=2.5, jinja2>=3.1, pygnmi>=0.8, infrahub-sdk, temporalio. **No new dependencies.**
**Storage**: Infrahub (SoT) for intent, with new Batch 3 kinds. The SQLite audit log is unchanged.
**Testing**: pytest with markers `unit`, `integration`, `live`, `e2e`. Unit tests use captured fixtures. An e2e test drives the site_edge arc on the live lab.
**Target Platform**: macOS with OrbStack/Docker. Containerlab runs dockerized (`ghcr.io/srl-labs/clab`). Nokia SR Linux `26.3.1`, pinned.
**Project Type**: uv workspace library packages (the six NAF blocks) plus invoke tasks and a Containerlab topology
**Performance Goals**: spec 007 SC-006 holds: a dcfabric scan is not measurably slower with `/acl` collected. A site_edge deploy and scan run in the same order of time as a single dcfabric device.
**Constraints**:
- SR Linux only (Principle VI).
- Apply stays a merge; deletes happen only through `snapl remove` (spec 013).
- Vendor-specific code only in the four named seams.
- dcfabric behaviour unchanged (SC-005).
- Every boundary shape comes from a live capture (Principle III).
**Scale/Scope**: 2 devices in intent, 4 lab hosts, 1 ACL, 2 entries, 1 binding

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design below.*

| Principle | Status | Evidence |
| --- | --- | --- |
| I. NAF Alignment | PASS | Each change sits in its owning block: schema, seed and store in Intent; template and renderer in Executor; field map and diff in Observability; adapter and `DRIFT_PATHS` in the Orchestrator (the ADR-0004 deviation, unchanged); lab and tasks outside the blocks. No new dependency arrows. |
| II. Spec-Driven Development | PASS | New capability (a new entity kind written to devices and the SoT). The spec precedes this plan, and tasks follow after Q1 to Q3 are answered. |
| III. Test-Driven Development | PASS | Every source change below has a test file listed first. Boundary changes carry live evidence (capture list C1 to C8). Fixtures are captured, not hand-written (SC-007). |
| IV. Modularity | PASS | No new package. Each package remains independently unit-testable. |
| V. Contract-First | PASS | No ABC changes. `Executor.remove` exists (spec 013); this feature widens what its guard admits. The shared-model additions are additive and defined in `contracts/site-edge.md` before code. |
| VI. Single Vendor, Named Seams | PASS | SR Linux only. Vendor paths and JSON live only in `templates/site_edge/acl.j2`, `adapters/srlinux.py` and `ENTITY_FIELD_MAP` path templates. `AclEntry` and `AclBinding` use generic terms (accept/drop, prefixes, interface), and `test_vendor_seams.py` stays green. |
| VII. Simplicity | PASS (one note) | Flat models, copied templates (R7), one ACL, ingress only, IPv4 prefixes only, no zone-policy compiler. The only generalization is `key_field` → `key_fields`, which the first two-key entity forces (R6). It is not speculative. |
| VIII. Intent-First Correctness | PASS | VIII.1: the rule lives in the SoT, and a hand removal is drift. VIII.2: scheduled runs report (remediate opt-in, spec 009); removal is operator-only. VIII.3: the traffic test shows the rule's effect, and health its operational state. VIII.4: merge apply is idempotent (US1-AS4). VIII.5: injected entries are removed only by an operator after a preview, never automatically, and the protected set covers factory ACL content (FR-011). |

**Gate result**: PASS. No violations.

## Project Structure

### Documentation (this feature)

```text
specs/008-site-edge/
├── spec.md                 # Feature specification (3 open questions)
├── plan.md                 # This file
├── research.md             # Phase 0: R1–R19, incl. the live capture list (R16)
├── data-model.md           # Phase 1: SoT kinds, intent models, provisional payload, normalized shapes, field map
├── quickstart.md           # Phase 1: how the feature is run once built
├── contracts/
│   └── site-edge.md        # Phase 1: contract deltas per block and the tests that pin them
├── checklists/
│   └── requirements.md     # Spec quality checklist
└── tasks.md                # Phase 2: /speckit-tasks (NOT created yet; after Q1–Q3)
```

### Source Code (repository root)

New (N) and changed (C) files. Tests are listed with their source and are written first.

```text
packages/intent/snapl_intent/
├── models.py                                   C  AclEntry, AclBinding; DesiredState.acl_entries / acl_bindings
├── schemas/network_acl.yml                     N  SecurityAccessList, SecurityAccessListEntry, InterfacePhysical.ingress_acl
├── infrahub/schema.py                          C  readiness poll waits for new node kinds + relationship (R5)
├── infrahub/seed.py                            C  access_lists section (before interfaces); interface ingress_acl resolution
├── infrahub/store.py                           C  ACL entry query + flatten; bindings from interface ingress_acl
└── seed/site_edge/topology.yml                 N  research R14 addressing; data-model §6

packages/executor/snapl_executor/
├── gnmi/renderer.py                            C  render acl.j2 when ACL intent present; new render guards
└── templates/site_edge/
    ├── interfaces.j2                           N  copy of dcfabric (R7)
    ├── bgp.j2                                  N  copy of dcfabric (R7)
    ├── routing_policy.j2                       N  copy of dcfabric (R7); exports the segments (R18)
    └── acl.j2                                  N  written against capture C2

packages/observability/snapl_observability/structural/
└── diff.py                                     C  key_fields; acl_entry + acl_binding kinds; forward + undesired passes

packages/observability/snapl_observability/structural/
└── removal.py                                  C  _REMOVABLE_KINDS += acl_entry, acl_binding

packages/executor/snapl_executor/gnmi/
└── executor.py                                 C  _DELETE_SHAPES += ACL entry, ACL binding (shapes from C4, C6)

packages/orchestrator/snapl_orchestrator/adapters/
└── srlinux.py                                  C  DRIFT_PATHS += "/acl"; normalize entries + bindings

packages/orchestrator/snapl_orchestrator/worker/
└── run.py                                      C  telemetry streams every use case (R21)

packages/presentation/snapl_presentation/
└── render.py                                   C  scan and reconcile name `snapl remove <device>` when undesired config remains

containerlab/
├── site_edge.yml                               N  site-edge-01, carrier-a, ot-host, office-host, historian, wan-host on clab-snapl-site-edge 172.20.22.0/24
└── README.md                                   C  site_edge section

tasks/
├── dev.py                                      C  lab_deploy / lab_destroy / TLS strip take use_case (R15)
└── demo.py                                     C  demo.up passes use_case to the lab tasks

dev/
├── scripts/capture_srlinux_acl_state.py        N  capture tool for C1–C6 (R16)
└── knowledge/host-resource-registry.md         C  register 172.20.22.0/24 / clab-snapl-site-edge

docs/demo-scenarios.md                          C  site_edge scenario + its limitations (FR-018)

tests/unit/
├── test_intent/test_models.py                  C
├── test_intent/test_schema.py                  C  readiness covers new kinds
├── test_intent/test_seed.py                    C  access_lists ordering + ingress_acl resolution
├── test_intent/test_store.py                   C  ACL mapping (fixtures shaped from C7)
├── test_executor/test_renderer.py              C  acl payload, guards, dcfabric byte-identical
├── test_observability/test_diff.py             C  composite keys, new kinds, ordering, dcfabric unchanged
├── test_orchestrator/adapters/test_srlinux.py  C  normalization against C1–C6
├── test_orchestrator/test_render_diff_contract.py  C  parametrized by use case (R12)
├── test_orchestrator/fixtures/srlinux_v26_3_1_acl_*.json  N  captured (C1–C6) + README provenance
├── test_observability/test_removal.py          C  ACL kinds planned; deny and binding in intent never planned
├── test_executor/test_gnmi_remove.py           C  guard admits ACL shapes, refuses the filter container
├── test_orchestrator/test_worker_run.py        C  telemetry use case is every use case
├── test_presentation/test_render.py            C  undesired config names the remove command
├── test_containerlab/test_site_edge_topology.py    N  address-registry guard
└── test_tasks/                                 C  use-case-aware lab tasks

tests/e2e/                                      C  site_edge arc: deploy → clean → hand delete → drifted → reconcile → clean;
                                                   inserted permit → snapl remove --apply → clean; traffic checks (SC-008)
```

**Structure Decision**: No new package or module boundary. The use case is data (seed, lab), one template directory, and two new entity kinds threaded through the existing seams. This is exactly the extension path `diff.py`'s module docstring describes ("adding a new intent entity means extending ENTITY_FIELD_MAP"), plus the composite key that path never had to support.

## Build Order

Each step is Red → Green → Refactor. Steps 0 and 6 need the live lab, and every other step runs offline against their output.

0. **Capture first (live).** Stand up the site_edge lab (step 5 lab files can be drafted as a throwaway for this) and the dcfabric lab. Run captures C1, C2 and C5 *before* any seam code, because C1 sets the protected set and C5 can change the plan (R10). Commit the fixtures and README provenance.
1. **Intent models** (`test_models.py` → `models.py`). Additive fields with empty defaults. The dcfabric suite must pass unmodified.
2. **Diff** (`test_diff.py` → `diff.py`). First the mechanical `key_fields` refactor with existing tests green, then the two new kinds against the C2 to C6 normalized shapes.
3. **Adapter** (`test_srlinux.py` → `srlinux.py`). Normalize the captured fixtures. C1 must yield zero matching unprotected keys.
4. **Renderer and templates** (`test_renderer.py` → `renderer.py`, `templates/site_edge/`). Then the parametrized render-diff contract test (R12) closes the loop offline.
5. **SoT** (`test_schema.py`, `test_seed.py`, `test_store.py` → `network_acl.yml`, `schema.py`, `seed.py`, `store.py`, `seed/site_edge/`). Store fixtures are shaped from capture C7. Then the lab (`test_site_edge_topology.py` → `site_edge.yml`), the registry entry and the use-case-aware tasks.
6. **Removal and telemetry** (`test_removal.py`, `test_gnmi_remove.py` → `removal.py`, `executor.py`; `test_worker_run.py` → `run.py`). Guard shapes from C4 and C6.
7. **Live arc (live).** `demo.up --use-case site_edge`, then User Stories 1 to 5 per quickstart.md, then the dcfabric regression scan (C8) and health for both use cases (C9). The e2e test asserts the US2 arc, the removal arc and the traffic checks.
8. **Docs.** Demo scenario and limitations (FR-018). AGENTS.md and the constitution's Use Case heading follow R19, in the maintainer's PR.

Suggested PR slicing, each PR closing its own issue and stating live evidence where it touches a boundary:

- (a) captures and fixtures
- (b) `key_fields` refactor, with no behaviour change
- (c) models, diff, adapter and renderer, offline against the fixtures
- (d) SoT schema, seed and store
- (e) removal kinds and guard shapes; the remove hint in scan and reconcile output; telemetry for every use case
- (f) lab with hosts, use-case-aware tasks, e2e (arc, removal, traffic) and docs

(a) and (f) need the lab; (b) to (e) run offline and suit overnight runs once (a) has landed the fixtures.

## Live Evidence Required (Principle III)

None run yet. The lab, Docker, Infrahub and Temporal were not touched while writing or revising this plan.

| Capture | Gates | Lab |
| --- | --- | --- |
| C1 factory `/acl` baseline | protected set (FR-011); adapter step 3; SC-005 | dcfabric leaf + fresh site-edge-01 |
| C2 `/acl` after apply | `acl.j2`, adapter paths, `0.0.0.0/0` round trip, implicit default, `ixr-d2` accepts ACL config | site_edge |
| C3 deny entry deleted by hand | US2 fixture | site_edge |
| C4 accept entry inserted by hand | US4 fixture (undesired) | site_edge |
| C5 action flipped by hand, then apply | merge heals a YANG choice (R10). **Plan-changing if it fails.** | site_edge |
| C6 binding removed / moved by hand | US3 fixtures (missing + undesired binding) | site_edge |
| C7 Infrahub ACL kinds and `ingress_acl` after provision + seed; additive provision on a seeded dcfabric instance | store mapper; schema readiness | dev stack |
| C8 full dcfabric scan after the change | SC-005 regression (6 clean, 0 items) | dcfabric |
| C9 health of both use cases from one worker; WAN link down | FR-019, SC-010 | both |
| C10 traffic: OT to historian, OT to wan-host, with the deny removed, with a permit inserted, after reconcile and after removal | FR-017, SC-008; whether the virtual dataplane enforces interface ACLs. **Plan-changing if it does not.** | site_edge |
| C11 `snapl remove site-edge-01` preview and `--apply` for an inserted entry and an extra binding; device response | FR-016, SC-009; guard shapes | site_edge |

## Risks

| Risk | Effect | Mitigation |
| --- | --- | --- |
| SR Linux ACL model differs from R1's recollection | template, adapter and field-map paths change | Seams only. Captured before code (step 0). |
| Factory ACL content matches the entry template | every device drifts on every scan | C1 first, then the protected set (R9). SC-005 test. |
| Merge does not replace a YANG choice (C5) | an action flip cannot be healed | Escalate to the maintainer: targeted replace of one entry is a new write kind (R10). |
| `0.0.0.0/0` normalized away by the device | "prefix added to any-match" becomes unhealable | Fall back to unset prefixes and document it (R2). |
| `ixr-d2` emulation rejects interface ACLs | lab type change | One-line topology change, confirmed in C2. |
| New SoT kinds race seeding (#87) | silent attribute loss on seed | Readiness poll extended (R5). C7. |
| WAN interface role `upstream` rejected, because the Batch 3 `InterfacePhysical.role` re-declaration overrides the base choices | seed fails | Add a `wan` choice to project-owned `network_interface.yml` (R20). C7. Device roles use existing base choices (`edge`, `core`). |
| Hand-inserted permit survives reconcile | an operator believes the site is healed | The reconcile and scan summaries say undesired config remains and name `snapl remove <device>`. Today only the dashboard shows that command (spec 013 FR-016); the CLI gains it here. |
| The emulated dataplane does not enforce interface ACLs (C10) | the traffic test cannot pass | Capture C10 early, in slice (a). If it fails, escalate: change the node type or drop US5 back to a follow-on. |
| Removing an ACL entry deletes too much (the filter, not the entry) | the deny goes too | Guard shapes admit the entry and binding paths only, never `/acl/acl-filter[...]` alone; `test_gnmi_remove.py` pins it. |
| Telemetry for every use case doubles the streams | worker load | Two devices more; spec 016 measured six. Checked in C9. |

## Post-Design Constitution Re-Check

| Principle | Status | Notes |
| --- | --- | --- |
| I. NAF Alignment | PASS | The design adds no cross-block imports beyond shared models. The adapter remains the ADR-0004 deviation, with no new deviation. |
| II. SDD | PASS | Spec, research, data model, contracts and quickstart all precede tasks. Tasks wait on Q1 to Q3. |
| III. TDD | PASS | Test files are mapped for every source change. Captured fixtures come before seam code. The e2e test covers the live arc. |
| IV. Modularity | PASS | No circular dependencies. No new package. |
| V. Contract-First | PASS | `contracts/site-edge.md` lists each contract delta and its pinning test. No ABC changes under the defaults. |
| VI. Single Vendor, Named Seams | PASS | Vendor detail confined to template, adapter and field-map path templates. Models stay generic. |
| VII. Simplicity | PASS | One generalization (`key_fields`), forced by the data. Everything else is copied or additive. |
| VIII. Intent-First Correctness | PASS | Drift on the rule has a defined response. No silent reverse sync and no automatic removal. Protected set extended. The VIII.3 gap is stated and owned by Q2. |

## Agent Context Update

The Spec Kit plan step would run `.specify/scripts/bash/update-agent-context.sh claude`. **It was skipped on purpose.** None of the spec commits for 005, 006 or 007 touched `CLAUDE.md`'s "Recent Changes" or "Active Technologies" (the last update is from 004), and this feature adds no new technology. For the same reason `.specify/feature.json` was left unchanged: the plan setup ran with `SPECIFY_FEATURE_DIRECTORY=specs/008-site-edge`.

## Complexity Tracking

No constitution violations to justify.
