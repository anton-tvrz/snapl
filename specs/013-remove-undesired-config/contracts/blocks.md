# Contract: Observer and Executor additions

Each package's `abc.py` is the source of truth once these methods land (constitution V). This file sets the contract before any code is written. The models are in [data-model.md](../data-model.md).

## Observer: `plan_removal`

```python
class Observer(ABC):
    @abstractmethod
    def plan_removal(self, report: DriftReport) -> RemovalPlan:
        """The deletes that would remove a report's undesired config, and what is refused.

        Pure: no I/O and no events. Forward items never produce a delete
        (FR-003). Every undesired item ends up in exactly one PlannedDelete's
        `answers` or one RefusedEntity's `paths`.

        Raises:
            ValueError: report.status is ERROR (nothing to plan from).
        """
```

Like `evaluate_health`, it is synchronous: it is a pure function over a report. `StructuralObserver.plan_removal` delegates to `structural/removal.py::plan_removal(report)`, which reads `ENTITY_FIELD_MAP` (research R1, R2).

**Behaviour, as unit tests over hand-built reports and the fixture reports in `tests/unit/test_observability/`:**

| Report holds (undesired) | Plan |
|---|---|
| nothing (`CLEAN`) | no deletes, no refusals |
| `/interface[name=ethernet-1/7]/description`, `…/enabled` | one delete `/interface[name=ethernet-1/7]` answering both |
| only `/interface[name=ethernet-1/7]/enabled` (spec edge case) | one delete `/interface[name=ethernet-1/7]` |
| `/interface[name=ethernet-1/49]/subinterface[index=0]/ipv4/address[ip-prefix=192.0.2.1/31]` | one delete of exactly that path |
| a neighbour's `peer_asn` and `peer_group` | one delete of the neighbour entry |
| a port plus its `routed_interface` binding | two deletes, the port and the binding |
| `/network-instance[name=default]/interface[name=system0.0]/name` | refused, `protected` |
| a `routing_policy` item | refused, `not_removable` |
| an undesired item of an unknown kind or shape | refused, `not_removable` |
| forward items alongside undesired ones | deletes for the undesired items only; forward paths in `forward_drift` |

The plan's `delete_paths` are sorted. Two calls on the same report return equal plans.

## Executor: `dry_run_remove` and `remove`

```python
class Executor(ABC):
    @abstractmethod
    async def dry_run_remove(self, device: Device, paths: list[str]) -> RemoveResult:
        """Check deletes against the executor's guard without connecting.

        The dry run that precedes every removal (constitution VIII.4, VIII.5).
        A refused path makes success=False with `refused` naming it.

        Raises:
            ValueError: paths is empty or holds a duplicate (programming error).
        """

    @abstractmethod
    async def remove(self, device: Device, paths: list[str]) -> RemoveResult:
        """Delete `paths` from the device in one gNMI Set of deletes only.

        Checks the same guard first; if any path is refused, nothing is sent.
        The device applies all of the deletes or none (FR-008). A rejection is
        success=False with the device's reason in `error`, not an exception.

        Raises:
            ValueError: paths is empty or holds a duplicate (programming error).
        """
```

**`GnmiExecutor`:**
- **Constructor**: `protected_interfaces: Collection[str] | None = None`. When it is `None`, both methods refuse every path with the reason `"no protected set configured"`, so the executor fails closed. `worker/run.py::_build_executor` passes `ENTITY_FIELD_MAP["interface"]["protected"]` (research R5).
- **Guard**: each path must fully match one of these shapes. `X` is an interface name and may contain `/`; `N` is a digit string; `P` is a prefix and may contain `/`; `A` is an address.
  - `/interface[name=X]`
  - `/interface[name=X]/subinterface[index=N]/ipv4/address[ip-prefix=P]`
  - `/network-instance[name=default]/protocols/bgp/neighbor[peer-address=A]`
  - `/network-instance[name=default]/interface[name=X.N]`

  `X` (or the binding's parent, the part of `X.N` before the last `.`) must not be protected. Any other path is refused. This includes `/`, `/interface`, `/network-instance[name=default]/protocols/bgp` and a bare leaf such as `/interface[name=X]/description` (FR-005).
- **Set**: `gc.set(delete=paths)`. It holds the per-host `_set_locks` lock, uses the same dial target resolution and `_error_reason` as `apply`, and runs in `asyncio.to_thread`.
- **Errors**: a gRPC error from the Set is `success=False` with `str(exc)`. C1 shows that it names the path and the reason (`FailedPrecondition …`).

**Tests** (`tests/unit/test_executor/test_gnmi_remove.py`, with pygnmi mocked as the executor's other tests do):
- The guard accepts each of the four shapes, including `X` with `/` and `P` with `/`, and refuses `/`, each container, leaf paths and protected names.
- `remove` sends exactly one `set(delete=…)` with the paths in order, and never calls `update` or `replace`.
- A refused path means `set` is not called.
- A rejection maps to `success=False` with the device text.
- A dial failure maps to the #194 message.
- Without `protected_interfaces`, every path is refused.
- `ValueError` on an empty list or a duplicate.

**SC-003** (`tests/unit/test_orchestrator/test_removal_paths.py`): every delete path the planner produces from the fixture and hand-built reports, including adversarial ones (`mgmt0`, `system0`, `system0.0`), passes a `GnmiExecutor` built the way the bootstrap builds it. Every path the planner refuses would also be refused by that executor. Every protected name in the field map is refused by that executor. This is the one test that holds detection, planning and execution to the same list (FR-004).
