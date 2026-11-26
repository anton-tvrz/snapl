# Tasks: Site Edge — A Segmentation Rule as Intent

**Input**: Design documents from `/specs/008-site-edge/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/site-edge.md, quickstart.md

**Tests**: TDD is mandatory per CLAUDE.md and the constitution. Every phase lists its tests first. Each test is run and seen to fail for the expected reason before the code it covers is written.

**Organization**: Tasks are grouped by the spec's user stories: US1 deploy (P1), US2 deny removed (P1), US3 weakened in place (P2), US4 inserted permit (P2), US5 traffic (P2). US1 needs almost every layer, so it spans two phases: the offline core (Phase 3) and the Source of Truth (Phase 6). Its live check runs at the end, with the other stories' checks.

**PR slicing** (each slice is one PR, in this order):
- **PR A**: Phase 2a (T002 to T005), composite keys in the field map, no behaviour change. Offline.
- **PR B**: Phase 2b (T006 to T011), telemetry for every use case (FR-019) and the `snapl remove` hint (FR-022). Offline. Both serve dcfabric today.
- **PR C**: Phase 2c (T012 to T020), the site_edge lab with hosts, use-case-aware lab tasks, the capture tool. Offline.
- **PR D**: Phase 2d (T021 to T029), live captures C1 to C6 and C10, and the shapes they settle. **Needs the lab**, all of it.
- **PR E**: Phases 3, 4 and 5 (T030 to T049), the ACL as intent offline: models, field map, adapter, templates, renderer, round trip, and US2 and US3 detection. **Needs PR D.**
- **PR F**: Phase 6 (T050 to T060), the ACL in the Source of Truth: schema, readiness, seed, store, routing policy for the segments. **Needs PR D.**
- **PR G**: Phase 7 (T061 to T066), `snapl remove` for ACL entries and bindings (US4). **Needs PR D.**
- **PR H**: Phase 8 (T067 to T074), the live tests written (traffic, arc, removal) and the docs. Offline.
- **PR I**: Phase 9 (T075 to T083), the live run: every story on the lab, evidence, status. **Needs the lab**, all of it.

**Issues**: each slice opens its own issue per `dev/guides/tracking-work.md`, titled `feat(<block>): spec 008 PR <X>, <goal>`, part of the umbrella #117. Every PR `Refs #117`. #117 stays open until PR I.

| PR | Changelog |
| --- | --- |
| A | `skip-changelog` label (no behaviour change) |
| B | `changelog/<issue>.added.md` |
| C | `changelog/<issue>.added.md` |
| D | `skip-changelog` label (fixtures and docs) |
| E, F, G, H | `changelog/<issue>.added.md` |
| I | `skip-changelog` label |

**The lab**: tasks marked **Needs the lab** are left unticked by an unattended run, listed under "Deferred to the lab", and the slice still merges (`dev/guides/overnight-runs.md`). PR D and PR I are lab-only.

**Needs PR D**: PRs E, F and G write seams against the shapes PR D captures (plan, build order step 0; research R1). An unattended run reaching PR E while PR D's tasks are unticked must **stop and report**, as for a product decision. It must not write templates, adapter code, guard shapes or fixtures from data-model.md's provisional shapes.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 to US5 from spec.md
- Exact file paths in every description

---

## Phase 1: Setup

- [x] T001 Point `.specify/feature.json` at `specs/008-site-edge`, so overnight runs and Spec Kit commands find this spec once it merges (spec 013 is shipped).

---

## Phase 2: Foundational (Blocking Prerequisites)

### Phase 2a: Composite keys (PR A)

**Purpose**: an ACL entry is keyed by (list, sequence). The field map takes one key today (research R6).

#### Tests (write first, verify they fail)

- [ ] T002 Extend tests/unit/test_observability/test_diff.py:
  - every `ENTITY_FIELD_MAP` kind declares `key_fields` as a non-empty tuple, and no kind declares `key_field`;
  - `_key_pattern` compiles one capture group per placeholder: a two-placeholder template such as `/x[a={a}]/y[b={b}]` matches `/x[a=k1]/y[b=2]` as `("k1", "2")`, and does not match a deeper path;
  - `_undesired_entities` compares key tuples. Use a test-only two-key spec passed directly, not a new kind in the real map;
  - a desired key with an `int` field (`2`) equals the actual key captured as `"2"` (data-model §5, key types).
