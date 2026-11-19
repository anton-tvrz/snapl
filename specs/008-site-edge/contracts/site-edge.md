# Contracts: Site Edge — A Segmentation Rule as Intent

**Feature**: 008-site-edge
**Date**: 2026-10-28

Each package's `abc.py` is the source of truth for its contract (Principle V). Under the proposed defaults for Q1 to Q3, this feature **changes no ABC**. It changes three shared data contracts and three seams, and adds no CLI command. This document lists each change and the test that pins it. The full model definitions are in `data-model.md`.

If Q3 is answered "remove it", the `Executor` ABC gains a targeted-delete operation. That case is described at the end and is not part of the default plan.

---

## C-1. Intent data contract (`snapl_intent.models`)

| Change | Compatibility | Pinned by |
| --- | --- | --- |
| New `AclEntry`, `AclBinding` models | additive | `tests/unit/test_intent/test_models.py` |
| `DesiredState.acl_entries: list[AclEntry] = []` | additive. Existing constructors and payloads stay valid. | same |
| `DesiredState.acl_bindings: list[AclBinding] = []` | additive | same |

`IntentStore.get_desired_state(...)`: the signature is unchanged. For a device with ACL intent, the returned `DesiredState` now carries both lists populated. For every dcfabric device, both are `[]`. Pinned by `tests/unit/test_intent/test_store.py`, with Infrahub node fixtures shaped from capture C7.

`IntentStore.provision_schema(use_case)`: the signature is unchanged. It now also waits for the new kinds and the `ingress_acl` relationship to register before returning (#87). Pinned by `tests/unit/test_intent/test_schema.py`.

`IntentStore.seed("site_edge")`: the new `access_lists` section is seeded before `interfaces`, and an interface's `ingress_acl` resolves by `(device, name)`. An unresolvable ACL raises `IntentValidationError`, which is the existing fail-fast rule for peers. Pinned by `tests/unit/test_intent/test_seed.py`.

## C-2. Rendered payload (Executor seam: `ConfigRenderer`, `templates/site_edge/`)

`Executor.apply`, `dry_run` and `rollback` are unchanged. For a `DesiredState` whose `acl_entries` or `acl_bindings` is non-empty:

- The payload gains an `acl` key, holding the rendered `acl.j2` (shape from capture C2). It is still one SET **update** at `/`.
- `dry_run` returns the payload including `acl`, so the operator can see the rule before it is applied.
- These raise `TemplateError` before any SET, and `dry_run` reports them as `render_error`:
  - duplicate `(acl_name, sequence)`
  - a binding that names an undeclared ACL
  - a binding on an interface absent from intent
  - a use case with ACL intent and no `acl.j2`

For a `DesiredState` with both lists empty, which includes all of dcfabric, the payload is byte-identical to today's. Pinned by `tests/unit/test_executor/test_renderer.py`.

## C-3. Normalized actual state (collect adapter seam: `snapl_orchestrator.adapters.srlinux`)

| Change | Pinned by |
| --- | --- |
| `DRIFT_PATHS` gains `"/acl"` (global, R8) | `tests/unit/test_orchestrator/adapters/test_srlinux.py`, `test_activity_collector.py` |
| `normalize_srlinux_state` emits `acl_entry` and `acl_binding` keys (data-model §4) | `test_srlinux.py`, against captured fixtures C1 to C6 |
| Factory ACL content (C1) produces no emitted key that a field-map template matches, or produces only protected keys | `test_srlinux.py`, against C1 |

## C-4. Drift diff (Observability seam: `ENTITY_FIELD_MAP`, `diff_desired_vs_actual`)

| Change | Pinned by |
| --- | --- |
| `key_field: str` becomes `key_fields: tuple[str, ...]`. Existing kinds become one-tuples, with identical drift output. | `tests/unit/test_observability/test_diff.py` (existing cases pass unmodified apart from the rename) |
| New `acl_entry` and `acl_binding` kinds (data-model §5) | `test_diff.py` |
| The forward pass iterates `desired.acl_entries` and `desired.acl_bindings`. The undesired pass covers both kinds. | `test_diff.py` |
| Deterministic ordering (spec 007 FR-009) holds with composite keys | `test_diff.py` |

`DriftItem` is unchanged. The new items use `entity_kind="acl_entry"` or `"acl_binding"`, and `undesired` as spec 007 defines it. `Observer.detect_drift` is unchanged.

## C-5. Render → normalize → diff round trip

`tests/unit/test_orchestrator/test_render_diff_contract.py` is parametrized by use case (R12):

- For each use case's full fixture, the rendered payload, replayed through the adapter, diffs to `[]`.
- Every `ENTITY_FIELD_MAP` field is populated by at least one use case's fixture (union meta-guard).
- Every desired ACL entry and binding appears in the replayed payload.

## C-6. Orchestrator and Presentation

- **Workflows**: unchanged. `test_vendor_seams.py` still passes: no workflow imports an adapter, `snapl_executor.gnmi` or `snapl_collector.gnmi`.
- **Activities**: unchanged. `collect_drift_state` picks up the new path through `DRIFT_PATHS`.
- **CLI**: no new command or option. The site_edge arc is `snapl deploy site-edge-01`, then `snapl scan --use-case site_edge`, then `snapl reconcile site-edge-01` or `snapl reconcile --use-case site_edge --drifted`. `render.py` already prints `undesired` items distinctly (spec 007).
- **Tasks**: `invoke dev.lab-deploy`, `dev.lab-destroy` and `demo.up` take `--use-case` (default `dcfabric`, R15). Pinned by `tests/unit/test_tasks/`.

## C-7. Lab

`containerlab/site_edge.yml` and the site_edge seed are one address registry. A new `tests/unit/test_containerlab/test_site_edge_topology.py` guards:

- the dedicated bridge
- pins inside `172.20.22.0/24`
- no overlap with the other registered /24s
- seed `lab_node_name` equal to the topology pin
- the pinned image

---

## Only if Q3 = "remove injected entries" (not in the default plan)

```python
class Executor(ABC):
    @abstractmethod
    async def remove(self, device: Device, paths: list[str], *, dry_run: bool = True) -> ApplyResult:
        """Targeted gNMI delete of undesired entities at exactly these paths.

        Never a replace at '/'. Paths must be undesired ACL entries inside an
        ACL the device's intent declares, and outside the protected set. With
        dry_run=True (the default) nothing is sent and the result lists the paths.
        """
```

It would be paired with a reconcile option that shows the dry run and asks for confirmation (`--yes` for scripts, as today), plus an audit event per removed path. Spec 007 User Story 3 is the design of record. This document names only the shape, so the maintainer can judge its size.
