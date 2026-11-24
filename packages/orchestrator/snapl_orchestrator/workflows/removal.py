"""Removing config intent never asked for, one device at a time (spec 013).

``PlanRemoval`` is the preview: it scans the device, plans the deletes and
checks them with the Executor's dry run. It never writes.

Both removal workflows scan the same way, so the scan is one helper here:
intent, collect, detect, each recorded as a step, with ``DeployIntent``'s
retry policies and failure reasons. Scheduled workflows never import this
module (FR-012, ``test_removal_never_scheduled.py``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from snapl_observability.models import DriftReport, DriftStatus, RemovalPlan
    from snapl_orchestrator.activities.collector import collect_drift_state
    from snapl_orchestrator.activities.executor import dry_run_remove
    from snapl_orchestrator.activities.intent import fetch_desired_state
    from snapl_orchestrator.activities.observability import detect_drift, plan_removal
    from snapl_orchestrator.models import PLAN_REMOVAL, AuditEventType, WorkflowReason
    from snapl_orchestrator.workflows.audit import record_event
    from snapl_orchestrator.workflows.deploy_intent import _format_cause, _is_intent_not_found

if TYPE_CHECKING:
    from snapl_intent.models import DesiredState

_INTENT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_attempts=3,
    non_retryable_error_types=["IntentNotFoundError", "IntentValidationError"],
)
_COLLECT_RETRY = RetryPolicy(initial_interval=timedelta(seconds=2), maximum_attempts=3)
# Pure steps: a retry would only repeat the same answer.
_ONCE = RetryPolicy(maximum_attempts=1)
_TIMEOUT = timedelta(seconds=30)


class _StoppedError(Exception):
    """A step ended the run: the reason and detail its terminal event carries."""

    def __init__(self, reason: WorkflowReason, detail: str, payload: dict | None = None) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.payload = payload or {}


@dataclass(frozen=True)
class _Scan:
    desired: DesiredState
    report: DriftReport


async def _scan(workflow_type: str, device_id: UUID) -> _Scan:
    """Intent, collect and detect for one device, each recorded as a step.

    Raises ``_StoppedError`` for a failure the run reports, and lets cancellation through.
    """
    try:
        desired = await workflow.execute_activity(
            fetch_desired_state, device_id, start_to_close_timeout=_TIMEOUT, retry_policy=_INTENT_RETRY
        )
    except ActivityError as exc:
        if is_cancelled_exception(exc):
            raise
        if _is_intent_not_found(exc):
            detail = _format_cause(exc, default="device not in intent")
            raise _StoppedError(WorkflowReason.DEVICE_NOT_FOUND, detail) from exc
        detail = _format_cause(exc, default="intent fetch failed")
        raise _StoppedError(WorkflowReason.INTENT_UNAVAILABLE, detail) from exc
    await _step(workflow_type, device_id, "fetch_desired_state")

    try:
        collected = await workflow.execute_activity(
            collect_drift_state, desired.device, start_to_close_timeout=_TIMEOUT, retry_policy=_COLLECT_RETRY
        )
    except ActivityError as exc:
        if is_cancelled_exception(exc):
            raise
        raise _StoppedError(WorkflowReason.COLLECT_FAILED, _format_cause(exc, default="collect failed")) from exc
    await _step(workflow_type, device_id, "collect_drift_state")

    try:
        report = await workflow.execute_activity(
            detect_drift, args=[desired, collected], start_to_close_timeout=_TIMEOUT, retry_policy=_ONCE
        )
    except ActivityError as exc:
        if is_cancelled_exception(exc):
            raise
        detail = _format_cause(exc, default="drift detection failed")
        raise _StoppedError(WorkflowReason.COLLECT_FAILED, detail) from exc
    if report.status == DriftStatus.ERROR:
        raise _StoppedError(WorkflowReason.COLLECT_FAILED, report.error or "drift detection returned an error")
    await _step(workflow_type, device_id, "detect_drift")
    return _Scan(desired=desired, report=report)


async def _plan(workflow_type: str, scan: _Scan) -> RemovalPlan:
    """Plan the deletes and dry-run them; a refused plan stops the run (``remove_failed``)."""
    device = scan.desired.device
    plan = await workflow.execute_activity(
        plan_removal, scan.report, start_to_close_timeout=_TIMEOUT, retry_policy=_ONCE
    )
    await _step(workflow_type, device.id, "plan_removal")
    if plan.is_empty:
        return plan

    checked = await workflow.execute_activity(
        dry_run_remove, args=[device, plan.delete_paths], start_to_close_timeout=_TIMEOUT, retry_policy=_ONCE
    )
    if not checked.success:
        raise _StoppedError(
            WorkflowReason.REMOVE_FAILED,
            checked.error or "the executor refused the plan",
            payload={"refused": checked.refused},
        )
    await _step(workflow_type, device.id, "dry_run_remove")
    return plan


async def _step(workflow_type: str, device_id: UUID, activity_name: str) -> None:
    await record_event(
        workflow_type=workflow_type,
        target_id=device_id,
        event_type=AuditEventType.ACTIVITY_COMPLETED,
        activity_name=activity_name,
        outcome="success",
    )


@workflow.defn(name=PLAN_REMOVAL)
class PlanRemovalWorkflow:
    """The preview: the deletes that would remove a device's undesired config. Writes nothing."""

    @workflow.run
    async def run(self, device_id: UUID) -> RemovalPlan:
        await record_event(workflow_type=PLAN_REMOVAL, target_id=device_id, event_type=AuditEventType.WORKFLOW_STARTED)
        try:
            plan = await _plan(PLAN_REMOVAL, await _scan(PLAN_REMOVAL, device_id))
        except _StoppedError as stop:
            await record_event(
                workflow_type=PLAN_REMOVAL,
                target_id=device_id,
                event_type=AuditEventType.WORKFLOW_TERMINATED,
                outcome="failure",
                reason=stop.reason,
                payload={"detail": stop.detail, **stop.payload},
            )
            raise ApplicationError(stop.detail, type=stop.reason.value, non_retryable=True) from None
        except (asyncio.CancelledError, ActivityError) as exc:
            if not is_cancelled_exception(exc):
                raise
            await record_event(
                workflow_type=PLAN_REMOVAL,
                target_id=device_id,
                event_type=AuditEventType.WORKFLOW_CANCELLED,
                outcome="cancelled",
            )
            raise

        await record_event(
            workflow_type=PLAN_REMOVAL,
            target_id=device_id,
            event_type=AuditEventType.WORKFLOW_TERMINATED,
            outcome="success",
            reason=WorkflowReason.SUCCEEDED,
            payload={
                "plan_paths": plan.delete_paths,
                "refused_paths": [refusal.entity_path for refusal in plan.refused],
            },
        )
        return plan