- [ ] T003 [P] Run tests/unit/test_observability/test_removal_plan.py, tests/unit/test_orchestrator/test_render_diff_contract.py and tests/unit/test_observability/test_diff.py before the change, and record the counts in the PR body. Every existing test must pass unmodified after it, apart from renaming `key_field`.

#### Implementation

- [ ] T004 In packages/observability/snapl_observability/structural/diff.py:
  - rename `key_field` to `key_fields` for all four kinds (`interface`, `bgp_session`, `routed_interface`, `routing_policy`), each a one-tuple;
  - make `_key_pattern` emit one group per `{placeholder}`;
  - make `_diff_entity` format the path from every key field;
  - make `_undesired_entities` build and compare key tuples, formatting both sides through the template.
- [ ] T005 Update every other reader of `key_field` that `grep -rn key_field packages/ tests/unit` finds, then run `uv run pytest tests/unit -q`. No drift output changes.

**Checkpoint**: `uv run invoke test-unit` is green. dcfabric's drift output is byte-identical.

### Phase 2b: Telemetry for every use case and the remove hint (PR B)

#### Tests (write first, verify they fail)

- [ ] T006 [P] Extend tests/unit/test_orchestrator/test_worker_run.py:
  - with `SNAPL_TELEMETRY_USE_CASE` unset or blank, `telemetry_use_case()` returns `None`, and the `TelemetryRunner` the worker builds gets `use_case=None`;
  - with it set to `site_edge`, the runner gets `"site_edge"`;
  - the startup log line names "every use case" when `None` (research R21).
- [ ] T007 [P] Extend tests/unit/test_presentation/test_render.py (FR-022):
  - a scan view with a device that has undesired items prints `snapl remove <device>` for that device, once;
  - a reconcile result whose verification leaves undesired items prints the same line for each such device;
  - a clean device, or one with forward drift only, prints no hint;
  - `--json` output gains no new key (the hint is human output only).
- [ ] T008 [P] Extend tests/unit/test_presentation/test_cli.py: `snapl scan` and `snapl reconcile` pass the device names through to the renderer, so the hint names the device, not its id.

#### Implementation

- [ ] T009 In packages/orchestrator/snapl_orchestrator/worker/run.py, drop `DEFAULT_TELEMETRY_USE_CASE`. `telemetry_use_case()` returns the variable's value or `None`, and the log line says which. Update the env-var docstring in the same file.
- [ ] T010 In packages/presentation/snapl_presentation/render.py, print `To remove what intent never asked for: snapl remove <device>` under each device with undesired items, in the scan and reconcile views. Wire the device names through packages/presentation/snapl_presentation/cli.py if T008 needs it.
- [ ] T011 Amend specs/016-device-telemetry/ (the contract or quickstart that names `SNAPL_TELEMETRY_USE_CASE`): unset now means every use case. Note the amendment in the PR body. Add the changelog fragment.

**Checkpoint**: one worker streams every use case's devices. Scan and reconcile name the remove command.

### Phase 2c: The lab and its tooling (PR C)

**Purpose**: the site_edge lab exists and can be brought up alongside dcfabric, so PR D can capture on it. Nothing here touches intent or a seam.

#### Tests (write first, verify they fail)

- [ ] T012 [P] Write tests/unit/test_containerlab/test_site_edge_topology.py, a sibling of test_topology.py (contracts C-7):
  - bridge `clab-snapl-site-edge`, subnet `172.20.22.0/24`;
  - every node's mgmt pin is inside it: `site-edge-01` .11, `carrier-a` .21, hosts .31 to .34;
  - the /24 overlaps neither 172.20.20.0/24 nor 172.20.21.0/24;
  - SR Linux nodes use the same pinned image as containerlab/dcfabric.yml, and the host image is pinned by digest;
  - the links of research R13;
  - every enabled port of the two routers has a link partner (health, FR-019).

  The seed `lab_node_name` check is added in T055, once the seed exists.
