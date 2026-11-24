# Contract: `PlanRemoval` and `RemoveConfig` workflows

Both workflows live in `packages/orchestrator/snapl_orchestrator/workflows/removal.py`, and the worker (`worker/run.py`) registers them. They import only orchestrator models, activities and workflow helpers; `test_vendor_seams.py` covers the new file automatically.

## Activities

| Activity | Module | Wraps | Retry |
|---|---|---|---|
| `plan_removal(report) -> RemovalPlan` | `activities/observability.py` | `Observer.plan_removal` | 1 attempt (pure) |
| `dry_run_remove(device, paths) -> RemoveResult` | `activities/executor.py` | `Executor.dry_run_remove` | 1 attempt (pure) |
| `remove_config(device, paths) -> RemoveResult` | `activities/executor.py` | `Executor.remove`, recorded as `GnmiOperation.CONFIGURE` | 3 attempts, as `apply_config`. A retried delete is safe: deleting a missing path is a no-op (C1). |
| `deploy_in_flight(device_id) -> bool` | `activities/workflows.py` (new) | `activity.client().get_workflow_handle(f"deploy-intent-{id}").describe()`, true when its status is RUNNING. Not found is false. | 3 attempts |

`fetch_desired_state`, `collect_drift_state` and `detect_drift` are reused unchanged. `detect_drift` counts scan metrics only under `ScanDrift` (`_in_a_scan`), so the scans a removal takes never count as fabric scans.

## `PlanRemoval`

```python
@workflow.defn(name="PlanRemoval")
class PlanRemovalWorkflow:
    @workflow.run
    async def run(self, device_id: UUID) -> RemovalPlan: ...
```

- **Id**: `plan-removal-{device_id}-{uuid4}`, started by the CLI. Previews never collide.
- **Steps**:
  1. `WORKFLOW_STARTED`
  2. intent
  3. collect
  4. detect
  5. `plan_removal`
  6. `dry_run_remove` on the plan's deletes, if there are any
  7. `WORKFLOW_TERMINATED` with `plan_paths` and `refused_paths`

  Each step records `ACTIVITY_COMPLETED`.
- **Dry-run refusal**: if the dry run refuses a path the planner produced, that is a defect, because the two share one guard (SC-003). The workflow ends `remove_failed` and raises `ApplicationError` with the refused paths, so it never shows a plan the executor would not run.
- **Failures**: intent, collect or detect failures end the run as `DeployIntent` does (`device_not_found`, `intent_unavailable`, `collect_failed`) and raise `ApplicationError`. The CLI reports the cause. No device write happens anywhere in this workflow.

## `RemoveConfig`

```python
@workflow.defn(name="RemoveConfig")
class RemoveConfigWorkflow:
    @workflow.run
    async def run(self, request: RemovalRequest) -> RemovalResult: ...
```

- **Id**: `remove-config-{device_id}`, started with `WorkflowIDConflictPolicy.FAIL`. A second removal while one runs is refused by Temporal, and the CLI turns that into "a removal of <device> is already running".
- **Steps**: the state diagram in [data-model.md](../data-model.md). `WORKFLOW_STARTED` carries `actor` and `confirmed_paths`. Every step records `ACTIVITY_COMPLETED` or `ACTIVITY_FAILED`, and the Set's record carries the paths and the device response.
- **Return value**: it always returns a `RemovalResult` for outcomes the workflow handles (plan changed, deploy in flight, rejected, verification), and never raises for them. Cancellation records `WORKFLOW_CANCELLED`. If the Set had already returned when cancellation arrived, the cancelled event's payload names the deleted paths.
- **Never**: it never issues a delete that is not in `request.confirmed_paths`, and it never issues one before the fresh plan's `delete_paths` equal them.

**Tests** (`tests/unit/test_orchestrator/test_workflow_removal.py`, using `WorkflowEnvironment.start_time_skipping()` with mocked activities, as the other workflow tests do):
- The happy path, with the order of activity calls asserted.
- `plan_changed`: a re-scan with one more and with one fewer path, and a re-scan with nothing left. `remove_config` is never called.
- `deploy_in_flight` at start, and again before the Set. `remove_config` is never called.
- The dry run refuses: nothing is sent.
- The device rejects: `remove_failed`, `deleted` empty, the device text in `detail`.
- Verification passes. Verification fails with `remaining`, and with `new_forward`. Refused items appear in `still_refused` and do not fail verification.
- Collect fails after the Set: `collect_failed`, `deleted` set.
- Cancellation before the Set, and after it.
- Intent not found: `device_not_found`, nothing sent.
- The audit events: `actor` and `confirmed_paths` on the start, and the terminal payload's keys.

## Never from a schedule (FR-012)

`tests/unit/test_orchestrator/test_removal_never_scheduled.py` parses `workflows/drift_response.py`, `workflows/reconcile_devices.py` and `workflows/deploy_intent.py`. It fails if any of them imports `workflows.removal`, `remove_config`, `dry_run_remove` or `plan_removal`. `schedules.py` builds only `DriftResponse` actions; its existing tests already assert this.
