"""DeployIntentWorkflow — the closed-loop deploy workflow."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from snapl_intent.exceptions import (
        IntentConnectionError,
        IntentNotFoundError,
        IntentValidationError,
    )
    from snapl_observability.models import DriftStatus
    from snapl_orchestrator.activities.audit import record_audit_event
    from snapl_orchestrator.activities.collector import collect_running_state
    from snapl_orchestrator.activities.executor import apply_config
    from snapl_orchestrator.activities.intent import fetch_desired_state
    from snapl_orchestrator.activities.observability import detect_drift
    from snapl_orchestrator.models import (
        AuditEvent,
        AuditEventType,
        WorkflowReason,
        WorkflowResult,
    )

_WORKFLOW_TYPE = "DeployIntent"

_AUDIT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_attempts=5,
)
_INTENT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_attempts=3,
    non_retryable_error_types=[
        "IntentNotFoundError",
        "IntentValidationError",
    ],
)
_APPLY_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_attempts=3,
)
_COLLECT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_attempts=3,
)
_DETECT_RETRY = RetryPolicy(maximum_attempts=1)


async def _record_event(
    *,
    workflow_id: str,
    device_id: UUID,
    event_type: AuditEventType,
    activity_name: str | None = None,
    outcome: str | None = None,
    reason: WorkflowReason | None = None,
    payload: dict | None = None,
) -> None:
    event = AuditEvent(
        event_id=workflow.uuid4(),
        workflow_id=workflow_id,
        workflow_type=_WORKFLOW_TYPE,
        target_id=device_id,
        event_type=event_type,
        activity_name=activity_name,
        outcome=outcome,
        reason=reason,
        payload=payload or {},
        timestamp=workflow.now(),
    )
    await workflow.execute_activity(
        record_audit_event,
        event,
        start_to_close_timeout=timedelta(seconds=10),
        retry_policy=_AUDIT_RETRY,
    )


@workflow.defn(name="DeployIntent")
class DeployIntentWorkflow:
    """End-to-end durable deploy: intent → apply → collect → verify → audit."""

    @workflow.run
    async def run(self, device_id: UUID) -> WorkflowResult:  # noqa: PLR0911 — multiple terminal branches by design
        wf_id = workflow.info().workflow_id
        started_at = workflow.now()

        await _record_event(
            workflow_id=wf_id,
            device_id=device_id,
            event_type=AuditEventType.WORKFLOW_STARTED,
        )

        # ---- Fetch intent ---------------------------------------------------
        try:
            desired = await workflow.execute_activity(
                fetch_desired_state,
                device_id,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_INTENT_RETRY,
            )
        except (asyncio.CancelledError, ActivityError) as exc:
            if is_cancelled_exception(exc):
                return await self._terminate_cancelled(
                    wf_id=wf_id,
                    device_id=device_id,
                    started_at=started_at,
                )
            if _is_intent_not_found(exc):
                return await self._terminate_failure(
                    wf_id=wf_id,
                    device_id=device_id,
                    reason=WorkflowReason.DEVICE_NOT_FOUND,
                    detail=_format_cause(exc, default="device not found in SoT"),
                    started_at=started_at,
                )
            return await self._terminate_failure(
                wf_id=wf_id,
                device_id=device_id,
                reason=WorkflowReason.INTENT_UNAVAILABLE,
                detail=_format_cause(exc, default="intent fetch failed"),
                started_at=started_at,
            )

        await _record_event(
            workflow_id=wf_id,
            device_id=device_id,
            event_type=AuditEventType.ACTIVITY_COMPLETED,
            activity_name="fetch_desired_state",
            outcome="success",
        )

        # ---- Apply config ---------------------------------------------------
        try:
            apply_result = await workflow.execute_activity(
                apply_config,
                desired,
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=_APPLY_RETRY,
            )
        except (asyncio.CancelledError, ActivityError) as exc:
            if is_cancelled_exception(exc):
                return await self._terminate_cancelled(
                    wf_id=wf_id,
                    device_id=device_id,
                    started_at=started_at,
                )
            return await self._terminate_failure(
                wf_id=wf_id,
                device_id=device_id,
                reason=WorkflowReason.APPLY_FAILED,
                detail=_format_cause(exc, default="apply activity failed"),
                started_at=started_at,
            )

        if not apply_result.success:
            return await self._terminate_failure(
                wf_id=wf_id,
                device_id=device_id,
                reason=WorkflowReason.APPLY_FAILED,
                detail=apply_result.error or "apply reported success=False",
                started_at=started_at,
            )

        await _record_event(
            workflow_id=wf_id,
            device_id=device_id,
            event_type=AuditEventType.ACTIVITY_COMPLETED,
            activity_name="apply_config",
            outcome="success",
        )

        # ---- Collect verification state ------------------------------------
        applied_paths = list(apply_result.payload.keys()) if apply_result.payload else []
        try:
            collected = await workflow.execute_activity(
                collect_running_state,
                args=[desired.device, applied_paths],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_COLLECT_RETRY,
            )
        except (asyncio.CancelledError, ActivityError) as exc:
            if is_cancelled_exception(exc):
                return await self._terminate_cancelled(
                    wf_id=wf_id,
                    device_id=device_id,
                    started_at=started_at,
                )
            return await self._terminate_failure(
                wf_id=wf_id,
                device_id=device_id,
                reason=WorkflowReason.COLLECT_FAILED,
                detail=_format_cause(exc, default="collect activity failed"),
                started_at=started_at,
            )

        await _record_event(
            workflow_id=wf_id,
            device_id=device_id,
            event_type=AuditEventType.ACTIVITY_COMPLETED,
            activity_name="collect_running_state",
            outcome="success",
        )

        # ---- Verify via Observer -------------------------------------------
        try:
            report = await workflow.execute_activity(
                detect_drift,
                args=[desired, collected],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_DETECT_RETRY,
            )
        except (asyncio.CancelledError, ActivityError) as exc:
            if is_cancelled_exception(exc):
                return await self._terminate_cancelled(
                    wf_id=wf_id,
                    device_id=device_id,
                    started_at=started_at,
                )
            return await self._terminate_failure(
                wf_id=wf_id,
                device_id=device_id,
                reason=WorkflowReason.COLLECT_FAILED,
                detail=_format_cause(exc, default="drift detection failed"),
                started_at=started_at,
            )

        await _record_event(
            workflow_id=wf_id,
            device_id=device_id,
            event_type=AuditEventType.ACTIVITY_COMPLETED,
            activity_name="detect_drift",
            outcome="success",
        )

        # ---- Terminal classification ---------------------------------------
        if report.status == DriftStatus.CLEAN:
            return await self._terminate_success(
                wf_id=wf_id,
                device_id=device_id,
                started_at=started_at,
            )
        if report.status == DriftStatus.DRIFTED:
            return await self._terminate_verification_failed(
                wf_id=wf_id,
                device_id=device_id,
                drift_items=list(report.items),
                started_at=started_at,
            )
        # DriftStatus.ERROR
        return await self._terminate_failure(
            wf_id=wf_id,
            device_id=device_id,
            reason=WorkflowReason.COLLECT_FAILED,
            detail=report.error or "drift detection returned ERROR status",
            started_at=started_at,
        )

    # ---- Terminal helpers ----------------------------------------------------

    async def _terminate_success(
        self,
        *,
        wf_id: str,
        device_id: UUID,
        started_at,
    ) -> WorkflowResult:
        await _record_event(
            workflow_id=wf_id,
            device_id=device_id,
            event_type=AuditEventType.WORKFLOW_TERMINATED,
            outcome="success",
            reason=WorkflowReason.SUCCEEDED,
        )
        return WorkflowResult(
            workflow_id=wf_id,
            workflow_type=_WORKFLOW_TYPE,
            target_id=device_id,
            success=True,
            reason=WorkflowReason.SUCCEEDED,
            started_at=started_at,
            ended_at=workflow.now(),
        )

    async def _terminate_failure(
        self,
        *,
        wf_id: str,
        device_id: UUID,
        reason: WorkflowReason,
        detail: str,
        started_at,
    ) -> WorkflowResult:
        await _record_event(
            workflow_id=wf_id,
            device_id=device_id,
            event_type=AuditEventType.WORKFLOW_TERMINATED,
            outcome="failure",
            reason=reason,
            payload={"detail": detail},
        )
        return WorkflowResult(
            workflow_id=wf_id,
            workflow_type=_WORKFLOW_TYPE,
            target_id=device_id,
            success=False,
            reason=reason,
            detail=detail,
            started_at=started_at,
            ended_at=workflow.now(),
        )

    async def _terminate_verification_failed(
        self,
        *,
        wf_id: str,
        device_id: UUID,
        drift_items,
        started_at,
    ) -> WorkflowResult:
        await _record_event(
            workflow_id=wf_id,
            device_id=device_id,
            event_type=AuditEventType.WORKFLOW_TERMINATED,
            outcome="failure",
            reason=WorkflowReason.VERIFICATION_FAILED,
            payload={"drift_paths": [item.path for item in drift_items]},
        )
        return WorkflowResult(
            workflow_id=wf_id,
            workflow_type=_WORKFLOW_TYPE,
            target_id=device_id,
            success=False,
            reason=WorkflowReason.VERIFICATION_FAILED,
            detail=f"post-apply drift on {len(drift_items)} path(s)",
            started_at=started_at,
            ended_at=workflow.now(),
            drift_items=drift_items,
        )

    async def _terminate_cancelled(
        self,
        *,
        wf_id: str,
        device_id: UUID,
        started_at,
    ) -> WorkflowResult:
        await _record_event(
            workflow_id=wf_id,
            device_id=device_id,
            event_type=AuditEventType.WORKFLOW_CANCELLED,
            outcome="cancelled",
        )
        return WorkflowResult(
            workflow_id=wf_id,
            workflow_type=_WORKFLOW_TYPE,
            target_id=device_id,
            success=False,
            reason=WorkflowReason.CANCELLED,
            detail="workflow cancelled by caller",
            started_at=started_at,
            ended_at=workflow.now(),
        )


def _is_intent_not_found(exc: BaseException) -> bool:
    """True if an activity failure originates from IntentNotFoundError (device absent from SoT)."""
    if not isinstance(exc, ActivityError):
        return False
    return isinstance(exc.cause, ApplicationError) and exc.cause.type == IntentNotFoundError.__name__


def _format_cause(exc: BaseException, *, default: str) -> str:
    """Pull a human-readable detail string out of an ActivityError chain."""
    cause = exc.__cause__
    if isinstance(cause, ApplicationError):
        return cause.message or default
    if cause is not None:
        return str(cause) or default
    return str(exc) or default


# Silence unused-import warnings for exception types referenced only in retry policies.
_ = (IntentConnectionError, IntentNotFoundError, IntentValidationError)