- [ ] T013 [P] Extend tests/unit/test_tasks/test_dev.py: `lab_deploy` and `lab_destroy` take `use_case` (default `dcfabric`). It selects the topology file, the SR Linux node list and the container prefix (`clab-dcfabric-` or `clab-site-edge-`). `_strip_gnmi_tls` runs on the SR Linux nodes only, never on a host. An unknown use case raises before any command runs.
- [ ] T014 [P] Extend tests/unit/test_tasks/test_demo.py: `demo.up --use-case site_edge` passes the use case to `dev.lab_deploy`, to the seed and to `ensure_schedule` (FR-020), and the preflight lists that use case's routers.

#### Implementation

- [ ] T015 Write containerlab/site_edge.yml per research R13 and R14:
  - lab name `site-edge`;
  - `site-edge-01` and `carrier-a` as `nokia_srlinux` `ixr-d2`;
  - `ot-host`, `office-host`, `historian` and `wan-host` as `linux` with `ghcr.io/srl-labs/network-multitool` pinned by digest;
  - each host's address and default route as `exec` lines.
- [ ] T016 Register `172.20.22.0/24` / `clab-snapl-site-edge` in dev/knowledge/host-resource-registry.md, and add a site_edge section to containerlab/README.md.
- [ ] T017 In tasks/dev.py, replace `LAB_TOPOLOGY`, `LAB_NODES` and the hardcoded prefix with a per-use-case table, and thread `use_case` through `lab_deploy`, `lab_destroy` and `_strip_gnmi_tls` (research R15).
- [ ] T018 In tasks/demo.py, pass `use_case` from `up` to `dev.lab_deploy` and to the preflight.
- [ ] T019 Write dev/scripts/site_edge_capture.cli: the `sr_cli` lines that configure the rule, the binding, the interfaces, eBGP and the export policy by hand for the captures (research R16). PR D uses it before snapl can render ACLs, so the captures do not depend on code written from them.
- [ ] T020 Write dev/scripts/capture_srlinux_acl_state.py, a sibling of capture_srlinux_state.py:
  - `--node`, `--case` with values `baseline`, `applied`, `deny-deleted`, `permit-inserted`, `action-flipped`, `action-flipped-applied`, `binding-removed` and `binding-moved`;
  - an optional `--change` that runs one `sr_cli -ec` line first;
  - collects `["/acl"]` with `GnmiCollector`, prunes statistics, and writes tests/unit/test_orchestrator/fixtures/srlinux_v26_3_1_acl_<case>.json.

  It writes nothing to Infrahub and never calls the executor.

**Checkpoint**: `uv run invoke dev.lab-deploy --use-case site_edge` would bring up the lab beside dcfabric. Not run by an unattended pass.

### Phase 2d: Live captures (PR D) — Needs the lab

**Purpose**: settle every shape the seams depend on (research R1, R9, R10, R16), and the one fact that can change the plan: whether the emulated dataplane enforces an interface ACL (C10).

- [ ] T021 **Needs the lab** Bring up the site_edge lab beside dcfabric with `uv run invoke dev.lab-deploy --use-case site_edge`, and confirm both labs answer gNMI. Raw output goes under `.snapl/lab/<date>/spec008-captures/`.
- [ ] T022 **Needs the lab** C1: capture `/acl` on a dcfabric leaf (`--node` its clab IP, `--case baseline`) and on the unconfigured `site-edge-01`. Record every factory filter and binding, and decide the `protected` membership for `acl_entry` and `acl_binding` (research R9).
- [ ] T023 **Needs the lab** C2: apply dev/scripts/site_edge_capture.cli on both routers, then capture `applied`. Record:
  - the real entry and binding paths and JSON;
  - whether `0.0.0.0/0` is kept as written;
  - the implicit default action;
  - that `ixr-d2` accepts the config.
