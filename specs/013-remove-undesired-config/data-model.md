# Data Model: Remove Undesired Config

**Feature**: 013-remove-undesired-config
**Date**: 2026-11-24

Every Pydantic model is frozen with `extra="forbid"`, matching each package's other models. Executor results are frozen dataclasses, the same as `ApplyResult`. No model names a vendor (Principle VI). Paths are strings, and only the field map and `GnmiExecutor` know their shapes.

## Observability (`snapl_observability.models`)

### `RemovalRefusal` (StrEnum)

| Value | Meaning |
|---|---|
| `protected` | The interface, or a binding's parent interface, is in the field map's protected set (FR-004). |
| `not_removable` | The kind or shape is outside this cut: `routing_policy`, or a path shape the planner doesn't recognise (FR-014, research R3). |

### `PlannedDelete`

| Field | Type | Rule |
|---|---|---|
| `path` | `str` | The gNMI path to delete: an entity entry or an address under one (research R2). Never `/`. |
| `entity_kind` | `str` | The drift item's `entity_kind`. |
| `answers` | `list[str]` | The undesired drift paths this delete removes. Must not be empty. |

### `RefusedEntity`

| Field | Type | Rule |
|---|---|---|
| `entity_path` | `str` | The entity the drift paths belong to. |
| `entity_kind` | `str` | |
| `paths` | `list[str]` | The undesired drift paths left in place. Must not be empty. |
| `reason` | `RemovalRefusal` | |

### `RemovalPlan`

| Field | Type | Rule |
|---|---|---|
| `device_id` | `UUID` | |
| `device_name` | `str` | |
| `scanned_at` | `datetime` | The timestamp of the report the plan was built from (FR-002). |
| `deletes` | `list[PlannedDelete]` | Sorted by `path`, with unique paths. |
| `refused` | `list[RefusedEntity]` | Sorted by `entity_path`. |
| `forward_drift` | `list[str]` | The report's forward drift paths. The plan never acts on them; they are listed so the preview can say a reconcile heals them (US1 scenario 4). |

The model has three invariants:
- No path appears in both a delete's `answers` and a refusal's `paths`.
- Every undesired drift path in the source report appears in exactly one of the two. The planner checks this; the model can't, because it doesn't hold the report.
- No `forward_drift` path appears in either.

It has two derived values:
- `delete_paths -> list[str]`: the sorted delete paths. This is the identity used to compare plans (research R7).
- `is_empty -> bool`: true when there are no deletes.

**Built by**: `Observer.plan_removal(report: DriftReport) -> RemovalPlan`. A report whose status is `ERROR` raises `ValueError`, since there is nothing to plan from, and the workflow never passes one. A `CLEAN` report gives an empty plan.

## Executor (`snapl_executor.models`)

### `RemoveResult` (frozen dataclass)

