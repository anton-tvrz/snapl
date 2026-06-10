"""ReconcileDevicesWorkflow — operator-initiated per-device reconciliation."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import CancelledError, ChildWorkflowError

with workflow.unsafe.imports_passed_through():
    from snapl_orchestrator.activities.audit import record_audit_event
    from snapl_orchestrator.models import (
        AuditEvent,
        AuditEventType,
        ReconcileResult,
        WorkflowReason,
        WorkflowResult,
    )
    from snapl_orchestrator.workflows.deploy_intent import DeployIntentWorkflow


_WORKFLOW_TYPE = "ReconcileDevices"
_AUDIT_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=5)


async def _audit(
    *,
    workflow_id: str,
    event_type: AuditEventType,
    target_id: UUID | str | None = None,
    outcome: str | None = None,
    reason: WorkflowReason | None = None,
    payload: dict | None = None,
) -> None:
    event = AuditEvent(
        event_id=workflow.uuid4(),
        workflow_id=workflow_id,
        workflow_type=_WORKFLOW_TYPE,
        target_id=target_id,
        event_type=event_type,
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


@workflow.defn(name="ReconcileDevices")
class ReconcileDevicesWorkflow:
    """Run DeployIntentWorkflow as a child for each target device."""

    @workflow.run
    async def run(self, device_ids: list[UUID]) -> ReconcileResult:
        if not device_ids:
            raise ValueError("device_ids must be non-empty")

        wf_id = workflow.info().workflow_id
        started_at = workflow.now()

        await _audit(
            workflow_id=wf_id,
            event_type=AuditEventType.WORKFLOW_STARTED,
            payload={"device_count": len(device_ids)},
        )

        try:
            outcomes = await asyncio.gather(
                *(self._deploy_one(device_id) for device_id in device_ids),
            )
        except CancelledError:
            await _audit(
                workflow_id=wf_id,
                event_type=AuditEventType.WORKFLOW_CANCELLED,
                outcome="cancelled",
            )
            raise

        device_results: dict[UUID, WorkflowResult] = {}
        skipped = 0
        succeeded = 0
        failed = 0
        for device_id, outcome in zip(device_ids, outcomes, strict=False):
            if outcome is None:
                skipped += 1
                continue
            device_results[device_id] = outcome
            if outcome.success:
                succeeded += 1
            else:
                failed += 1

        await _audit(
            workflow_id=wf_id,
            event_type=AuditEventType.WORKFLOW_TERMINATED,
            outcome="success",
            reason=WorkflowReason.SUCCEEDED,
            payload={
                "total": len(device_ids),
                "succeeded": succeeded,
                "failed": failed,
                "skipped": skipped,
            },
        )

        return ReconcileResult(
            workflow_id=wf_id,
            device_results=device_results,
            total=len(device_ids),
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
            started_at=started_at,
            ended_at=workflow.now(),
        )

    async def _deploy_one(self, device_id: UUID) -> WorkflowResult | None:
        """Run DeployIntent for one device. Returns None if the device is skipped."""
        try:
            # Deterministic child id dedupes within this reconcile run. Cross-invocation
            # per-device serialization (FR-009) is enforced at the entry point where the
            # workflow is started (client id-conflict policy), not on the child call —
            # execute_child_workflow has no id_conflict_policy parameter.
            result: WorkflowResult = await workflow.execute_child_workflow(
                DeployIntentWorkflow.run,
                device_id,
                id=f"deploy-intent-{device_id}",
            )
        except ChildWorkflowError as exc:
            # Treat child-workflow init failures (device not found in SoT) as skipped.
            message = str(exc.cause) if exc.cause else str(exc)
            if "device_not_found" in message.lower() or "not found" in message.lower():
                return None
            return WorkflowResult(
                workflow_id=f"deploy-intent-{device_id}",
                workflow_type="DeployIntent",
                target_id=device_id,
                success=False,
                reason=WorkflowReason.APPLY_FAILED,
                detail=f"child workflow failed: {message}",
                started_at=workflow.now(),
                ended_at=workflow.now(),
            )
        if result.reason == WorkflowReason.DEVICE_NOT_FOUND:
            return None
        return result