- [ ] T024 **Needs the lab** C10 (early, decision gate). From `ot-host`, ping `historian` (10.50.10.10) and `wan-host` (10.50.99.10). With the rule configured, the first must succeed and the second fail. Delete entry 20 and repeat. **If the dataplane does not enforce the ACL, stop and escalate** to the maintainer (plan, Risks): change the node type, or move US5 back to a follow-on. Record whether eBGP came up with the export policy in the cli file, and the policy shape it needed (research R18).
- [ ] T025 **Needs the lab** C3 `deny-deleted`, C4 `permit-inserted` (accept at sequence 5) and C6 `binding-removed` / `binding-moved` (bound to `ethernet-1/2.0`), each from a freshly re-applied config. Use the exact `sr_cli` syntax that worked, and update quickstart.md's provisional commands to it.
- [ ] T026 **Needs the lab** C5 `action-flipped`, then re-apply entry 20 with `drop` as a gNMI merge (`set update` with the C2 entry JSON) and capture `action-flipped-applied`. If both cases are present or the Set is rejected, **stop and escalate** (research R10).
- [ ] T027 **Needs the lab** Record each removal path for C11 without removing anything: the exact gNMI path of the inserted entry (C4) and the extra binding (C6), as PR G's guard shapes will need them.
- [ ] T028 **Needs the lab** Write tests/unit/test_orchestrator/fixtures/README.md provenance rows for every new fixture, and specs/008-site-edge/captures.md with C1 to C6 and C10: what was observed, and each decision it settled.
- [ ] T029 **Needs the lab** Replace data-model.md §3 and §4's provisional shapes, research R1's recalled paths, contracts C-8's provisional guard paths and data-model §5's path templates with the observed ones, marking each "observed (C<n>)". If anything changes a design decision, say so in the PR body.

**Checkpoint**: every boundary shape the seams need is a committed fixture. C10 has shown the rule enforced, or the maintainer has decided otherwise.

---

## Phase 3: User Story 1: Declare and Deploy the Segmentation Rule, offline core (P1) 🎯 MVP

**Goal**: an ACL in a `DesiredState` renders, deploys, collects back and diffs clean (contracts C-1 to C-5). **Needs PR D.**

**Independent Test**: the round trip in tests/unit/test_orchestrator/test_render_diff_contract.py is clean for both use cases. The live check is T075.

### Tests (write first, verify they fail)

- [ ] T030 [P] [US1] Extend tests/unit/test_intent/test_models.py with `AclEntry` and `AclBinding` (data-model §2):
  - `sequence > 0`;
  - `action` is only `accept` or `drop`;
  - prefixes are optional IPv4 CIDR strings;
  - `extra="forbid"`;
  - `DesiredState` defaults both lists to `[]`, and every existing dcfabric constructor still validates.
- [ ] T031 [P] [US1] Extend tests/unit/test_orchestrator/adapters/test_srlinux.py:
  - `DRIFT_PATHS` includes `/acl`;
  - the C1 fixtures normalize to no `acl_entry` or `acl_binding` key outside the protected set;
  - the C2 fixture normalizes to data-model §4's keys and values (2 entries, 1 binding, prefixes as written);
  - non-IPv4 filters and egress bindings are not emitted.
- [ ] T032 [P] [US1] Extend tests/unit/test_observability/test_diff.py with the `acl_entry` and `acl_binding` kinds (data-model §5):
  - desired from the site_edge state against the normalized C2 gives no items;
  - the C1 protected names never give an undesired item;
  - ordering is deterministic (spec 007 FR-009);
  - a dcfabric `DesiredState` against a dcfabric actual with factory `/acl` gives the same items as before.
- [ ] T033 [P] [US1] Extend tests/unit/test_executor/test_renderer.py (contracts C-2):
  - a site_edge `DesiredState` renders an `acl` key matching the C2 JSON;
  - entries are grouped by list, in sequence order;
  - each of the four guards raises `TemplateError` before any Set, and `dry_run` reports `render_error`: duplicate `(acl_name, sequence)`, a binding to an undeclared ACL, a binding on an interface absent from intent, and ACL intent without `acl.j2`;
  - every dcfabric `DesiredState` renders a byte-identical payload to today's (pin it against a saved render).
- [ ] T034 [US1] Parametrize tests/unit/test_orchestrator/test_render_diff_contract.py by use case (research R12):
  - a full site_edge fixture with ACL entries and a binding;
  - `_replay_through_adapter` learns the `acl` key;
  - the field-coverage guard checks the union across use cases;
  - every desired entry and binding appears in the replay.

### Implementation

