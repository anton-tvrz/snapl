"""Removing config intent never asked for, one device at a time (spec 013).

``PlanRemoval`` is the preview: it scans the device, plans the deletes and
checks them with the Executor's dry run. It never writes.

``RemoveConfig`` is the confirmed removal. It scans again, writes nothing
unless the fresh plan's deletes equal the ones the operator confirmed and no
deploy to the device is running, sends them in one Set, and verifies with one
more scan (data-model.md, "State: one removal"; research R7 to R9).

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
    from snapl_orchestrator.activities.executor import dry_run_remove, remove_config
    from snapl_orchestrator.activities.intent import fetch_desired_state
    from snapl_orchestrator.activities.observability import detect_drift, plan_removal
    from snapl_orchestrator.activities.workflows import deploy_in_flight
    from snapl_orchestrator.models import (
        PLAN_REMOVAL,
        REMOVE_CONFIG,
        AuditEventType,
        RemovalRequest,
        RemovalResult,
        RemovalVerification,
        WorkflowReason,
    )
    from snapl_orchestrator.workflows.audit import record_event
    from snapl_orchestrator.workflows.deploy_intent import _format_cause, _is_intent_not_found

if TYPE_CHECKING:
    from datetime import datetime

    from snapl_intent.models import DesiredState

_INTENT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_attempts=3,
    non_retryable_error_types=["IntentNotFoundError", "IntentValidationError"],
)
_COLLECT_RETRY = RetryPolicy(initial_interval=timedelta(seconds=2), maximum_attempts=3)
# As DeployIntent's apply: a retried delete is safe, since deleting a missing path is a no-op (C1).
_SET_RETRY = RetryPolicy(initial_interval=timedelta(seconds=5), maximum_attempts=3)
_CHECK_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3)
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
    desired = await _intent(workflow_type, device_id)
    return _Scan(desired=desired, report=await _collect_and_detect(workflow_type, desired))


async def _intent(workflow_type: str, device_id: UUID) -> DesiredState:
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
    return desired


async def _collect_and_detect(workflow_type: str, desired: DesiredState) -> DriftReport:
    device_id = desired.device.id
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
    return report


async def _plan(workflow_type: str, scan: _Scan) -> RemovalPlan:
    """Plan the deletes and dry-run them; a refused plan stops the run (``remove_failed``)."""
    plan = await _plan_only(workflow_type, scan)
    if not plan.is_empty:
        await _dry_run(workflow_type, scan, plan.delete_paths)
    return plan


async def _plan_only(workflow_type: str, scan: _Scan) -> RemovalPlan:
    plan = await workflow.execute_activity(
        plan_removal, scan.report, start_to_close_timeout=_TIMEOUT, retry_policy=_ONCE
    )
    await _step(workflow_type, scan.desired.device.id, "plan_removal")
    return plan


async def _dry_run(workflow_type: str, scan: _Scan, paths: list[str]) -> None:
    device = scan.desired.device
    checked = await workflow.execute_activity(
        dry_run_remove, args=[device, paths], start_to_close_timeout=_TIMEOUT, retry_policy=_ONCE
    )
    if not checked.success:
        raise _StoppedError(
            WorkflowReason.REMOVE_FAILED,
            checked.error or "the executor refused the plan",
            payload={"refused": checked.refused},
        )
    await _step(workflow_type, device.id, "dry_run_remove")


async def _step(workflow_type: str, device_id: UUID, activity_name: str, payload: dict | None = None) -> None:
    await record_event(
        workflow_type=workflow_type,
        target_id=device_id,
        event_type=AuditEventType.ACTIVITY_COMPLETED,
        activity_name=activity_name,
        outcome="success",
        payload=payload,
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


def _under(path: str, delete: str) -> bool:
    """Whether ``path`` is ``delete`` or lies beneath it (``ethernet-1/70`` is not under ``ethernet-1/7``)."""
    return path == delete or path.startswith(delete + "/")


def verify_removal(plan: RemovalPlan, before: DriftReport, after: DriftReport) -> RemovalVerification:
    """What the scan after the Set shows (research R9).

    A delete is gone when no undesired item lies under it any more. Forward
    drift the earlier scan did not have means the removal took something
    intent manages. Undesired items the plan refused stay by design.
    """
    left = [item.path for item in after.items if item.undesired]
    refused = {path for refusal in plan.refused for path in refusal.paths}
    forward_before = {item.path for item in before.items if not item.undesired}
    remaining = [d for d in plan.delete_paths if any(_under(path, d) for path in left)]
    return RemovalVerification(
        gone=[d for d in plan.delete_paths if d not in remaining],
        remaining=remaining,
        new_forward=sorted({item.path for item in after.items if not item.undesired} - forward_before),
        still_refused=sorted(path for path in left if path in refused),
    )


class _Removal:
    """What one removal has done so far, for its result and its terminal event."""

    def __init__(self, request: RemovalRequest, started_at: datetime) -> None:
        self.request = request
        self.started_at = started_at
        self.device_name: str | None = None
        self.plan: RemovalPlan | None = None
        self.deleted: list[str] = []
        self.device_response: str | None = None
        self.verification: RemovalVerification | None = None

    def payload(self, reason: WorkflowReason, detail: str | None) -> dict:
        """The terminal event's payload (data-model.md, "AuditEvent validator addition")."""
        if reason == WorkflowReason.PLAN_CHANGED and self.plan is not None:
            out: dict = {"plan_paths": self.plan.delete_paths}
        else:
            out = {"deleted_paths": list(self.deleted)}
        out["device_response"] = self.device_response
        if self.verification is not None:
            out.update(self.verification.model_dump(mode="json"))
        out["detail"] = detail
        return out

    def result(self, reason: WorkflowReason, detail: str | None = None) -> RemovalResult:
        return RemovalResult(
            workflow_id=workflow.info().workflow_id,
            device_id=self.request.device_id,
            device_name=self.device_name,
            success=reason == WorkflowReason.SUCCEEDED,
            reason=reason,
            detail=detail,
            plan=self.plan,
            deleted=list(self.deleted),
            device_response=self.device_response,
            verification=self.verification,
            started_at=self.started_at,
            ended_at=workflow.now(),
        )


