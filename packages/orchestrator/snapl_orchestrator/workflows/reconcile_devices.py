"""ReconcileDevicesWorkflow — operator-initiated per-device reconciliation."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import (
    ChildWorkflowError,
    WorkflowAlreadyStartedError,
    is_cancelled_exception,
)

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

        # Dedupe before dispatching. Two identical ids would start two children
        # with the same deterministic id — the second raising past the
        # ChildWorkflowError handler and failing the whole run — and even
        # serialized would trip ReconcileResult's
        # ``len(device_results) + skipped == total`` validator, since
        # device_results is keyed by UUID and duplicates collapse (#66).
        # Order-preserving: callers read reconcile results positionally.
        targets = list(dict.fromkeys(device_ids))
        duplicates = len(device_ids) - len(targets)

        wf_id = workflow.info().workflow_id
        started_at = workflow.now()

        await _audit(
            workflow_id=wf_id,
            event_type=AuditEventType.WORKFLOW_STARTED,
            payload={"device_count": len(targets), "duplicate_ids_dropped": duplicates},
        )

        try:
            outcomes = await asyncio.gather(
                *(self._deploy_one(device_id) for device_id in targets),
            )
        except (asyncio.CancelledError, ChildWorkflowError) as exc:
            if not is_cancelled_exception(exc):
                raise
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
        for device_id, outcome in zip(targets, outcomes, strict=True):
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
                "total": len(targets),
                "succeeded": succeeded,
                "failed": failed,
                "skipped": skipped,
            },
        )

        return ReconcileResult(
            workflow_id=wf_id,
            device_results=device_results,
            total=len(targets),
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
            started_at=started_at,
            ended_at=workflow.now(),
        )

    async def _deploy_one(self, device_id: UUID) -> WorkflowResult | None:
        """Run DeployIntent for one device. Returns None if the device is skipped."""
        try:
            # Deterministic child id enforces per-device serialization (FR-009):
            # it is the same id family the operator entry point uses, so a deploy
            # already in flight for this device collides here — deliberately.
            result: WorkflowResult = await workflow.execute_child_workflow(
                DeployIntentWorkflow.run,
                device_id,
                id=f"deploy-intent-{device_id}",
            )
        except WorkflowAlreadyStartedError:
            # A deploy for this device is already running — started by an operator
            # or an overlapping reconcile. Reconcile's goal for it is already being
            # met, so skip rather than failing the whole batch (#35). Not joined to
            # the existing run: its result is not ours to report, and awaiting it
            # would make this run's duration depend on a workflow we do not control.
            return None
        except ChildWorkflowError as exc:
            if is_cancelled_exception(exc):
                raise
            # Everything reaching here is an unexpected child failure. DeployIntent
            # *returns* WorkflowReason.DEVICE_NOT_FOUND rather than raising, so the
            # legitimate skip is handled below on the structured reason. The old
            # 'not found' substring test could only ever fire on genuine failures —
            # a gNMI "Requested element(s) not found", a missing-table audit error —
            # and silently recorded them as skips (#66).
            message = str(exc.cause) if exc.cause else str(exc)
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