- [ ] T035 [US1] Add `AclEntry` and `AclBinding`, and `DesiredState.acl_entries` and `acl_bindings`, to packages/intent/snapl_intent/models.py.
- [ ] T036 [US1] Add `"/acl"` to `DRIFT_PATHS`, and normalize entries and bindings per data-model §4, in packages/orchestrator/snapl_orchestrator/adapters/srlinux.py. Emit `"accept+drop"` if both action cases are ever present.
- [ ] T037 [US1] Add the `acl_entry` and `acl_binding` kinds, with C1's protected names, to `ENTITY_FIELD_MAP`, and add both to the forward and undesired passes of `diff_desired_vs_actual` in packages/observability/snapl_observability/structural/diff.py.
- [ ] T038 [US1] Create packages/executor/snapl_executor/templates/site_edge/ with copies of dcfabric's `interfaces.j2`, `bgp.j2` and `routing_policy.j2` (research R7), and a new `acl.j2` written against the C2 fixture.
- [ ] T039 [US1] In packages/executor/snapl_executor/gnmi/renderer.py, render `acl.j2` when either ACL list is non-empty, merge it under `acl`, and add the four guards.
- [ ] T040 [US1] Run `uv run pytest tests/unit/test_orchestrator/test_vendor_seams.py -q`. Vendor terms stay in the template, the adapter and the field-map path templates only.

**Checkpoint**: offline, the ACL makes the full trip from intent to device payload and back to a clean diff. dcfabric is unchanged.

---

## Phase 4: User Story 2: A Deny Rule Removed by Hand Is Drift (P1)

**Goal**: the C3 state reports entry 20 missing, field by field. Reconcile restores it with the existing merge. **Needs PR D.**

**Independent Test**: the diff of the site_edge intent against normalized C3. The live arc is T076.

### Tests (write first, verify they fail)

- [ ] T041 [P] [US2] In tests/unit/test_observability/test_diff.py, site_edge intent against normalized C3 gives `…/entry[sequence-id=20]/{action,source_prefix,destination_prefix,description}` with the desired value and `actual=None`, none undesired, and the report is `DRIFTED`.
- [ ] T042 [P] [US2] In tests/unit/test_executor/test_renderer.py, the payload for the same intent still contains entry 20 in full, so a reconcile's merge re-creates it (research R10, C3).

### Implementation

- [ ] T043 [US2] Make T041 and T042 pass if Phase 3 has not already, without touching the reconcile workflow (contracts C-6).

**Checkpoint**: US2 is detected and healable offline. The live run is in PR I.

---

## Phase 5: User Story 3: A Rule Weakened in Place Is Drift (P2)

**Goal**: an action flipped, a prefix changed, or a binding removed or added is drift naming the field. **Needs PR D.**

### Tests (write first, verify they fail)