| Field | Type | Rule |
|---|---|---|
| `device_id` | `UUID` | |
| `device_name` | `str` | |
| `success` | `bool` | |
| `paths` | `list[str]` | The paths checked, or sent. |
| `dry_run` | `bool` | True from `dry_run_remove`, which never dials. |
| `refused` | `dict[str, str]` | Path to the guard's reason. Not empty means `success=False`, and nothing was sent. |
| `device_response` | `str \| None` | Set only when a Set returned. |
| `error` | `str \| None` | The guard's summary, the device's rejection (C1: it names the path) or the dial error (#194). |
| `duration_ms` | `int` | |

The model has three invariants:
- `success=True` means `error` is None and `refused` is empty.
- `success=False` means `error` is set.
- `dry_run=True` means `device_response` is None.

## Orchestrator (`snapl_orchestrator.models`)

### `WorkflowReason` additions

| Value | Meaning |
|---|---|
| `plan_changed` | The re-scan's delete paths differ from the confirmed ones, so nothing was written (FR-007). |
| `deploy_in_flight` | `deploy-intent-{device}` is running, so nothing was written (FR-010, research R8). |
| `remove_failed` | The guard refused the plan, or the device rejected the Set. Nothing was written (C1). |

The existing `verification_failed`, `collect_failed`, `device_not_found`, `intent_unavailable` and `cancelled` keep their meanings.

### `RemovalRequest`

| Field | Type | Rule |
|---|---|---|
| `device_id` | `UUID` | |
| `actor` | `str` | Must not be empty. The operator who confirmed (FR-011). |
| `confirmed_paths` | `list[str]` | Must not be empty, and unique. The `delete_paths` of the plan the operator saw. |

### `RemovalVerification`

| Field | Type | Rule |
|---|---|---|
| `gone` | `list[str]` | Planned delete paths with no undesired item under them after the Set. |
| `remaining` | `list[str]` | Planned delete paths that still have one. |
| `new_forward` | `list[str]` | Forward drift paths that were not in the pre-delete report. |
| `still_refused` | `list[str]` | Undesired paths the plan refused, still there by design. |

It has one derived value, `passed -> bool`, true when `remaining` and `new_forward` are both empty. `gone` plus `remaining` equals the planned delete paths.

### `RemovalResult`

| Field | Type | Rule |
|---|---|---|
| `workflow_id` | `str` | `remove-config-{device_id}`. |
| `device_id` | `UUID` | |
| `device_name` | `str \| None` | None only when intent could not be read. |
| `success` | `bool` | True exactly when `reason` is `succeeded`. |
| `reason` | `WorkflowReason` | |
| `detail` | `str \| None` | |
| `plan` | `RemovalPlan \| None` | The re-scan's plan. On `plan_changed` this is the new plan the CLI shows. |
| `deleted` | `list[str]` | The paths sent in the Set. Empty unless the Set succeeded. |
| `device_response` | `str \| None` | |
| `verification` | `RemovalVerification \| None` | Present exactly when the Set succeeded and the verifying scan ran. |
| `started_at`, `ended_at` | `datetime` | `ended_at` is not before `started_at`. |

The model has three invariants:
- `success` means `verification` is present and `verification.passed`.
- `reason=plan_changed` means `deleted` is empty and `plan` is present.
- `deleted` is not empty exactly when the Set was accepted.

### `AuditEvent` validator addition

A `WORKFLOW_STARTED` event whose `workflow_type` is `RemoveConfig` must have `actor` set, and its payload must contain `confirmed_paths`. There is no new `AuditEventType`: a removal is a workflow, and its events are the ones every workflow writes. Its terminal payload looks like this:

```json
{
  "deleted_paths": ["/interface[name=ethernet-1/7]", "..."],
  "device_response": "…",
  "gone": ["…"], "remaining": [], "new_forward": [], "still_refused": [],
  "detail": "…"
}
```

On `plan_changed` the payload carries `"plan_paths"` (the new plan) in place of `deleted_paths`. `PlanRemoval`'s terminal payload carries `"plan_paths"` and `"refused_paths"`.

## Presentation (`snapl_presentation.dashboard.models`)

`EpisodeClose.kind`: `Literal["remediation", "reconcile", "deploy"]` gains `"removal"`. `schema.d.ts` is regenerated from the OpenAPI document. The designed page labels it "closed by a removal", in `lib/episodes.ts` and the `Lanes.tsx` key, and so does the plain page (`static/index.html`). The parity test holds both pages to the same text.

*Added 2026-11-24 (FR-016, research R10):*

- `DeviceView.remove_command: str | None`: `snapl remove <name>` when the latest scan found undesired paths, else None. `suggested_command` keeps the reconcile command, so a device with both kinds of drift shows both.
- `DeviceView.last_removal_preview: RemovalPreviewView | None`: `at`, `workflow_id`, `plan_paths` and `refused_paths` from the device's latest successful `PlanRemoval`.
- `RunView.removal: RemovalView | None`, for `RemoveConfig` runs only: `actor`, `confirmed_paths`, `deleted_paths`, `device_response`, `gone`, `remaining`, `new_forward`, `still_refused`, `reason` and `detail`, from the run's start and terminal events. A run still in flight has only the first two set.

None of these is a control: the dashboard shows removals and never starts one.

## State: one removal

```text
RemoveConfig(request)
  ├─ deploy in flight? ──yes──▶ DEPLOY_IN_FLIGHT (no write)
  ├─ intent ──not found──▶ DEVICE_NOT_FOUND / INTENT_UNAVAILABLE (no write)
  ├─ collect + detect ──fail──▶ COLLECT_FAILED (no write)
  ├─ plan; delete_paths == confirmed? ──no──▶ PLAN_CHANGED + new plan (no write)
  ├─ deploy in flight? ──yes──▶ DEPLOY_IN_FLIGHT (no write)
  ├─ dry_run_remove ──refused──▶ REMOVE_FAILED (no write)
  ├─ remove (one Set) ──rejected──▶ REMOVE_FAILED + device reason (no write, C1)
  ├─ collect + detect ──fail──▶ COLLECT_FAILED (written; deleted is set, verification None)
  └─ verify ──passed──▶ SUCCEEDED
              └─failed──▶ VERIFICATION_FAILED (written; lists kept)
```

`RemovalResult`'s invariant needs an exception for the `COLLECT_FAILED` after the write: `deleted` is set and `verification` is None. It allows that combination only with `reason=collect_failed`.
