# Implementation Plan: Site Edge — A Segmentation Rule as Intent

**Branch**: `008-site-edge` | **Date**: 2026-10-28 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/008-site-edge/spec.md`

> **Sequencing.** This plan is written now and executed after the dcfabric MVP epic (#103) closes. It assumes the proposed defaults for the spec's three open questions: **Q1** single carrier, **Q2** traffic test as a follow-on, **Q3** a hand-inserted permit is reported, not removed. research.md R17, R18 and R11 say exactly what changes for each other answer. Resolve Q1 to Q3 before `/speckit-tasks`.

## Summary

Add a second use case, `site_edge`: one SR Linux site edge with office, OT and WAN segments, and an SR Linux carrier reached over eBGP. Its intent carries one segmentation rule, an ingress ACL on the OT interface that accepts traffic to one named subnet and drops everything else. The new capability is the **access-control list as an intent entity**, carried through every block boundary the closed loop already has:

1. **Intent**: new Infrahub kinds and seed data, plus `AclEntry` and `AclBinding` on `DesiredState`.
2. **Executor**: a new `templates/site_edge/acl.j2`, and the renderer merges it under `acl`.
3. **Collector and adapter**: `/acl` joins `DRIFT_PATHS`, and the adapter normalizes entries and bindings.
4. **Observability**: two new field-map kinds, and composite keys in the diff (an entry is keyed by list and sequence).
5. **Lab**: `containerlab/site_edge.yml` on its own `172.20.22.0/24`, and use-case-aware lab tasks.

No ABC changes, no workflow changes and no CLI changes are needed. The SR Linux ACL shape is **not** known from this desk: it is captured on the live lab first (research R16), and every seam is written against the captures.

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
- Merge-only apply (#65) unless Q3 changes it.
- Vendor-specific code only in the four named seams.
- dcfabric behaviour unchanged (SC-005).
- Every boundary shape comes from a live capture (Principle III).
**Scale/Scope**: 2 devices (3 if Q1 is "dual"), 1 ACL, 2 entries, 1 binding

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design below.*

| Principle | Status | Evidence |
| --- | --- | --- |
| I. NAF Alignment | PASS | Each change sits in its owning block: schema, seed and store in Intent; template and renderer in Executor; field map and diff in Observability; adapter and `DRIFT_PATHS` in the Orchestrator (the ADR-0004 deviation, unchanged); lab and tasks outside the blocks. No new dependency arrows. |
| II. Spec-Driven Development | PASS | New capability (a new entity kind written to devices and the SoT). The spec precedes this plan, and tasks follow after Q1 to Q3 are answered. |
| III. Test-Driven Development | PASS | Every source change below has a test file listed first. Boundary changes carry live evidence (capture list C1 to C8). Fixtures are captured, not hand-written (SC-007). |
| IV. Modularity | PASS | No new package. Each package remains independently unit-testable. |
| V. Contract-First | PASS | No ABC changes under the default answers. The shared-model additions are additive and defined in `contracts/site-edge.md` before code. If Q3 is "remove", an `Executor` ABC change is required and is named there in advance. |
| VI. Single Vendor, Named Seams | PASS | SR Linux only. Vendor paths and JSON live only in `templates/site_edge/acl.j2`, `adapters/srlinux.py` and `ENTITY_FIELD_MAP` path templates. `AclEntry` and `AclBinding` use generic terms (accept/drop, prefixes, interface), and `test_vendor_seams.py` stays green. |
| VII. Simplicity | PASS (one note) | Flat models, copied templates (R7), one ACL, ingress only, IPv4 prefixes only, no zone-policy compiler. The only generalization is `key_field` → `key_fields`, which the first two-key entity forces (R6). It is not speculative. |
| VIII. Intent-First Correctness | PASS | VIII.1: the rule lives in the SoT, and a hand removal is drift. VIII.2: response is `report` on scan and operator-triggered `remediate` via reconcile, with no auto-remediation. VIII.4: merge apply is idempotent (US1-AS4). VIII.5: injected entries are reported, never removed automatically, and the protected set covers factory ACL content (FR-011). VIII.3 is only partly met: verification is config-level unless Q2 brings in the traffic test, and the spec says so rather than implying behaviour. |

**Gate result**: PASS. No violations. The VIII.3 limitation is a scope decision (Q2) and is stated, not hidden.

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
    └── acl.j2                                  N  written against capture C2

packages/observability/snapl_observability/structural/
└── diff.py                                     C  key_fields; acl_entry + acl_binding kinds; forward + undesired passes

packages/orchestrator/snapl_orchestrator/adapters/
└── srlinux.py                                  C  DRIFT_PATHS += "/acl"; normalize entries + bindings

containerlab/
├── site_edge.yml                               N  site-edge-01 + carrier-a on clab-snapl-site-edge 172.20.22.0/24
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
├── test_containerlab/test_site_edge_topology.py    N  address-registry guard
└── test_tasks/                                 C  use-case-aware lab tasks

tests/e2e/                                      C  site_edge arc: deploy → clean → hand delete → drifted → reconcile → clean
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
6. **Live arc (live).** `demo.up --use-case site_edge`, then User Stories 1 to 4 per quickstart.md, then the dcfabric regression scan (C8). The e2e test asserts the US2 arc.
7. **Docs.** Demo scenario and limitations (FR-018). AGENTS.md and the constitution's Use Case heading follow R19, in the maintainer's PR.

Suggested PR slicing, each PR closing its own issue and stating live evidence where it touches a boundary:

- (a) captures and fixtures
- (b) `key_fields` refactor, with no behaviour change
- (c) models, diff, adapter and renderer, offline against the fixtures
- (d) SoT schema, seed and store
- (e) lab, tasks, e2e and docs

## Live Evidence Required (Principle III)

Not run tonight. The lab, Docker, Infrahub and Temporal were not touched while writing this plan.

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

## Risks

| Risk | Effect | Mitigation |
| --- | --- | --- |
| SR Linux ACL model differs from R1's recollection | template, adapter and field-map paths change | Seams only. Captured before code (step 0). |
| Factory ACL content matches the entry template | every device drifts on every scan | C1 first, then the protected set (R9). SC-005 test. |
| Merge does not replace a YANG choice (C5) | an action flip cannot be healed | Escalate to the maintainer: targeted replace of one entry is a new write kind (R10). |
| `0.0.0.0/0` normalized away by the device | "prefix added to any-match" becomes unhealable | Fall back to unset prefixes and document it (R2). |
| `ixr-d2` emulation rejects interface ACLs | lab type change | One-line topology change, confirmed in C2. |
| New SoT kinds race seeding (#87) | silent attribute loss on seed | Readiness poll extended (R5). C7. |
| Hand-inserted permit survives reconcile | an operator believes the site is healed | Report-only stated in the reconcile summary and docs. Q3 lets the maintainer pull removal forward. |

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