- [ ] T044 [P] [US3] In tests/unit/test_observability/test_diff.py, against normalized C5 `action-flipped`: one item, `entry[sequence-id=20]/action`, desired `drop`, actual `accept`. Against `action-flipped-applied`: no items (merge healed it, C5).
- [ ] T045 [P] [US3] A destination prefix widened by hand (C2 fixture with entry 10's prefix edited, labelled as derived in the test) gives one item, `entry[sequence-id=10]/destination_prefix`, with both values.
- [ ] T046 [P] [US3] Against normalized C6 `binding-removed`: `/acl/interfaces/interface[interface-id=ethernet-1/3.0]/acl_name` desired `ot-ingress`, actual `None`. Against `binding-moved`: the same OT binding is clean, and `ethernet-1/2.0`'s binding is `undesired=True`.
- [ ] T047 [P] [US3] In tests/unit/test_orchestrator/adapters/test_srlinux.py, an entry with both action cases normalizes to `"accept+drop"` and diffs as drift (data-model §4).

### Implementation

- [ ] T048 [US3] Make T044 to T047 pass if Phase 3 has not already.
- [ ] T049 [US3] Add the PR E changelog fragment.

**Checkpoint**: every in-place weakening of US3 is named offline.

---

## Phase 6: User Story 1: The Rule in the Source of Truth (P1)

**Goal**: the site_edge use case seeds and reads back from Infrahub, with the rule and the routing policy that makes the segments reachable (FR-003, FR-004, FR-017). **Needs PR D.**

### Tests (write first, verify they fail)

- [ ] T050 [P] [US1] Extend tests/unit/test_intent/test_schema.py:
  - `network_acl.yml` loads in Batch 3;
  - the readiness poll also waits for the new node kinds `SecurityAccessList` and `SecurityAccessListEntry` and for `InterfacePhysical.ingress_acl`, not only extension attributes (research R5, #87);
  - the poll times out naming what is missing.
- [ ] T051 [P] [US1] Extend tests/unit/test_intent/test_seed.py:
  - `SEED_ORDER` puts `access_lists` after `devices` and before `interfaces`;
  - entries upsert by `(access_list, sequence)`;
  - an interface's `ingress_acl` resolves by `(device, name)`;
  - an unknown ACL raises `IntentValidationError` before any write;
  - seeding site_edge creates nothing that collides with dcfabric's devices, prefixes, ASNs or organization.
- [ ] T052 [P] [US1] Extend tests/unit/test_intent/test_store.py:
  - `get_desired_state(use_case="site_edge")` maps entries, with `acl_name` from the parent and stringified prefixes, and maps bindings from each interface's `ingress_acl`;
  - every dcfabric device gets both lists empty;
  - the ACL query prefetches relationships (the #33 pattern).

  The node fixtures follow the existing store fixtures' shape. T058 replaces them with C7.
- [ ] T053 [P] [US1] Write tests/unit/test_intent/test_site_edge_seed.py:
  - the seed parses;
  - it declares data-model §6's rule, both entries with explicit `0.0.0.0/0`, and the OT binding;
  - `routing_policies` export the office and OT segments from the site edge and the historian and wan-host subnets from the carrier, in the C10 shape;
  - device roles `edge` and `core` (research R20).

### Implementation

- [ ] T054 [US1] Write packages/intent/snapl_intent/schemas/network_acl.yml (data-model §1), and extend packages/intent/snapl_intent/infrahub/schema.py's readiness poll.
- [ ] T055 [US1] Write packages/intent/snapl_intent/seed/site_edge/topology.yml (research R14, data-model §6, routing policy per T053). Add the seed `lab_node_name` = topology pin check to tests/unit/test_containerlab/test_site_edge_topology.py.
- [ ] T056 [US1] Add the `access_lists` section and the `ingress_acl` resolution to packages/intent/snapl_intent/infrahub/seed.py.
- [ ] T057 [US1] Add the entry query and the binding mapping to packages/intent/snapl_intent/infrahub/store.py.
- [ ] T058 [US1] **Needs the lab** C7 (dev stack): provision on an already-seeded dcfabric instance (additive), seed site_edge, and capture the Infrahub responses for both kinds and `ingress_acl`, including the SDK's type for IPNetwork attributes and whether `upstream` is accepted as an interface role (research R20). Replace T052's fixtures with the captures, and add a `wan` choice to `network_interface.yml` if `upstream` is rejected.
- [ ] T059 [US1] If C10 (T024) showed the policy needs a field `RoutingPolicy` lacks, stop and report it: that is a model change, not seed data (research R18).
- [ ] T060 [US1] Add the PR F changelog fragment.

**Checkpoint**: `demo.up --use-case site_edge` has everything it needs: lab, schema, seed and schedule.

---

## Phase 7: User Story 4: A Permit Rule Inserted by Hand Is Reported and Removable (P2)

**Goal**: `snapl remove` deletes an undesired ACL entry or binding, and nothing else (FR-016, contracts C-8). **Needs PR D.**

**Independent Test**: the planner and the guard against the C4 and C6 states. The live removal is T078.

### Tests (write first, verify they fail)

- [ ] T061 [P] [US4] Extend tests/unit/test_observability/test_removal_plan.py:
  - normalized C4 against site_edge intent plans exactly one delete, the entry-5 path, with its field items as `answers`;
  - normalized C6 `binding-moved` plans one delete, the `ethernet-1/2.0` binding;
  - entries 10 and 20 and the OT binding are never planned, whatever the state;
  - an undesired entry or binding with a C1 protected name is refused as `protected`;
  - a forward ACL item never plans a delete.
- [ ] T062 [P] [US4] Extend tests/unit/test_executor/test_gnmi_remove.py:
  - `check_delete_paths` admits the C4 entry path and the C6 binding path;
  - it refuses `/acl`, `/acl/acl-filter[name=ot-ingress][type=ipv4]`, an entry path with a deeper suffix, and a binding path with a deeper suffix, all as `REFUSED_SHAPE`;
  - it refuses a protected filter or binding as `REFUSED_PROTECTED`.

  The capture group of each new shape is what the protected check reads: the filter name for an entry, the interface id for a binding.
- [ ] T063 [P] [US4] Extend tests/unit/test_presentation/test_render.py: a removal plan and result with ACL deletes render like any other kind, and the `NOTHING_WRITTEN` and removal lines on the dashboard need no change (assert through tests/unit/test_presentation/test_dashboard_projections.py with one ACL delete).

### Implementation

- [ ] T064 [US4] Add `acl_entry` and `acl_binding` to `_REMOVABLE_KINDS` in packages/observability/snapl_observability/structural/removal.py.
- [ ] T065 [US4] Add the two shapes to `_DELETE_SHAPES` in packages/executor/snapl_executor/gnmi/executor.py. The worker builds the executor with `protected_interfaces=ENTITY_FIELD_MAP["interface"]["protected"]` (packages/orchestrator/snapl_orchestrator/worker/run.py): pass the union of the `interface`, `acl_entry` and `acl_binding` protected names, and extend tests/unit/test_orchestrator/test_worker_run.py to pin it. Whether to rename the parameter is left to the PR, and noted in its body.
- [ ] T066 [US4] Add the PR G changelog fragment.

**Checkpoint**: an inserted permit is previewed and removable offline. Reconcile still never deletes (`test_removal_never_scheduled.py` is unchanged and green).

---

## Phase 8: User Story 5 and the live tests, written (P2)

**Goal**: the live tests and the docs exist, so PR I is a run, not a build. Offline: every test here is marked `e2e` or `live` and is skipped without the lab.

### Tests (written, not run here)

- [ ] T067 [P] [US5] Write tests/e2e/test_site_edge_traffic.py (contracts C-10, SC-008). From a converged site edge:
  - `ot-host` reaches `historian` and not `wan-host`;
  - with entry 20 deleted by hand, `wan-host` is reached;
  - after `snapl reconcile site-edge-01 --yes`, it is blocked;
  - with an accept at sequence 5 inserted, it is reached;
  - after `snapl remove site-edge-01 --apply --yes`, it is blocked.

  Each check is `ping -c 3 -W 1` over `docker exec`, with the result recorded under `.snapl/test-runs/`. The test restores the converged config in a finalizer.
- [ ] T068 [P] [US2] Write tests/e2e/test_site_edge_arc.py: deploy both routers, scan clean (SC-001), delete entry 20 by hand, scan drifted naming entry 20, reconcile, scan clean (SC-002). Plus each US3 change in turn (SC-003).
- [ ] T069 [P] [US4] In the same file, the US4 arc: insert the permit, scan reports it undesired (SC-004), reconcile succeeds with it still there, `snapl remove site-edge-01 --apply --yes` succeeds, scan clean, entries 10 and 20 and the OT binding unchanged (SC-009).
- [ ] T070 [P] Extend tests/e2e/test_demo_arc.py with a dcfabric scan after `/acl` joined `DRIFT_PATHS`: 6 clean, 0 items (SC-005). Give `invoke test-e2e` (tasks/main.py) an optional `--select` passed to pytest as `-k`, so one arc can run on its own and still be recorded under `.snapl/test-runs/`. Test it first in tests/unit/test_tasks/test_lab_runs.py.

### Docs

- [ ] T071 Add Scenario 16, "A segmentation rule as intent", to docs/demo-scenarios.md: User Story 2's arc, the US4 removal, the traffic check, and the known limitations (research R19). The limitations are: unmanaged match criteria, a hand-added prefix on an unset match, a binding on a subinterface other than `.0`, and the carrier in intent being a lab simplification.
- [ ] T072 Update quickstart.md to the commands as built.
- [ ] T073 Update README's use-case and limitations sections, and AGENTS.md's "dcfabric is the only implemented use case" line, to "dcfabric and site_edge", marked "pending the live run" until T081.
- [ ] T074 Add the PR H changelog fragment.

---

## Phase 9: The Live Run (PR I) — Needs the lab

Each task's raw output goes under `.snapl/lab/<date>/spec008/`. Write each result into specs/008-site-edge/evidence.md as it lands. The schedule for each use case is paused around hand changes and resumed after, as in spec 013's run.

- [ ] T075 **Needs the lab** [US1] `uv run invoke demo.up --use-case site_edge` with the dcfabric lab up. Deploy both routers, scan clean (SC-001), and deploy again with no change (US1 AS4). Read the rule on the device in order (US1 AS3).
- [ ] T076 **Needs the lab** [US2] [US3] Run `uv run invoke test-e2e --select site_edge_arc` (SC-002, SC-003). Record the scan output for entry 20 deleted and for the action flipped.
- [ ] T077 **Needs the lab** [US5] Run `uv run invoke test-e2e --select site_edge_traffic` (SC-008).
- [ ] T078 **Needs the lab** [US4] C11: `snapl remove site-edge-01` preview and `--apply` for the inserted entry, and again for the extra binding. Capture the device responses as tests/unit/test_executor/fixtures/remove_response_site_edge_*.json, and load them in test_gnmi_remove.py, as spec 013 T039 did (SC-009).
- [ ] T079 **Needs the lab** C8 and C9:
  - a dcfabric scan, 6 clean and 0 items (SC-005);
  - `snapl status --use-case site_edge` and `--use-case dcfabric` both show health from one worker with `SNAPL_TELEMETRY_USE_CASE` unset;
  - take the WAN link down (`docker exec clab-site-edge-site-edge-01 sr_cli -ec "set / interface ethernet-1/1 admin-state disable"`), and time both routers turning unhealthy, then restore it (SC-010).
- [ ] T080 **Needs the lab** Dashboard: `snapl dashboard --use-case site_edge` shows the site edge's drift, health, removal preview and the US4 removal in its record, with no new view (FR-021). Screens under the lab folder.
- [ ] T081 **Needs the lab** Mark spec.md "Shipped <date>" with "Met" pointers on SC-001 to SC-010. Drop "pending the live run" from README and AGENTS.md. Add the CLAUDE.md Recent Changes entry.
- [ ] T082 **Needs the lab** Tick every task, and close #117 with the PR (`Closes #117`).
- [ ] T083 The constitution's "Site Edge (next)" heading becomes current through the amendment process (ADR plus patch bump), in the maintainer's own PR (research R19). Not done by an agent.

---

## Dependencies & Execution Order

- **PR A, B, C**: independent of each other and of the lab. Any order. The list order is the overnight order.
- **PR D**: needs PR C (the lab and the capture tool). Lab-only.
- **PR E, F, G**: need PR D's fixtures. E before G, because the planner and guard tests use E's normalized shapes and kinds. F can go beside E.
- **PR H**: needs E, F and G for the tests to be meaningful, but runs nothing live.
- **PR I**: needs everything. Lab-only.
- **Within each phase**: tests first and seen failing, then the implementation, then the checkpoint.

## Parallel Opportunities

- PR A, B and C can be built side by side.
- Within PR E: T030 to T033 are separate test files. T035 to T039 follow the layers: model, adapter, field map, template, renderer.
- Within PR F: T050 to T053.
- Within PR G: T061 to T063.
- Within PR H: T067 to T070.

## Implementation Strategy

1. **Tonight-able**: PRs A, B and C, unattended. B gives dcfabric multi-use-case telemetry and the remove hint before site_edge exists.
2. **One lab session**: PR D. C10 comes first after C2, because it is the one capture that can send US5 back to a follow-on.
3. **Unattended again**: PRs E, F, G and H, against the captured shapes.
4. **One lab session**: PR I, the whole feature live, then the evidence. Then the public demo post can claim two use cases.

MVP: US1 and US2 (PRs E and F plus T075 and T076) are the demo beat on their own. US3 to US5 make the claim strong.
