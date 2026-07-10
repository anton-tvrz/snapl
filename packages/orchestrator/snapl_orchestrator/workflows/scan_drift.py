"""ScanDriftWorkflow — read-only drift evaluation across a use case."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from snapl_collector.models import CollectResult
    from snapl_observability.models import DriftReport, DriftStatus
    from snapl_orchestrator.activities.audit import record_audit_event
    from snapl_orchestrator.activities.collector import collect_running_state
    from snapl_orchestrator.activities.intent import (
        fetch_desired_state,
        fetch_devices_for_use_case,
    )
    from snapl_orchestrator.activities.observability import detect_drift
    from snapl_orchestrator.adapters.srlinux import DRIFT_PATHS
    from snapl_orchestrator.models import (
        AuditEvent,
        AuditEventType,
        DriftScanResult,
        WorkflowReason,
    )


_WORKFLOW_TYPE = "ScanDrift"

_AUDIT_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=5)
_INTENT_RETRY = RetryPolicy(initial_interval=timedelta(seconds=2), maximum_attempts=3)
_COLLECT_RETRY = RetryPolicy(initial_interval=timedelta(seconds=2), maximum_attempts=3)
_DETECT_RETRY = RetryPolicy(maximum_attempts=1)


async def _audit(
    *,
    workflow_id: str,
    use_case_id: str,
    event_type: AuditEventType,
    outcome: str | None = None,
    reason: WorkflowReason | None = None,
    payload: dict | None = None,
) -> None:
    event = AuditEvent(
        event_id=workflow.uuid4(),
        workflow_id=workflow_id,
        workflow_type=_WORKFLOW_TYPE,
        target_id=use_case_id,
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


@workflow.defn(name="ScanDrift")
class ScanDriftWorkflow:
    """Evaluate drift for every device in a use case in parallel."""

    @workflow.run
    async def run(self, use_case_id: str) -> DriftScanResult:
        wf_id = workflow.info().workflow_id
        started_at = workflow.now()

        await _audit(
            workflow_id=wf_id,
            use_case_id=use_case_id,
            event_type=AuditEventType.WORKFLOW_STARTED,
        )

        try:
            devices = await workflow.execute_activity(
                fetch_devices_for_use_case,
                use_case_id,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_INTENT_RETRY,
            )
        except (asyncio.CancelledError, ActivityError) as exc:
            if is_cancelled_exception(exc):
                return await self._cancel(
                    wf_id=wf_id,
                    use_case_id=use_case_id,
                    started_at=started_at,
                    partial_reports={},
                )
            raise

        try:
            reports = await asyncio.gather(
                *(self._evaluate_device(d) for d in devices),
                return_exceptions=False,
            )
        except (asyncio.CancelledError, ActivityError) as exc:
            if is_cancelled_exception(exc):
                return await self._cancel(
                    wf_id=wf_id,
                    use_case_id=use_case_id,
                    started_at=started_at,
                    partial_reports={},
                )
            raise
        reports_map = {r.device_id: r for r in reports}

        clean = sum(1 for r in reports if r.status == DriftStatus.CLEAN)
        drifted = sum(1 for r in reports if r.status == DriftStatus.DRIFTED)
        errored = sum(1 for r in reports if r.status == DriftStatus.ERROR)

        await _audit(
            workflow_id=wf_id,
            use_case_id=use_case_id,
            event_type=AuditEventType.WORKFLOW_TERMINATED,
            outcome="success",
            reason=WorkflowReason.SUCCEEDED,
            payload={
                "total": len(reports),
                "clean": clean,
                "drifted": drifted,
                "errored": errored,
            },
        )

        return DriftScanResult(
            workflow_id=wf_id,
            use_case_id=use_case_id,
            reports=reports_map,
            total=len(reports),
            clean=clean,
            drifted=drifted,
            errored=errored,
            started_at=started_at,
            ended_at=workflow.now(),
        )

    async def _evaluate_device(self, device) -> DriftReport:
        try:
            desired = await workflow.execute_activity(
                fetch_desired_state,
                device.id,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_INTENT_RETRY,
            )
        except ActivityError as exc:
            if is_cancelled_exception(exc):
                raise
            return DriftReport(
                device_id=device.id,
                device_name=device.name,
                status=DriftStatus.ERROR,
                items=[],
                error=f"intent fetch failed: {exc}",
                timestamp=workflow.now(),
            )

        try:
            collected: CollectResult = await workflow.execute_activity(
                collect_running_state,
                args=[device, list(DRIFT_PATHS)],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_COLLECT_RETRY,
            )
        except ActivityError as exc:
            if is_cancelled_exception(exc):
                raise
            return DriftReport(
                device_id=device.id,
                device_name=device.name,
                status=DriftStatus.ERROR,
                items=[],
                error=f"collect failed: {exc}",
                timestamp=workflow.now(),
            )

        try:
            return await workflow.execute_activity(
                detect_drift,
                args=[desired, collected],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_DETECT_RETRY,
            )
        except ActivityError as exc:
            if is_cancelled_exception(exc):
                raise
            return DriftReport(
                device_id=device.id,
                device_name=device.name,
                status=DriftStatus.ERROR,
                items=[],
                error=f"detect_drift failed: {exc}",
                timestamp=workflow.now(),
            )

    async def _cancel(
        self,
        *,
        wf_id: str,
        use_case_id: str,
        started_at,
        partial_reports: dict,
    ) -> DriftScanResult:
        await _audit(
            workflow_id=wf_id,
            use_case_id=use_case_id,
            event_type=AuditEventType.WORKFLOW_CANCELLED,
            outcome="cancelled",
        )
        return DriftScanResult(
            workflow_id=wf_id,
            use_case_id=use_case_id,
            reports=partial_reports,
            total=len(partial_reports),
            clean=0,
            drifted=0,
            errored=len(partial_reports),
            started_at=started_at,
            ended_at=workflow.now(),
        )