@workflow.defn(name=REMOVE_CONFIG)
class RemoveConfigWorkflow:
    """The confirmed removal: re-scan, write only the confirmed deletes in one Set, verify.

    It returns a ``RemovalResult`` for every outcome it handles and never
    raises for them. It never sends a delete that is not in
    ``request.confirmed_paths``, and never sends one before the fresh plan's
    ``delete_paths`` equal them (FR-007).
    """

    @workflow.run
    async def run(self, request: RemovalRequest) -> RemovalResult:
        device_id = request.device_id
        state = _Removal(request, workflow.now())
        await record_event(
            workflow_type=REMOVE_CONFIG,
            target_id=device_id,
            event_type=AuditEventType.WORKFLOW_STARTED,
            actor=request.actor,
            payload={"confirmed_paths": list(request.confirmed_paths)},
        )
        try:
            reason = await self._remove(request, state)
        except _StoppedError as stop:
            return await self._end(state, stop.reason, stop.detail, extra=stop.payload)
        except (asyncio.CancelledError, ActivityError) as exc:
            if not is_cancelled_exception(exc):
                raise
            payload = {"deleted_paths": list(state.deleted)} if state.deleted else {}
            await record_event(
                workflow_type=REMOVE_CONFIG,
                target_id=device_id,
                event_type=AuditEventType.WORKFLOW_CANCELLED,
                outcome="cancelled",
                payload=payload,
            )
            return state.result(WorkflowReason.CANCELLED, "workflow cancelled by caller")
        return await self._end(state, reason, None)

    async def _remove(self, request: RemovalRequest, state: _Removal) -> WorkflowReason:
        """The steps up to the verdict; a stop on the way raises ``_StoppedError``."""
        await _no_deploy_running(request.device_id)
        scan = await _scan(REMOVE_CONFIG, request.device_id)
        state.device_name = scan.desired.device.name
        state.plan = await _plan_only(REMOVE_CONFIG, scan)
        if state.plan.delete_paths != sorted(request.confirmed_paths):
            raise _StoppedError(
                WorkflowReason.PLAN_CHANGED,
                f"{state.device_name} changed since the preview; nothing was removed",
            )
        await _no_deploy_running(request.device_id)
        await _dry_run(REMOVE_CONFIG, scan, list(request.confirmed_paths))

        await self._send(scan, state)
        after = await _collect_and_detect(REMOVE_CONFIG, scan.desired)
        state.verification = verify_removal(state.plan, scan.report, after)
        return WorkflowReason.SUCCEEDED if state.verification.passed else WorkflowReason.VERIFICATION_FAILED

    async def _send(self, scan: _Scan, state: _Removal) -> None:
        """The one Set of deletes. A rejection or a failed call stops the run with nothing deleted (C1)."""
        device = scan.desired.device
        paths = list(state.request.confirmed_paths)
        try:
            sent = await workflow.execute_activity(
                remove_config, args=[device, paths], start_to_close_timeout=_TIMEOUT, retry_policy=_SET_RETRY
            )
        except ActivityError as exc:
            if is_cancelled_exception(exc):
                raise
            error = _format_cause(exc, default="the remove activity failed")
            await _failed_step(device.id, "remove_config", {"paths": paths, "error": error})
            raise _StoppedError(WorkflowReason.REMOVE_FAILED, error) from exc
        if not sent.success:
            error = sent.error or "the device rejected the removal"
            await _failed_step(device.id, "remove_config", {"paths": paths, "error": error})
            raise _StoppedError(WorkflowReason.REMOVE_FAILED, error)
        state.deleted = paths
        state.device_response = sent.device_response
        await _step(
            REMOVE_CONFIG, device.id, "remove_config", payload={"paths": paths, "device_response": sent.device_response}
        )

    async def _end(
        self, state: _Removal, reason: WorkflowReason, detail: str | None, *, extra: dict | None = None
    ) -> RemovalResult:
        await record_event(
            workflow_type=REMOVE_CONFIG,
            target_id=state.request.device_id,
            event_type=AuditEventType.WORKFLOW_TERMINATED,
            outcome="success" if reason == WorkflowReason.SUCCEEDED else "failure",
            reason=reason,
            payload={**state.payload(reason, detail), **(extra or {})},
        )
        return state.result(reason, detail)


async def _no_deploy_running(device_id: UUID) -> None:
    """Stop with ``deploy_in_flight`` while ``deploy-intent-{device}`` runs (research R8)."""
    running = await workflow.execute_activity(
        deploy_in_flight, device_id, start_to_close_timeout=_TIMEOUT, retry_policy=_CHECK_RETRY
    )
    await _step(REMOVE_CONFIG, device_id, "deploy_in_flight")
    if running:
        raise _StoppedError(WorkflowReason.DEPLOY_IN_FLIGHT, "a deploy to the device is running; nothing was removed")


async def _failed_step(device_id: UUID, activity_name: str, payload: dict) -> None:
    await record_event(
        workflow_type=REMOVE_CONFIG,
        target_id=device_id,
        event_type=AuditEventType.ACTIVITY_FAILED,
        activity_name=activity_name,
        outcome="failure",
        payload=payload,
    )
