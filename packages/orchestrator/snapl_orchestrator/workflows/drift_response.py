"""DriftResponseWorkflow — one scheduled run: scan a use case, then apply the schedule's response.

Started by a drift schedule (spec 009), never by the CLI directly. The scan is
the unchanged ScanDrift, run as a child so it keeps its own id, audit trail and
Web UI page. The response mode arrives in the request and is fixed for the
whole run (FR-016).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ChildWorkflowError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from snapl_orchestrator.activities.audit import record_audit_event
    from snapl_orchestrator.models import (
        AuditEvent,
        AuditEventType,
        DriftResponseMode,
        DriftResponseRequest,
        DriftResponseResult,
        DriftScanResult,
        ReconcileResult,
        WorkflowReason,
    )
    from snapl_orchestrator.remediation import select_remediation
    from snapl_orchestrator.workflows.reconcile_devices import ReconcileDevicesWorkflow
    from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow


_WORKFLOW_TYPE = "DriftResponse"

_AUDIT_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=5)


async def _record(
    *,
    request: DriftResponseRequest,
    target_id,
    event_type: AuditEventType,
    outcome: str | None = None,
    reason: WorkflowReason | None = None,
    payload: dict | None = None,
) -> None:
    event = AuditEvent(
        event_id=workflow.uuid4(),
        workflow_id=workflow.info().workflow_id,
        workflow_type=_WORKFLOW_TYPE,
        target_id=target_id,
        event_type=event_type,
        outcome=outcome,
        reason=reason,
        payload=payload or {},
        timestamp=workflow.now(),
        # Unattended actions must be told apart from human ones in `snapl audit`.
        actor=f"schedule:{request.schedule_id}",
    )
    await workflow.execute_activity(
        record_audit_event,
        event,
        start_to_close_timeout=timedelta(seconds=10),
        retry_policy=_AUDIT_RETRY,
    )


def _use_case_payload(result: DriftResponseResult) -> dict:
    excluded: dict[str, int] = {}
    for reason in result.excluded.values():
        excluded[reason.value] = excluded.get(reason.value, 0) + 1
    return {
        "mode": result.mode.value,
        "scan_workflow_id": result.scan.workflow_id,
        "clean": result.scan.clean,
        "drifted": result.scan.drifted,
        "errored": result.scan.errored,
        "remediated": len(result.remediated),
        "excluded": excluded,
        "reconcile_workflow_id": result.reconcile.workflow_id if result.reconcile else None,
    }


def _device_payloads(result: DriftResponseResult) -> list[tuple]:
    """One record per repaired or excluded device, so `snapl audit --device`
    shows "repaired by schedule X from scan Y" or "left alone, and why" (FR-020)."""
    records = []
    for device_id in result.remediated:
        report = result.scan.reports[device_id]
        outcome = result.reconcile.device_results.get(device_id) if result.reconcile else None
        records.append(
            (
                device_id,
                {
                    "decision": "remediated",
                    "scan_workflow_id": result.scan.workflow_id,
                    "reconcile_workflow_id": result.reconcile.workflow_id if result.reconcile else None,
                    # No device result means reconcile skipped it (a deploy was in flight).
                    "result": "skipped" if outcome is None else ("succeeded" if outcome.success else "failed"),
                    "paths": [item.path for item in report.items],
                },
            )
        )
    for device_id, reason in result.excluded.items():
        report = result.scan.reports[device_id]
        payload = {
            "decision": reason.value,
            "scan_workflow_id": result.scan.workflow_id,
            "paths": [item.path for item in report.items],
        }
        if report.error:
            payload["error"] = report.error
        records.append((device_id, payload))
    return records


@workflow.defn(name="DriftResponse")
class DriftResponseWorkflow:
    """Scan a use case, then report (write nothing) or remediate (reconcile forward drift)."""

    @workflow.run
    async def run(self, request: DriftResponseRequest) -> DriftResponseResult:
        wf_id = workflow.info().workflow_id
        started_at = workflow.now()

        await _record(
            request=request,
            target_id=request.use_case_id,
            event_type=AuditEventType.WORKFLOW_STARTED,
            payload={"mode": request.mode.value},
        )

        try:
            # TRY_CANCEL: ScanDrift absorbs a cancellation and *returns* an
            # empty result, so waiting for it would turn a cancelled run into
            # a clean-looking one. The child still records its own cancellation.
            scan: DriftScanResult = await workflow.execute_child_workflow(
                ScanDriftWorkflow.run,
                request.use_case_id,
                id=f"{wf_id}-scan",
                cancellation_type=workflow.ChildWorkflowCancellationType.TRY_CANCEL,
            )
            remediated: list = []
            excluded: dict = {}
            reconcile: ReconcileResult | None = None
            if request.mode is DriftResponseMode.REMEDIATE:
                # Only forward drift: an undesired-only device would be rewritten
                # every interval without converging, and an errored one was never
                # seen (FR-013). The repair is the operator's reconcile path,
                # unchanged — per-device serialization and in-flight skips included.
                remediated, excluded = select_remediation(scan)
                if remediated:
                    reconcile = await workflow.execute_child_workflow(
                        ReconcileDevicesWorkflow.run, remediated, id=f"{wf_id}-reconcile"
                    )
            result = DriftResponseResult(
                workflow_id=wf_id,
                schedule_id=request.schedule_id,
                use_case_id=request.use_case_id,
                mode=request.mode,
                scan=scan,
                remediated=remediated,
                excluded=excluded,
                reconcile=reconcile,
                started_at=started_at,
                ended_at=workflow.now(),
            )
        except (asyncio.CancelledError, ChildWorkflowError) as exc:
            if is_cancelled_exception(exc):
                await _record(
                    request=request,
                    target_id=request.use_case_id,
                    event_type=AuditEventType.WORKFLOW_CANCELLED,
                    outcome="cancelled",
                )
                raise
            # The scan itself failed (Source of Truth unreachable): nothing was
            # evaluated, so nothing can be reported or repaired. Re-raise so the
            # run shows as failed in the schedule's recent runs.
            await _record(
                request=request,
                target_id=request.use_case_id,
                event_type=AuditEventType.WORKFLOW_TERMINATED,
                outcome="failure",
                reason=WorkflowReason.INTENT_UNAVAILABLE,
                payload={"error": str(exc.cause or exc)},
            )
            raise

        for device_id, payload in _device_payloads(result):
            await _record(
                request=request,
                target_id=device_id,
                event_type=AuditEventType.DRIFT_RESPONSE,
                payload=payload,
            )
        await _record(
            request=request,
            target_id=request.use_case_id,
            event_type=AuditEventType.DRIFT_RESPONSE,
            payload=_use_case_payload(result),
        )
        await _record(
            request=request,
            target_id=request.use_case_id,
            event_type=AuditEventType.WORKFLOW_TERMINATED,
            outcome="success",
            reason=WorkflowReason.SUCCEEDED,
        )
        return result
