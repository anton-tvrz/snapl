"""DriftResponseWorkflow — one scheduled run: scan a use case, then apply the schedule's response.

Started by a drift schedule (spec 009), never by the CLI directly. The scan is
the unchanged ScanDrift, run as a child so it keeps its own id, audit trail and
Web UI page. The response mode arrives in the request and is fixed for the
whole run (FR-016).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError, ChildWorkflowError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from snapl_orchestrator.activities.audit import record_audit_event
    from snapl_orchestrator.models import (
        AuditEvent,
        AuditEventType,
        DriftResponseMode,
        DriftResponseRequest,
        DriftResponseResult,
        DriftScanResult,
        ExclusionReason,
        ReconcileResult,
        WorkflowReason,
    )
    from snapl_orchestrator.remediation import select_remediation
    from snapl_orchestrator.workflows.reconcile_devices import ReconcileDevicesWorkflow
    from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow


if TYPE_CHECKING:
    from uuid import UUID

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


async def _record_end(request: DriftResponseRequest, exc: BaseException, *, reason: WorkflowReason) -> None:
    """The run's last record when a child it waited on did not complete."""
    if is_cancelled_exception(exc):
        await _record(
            request=request,
            target_id=request.use_case_id,
            event_type=AuditEventType.WORKFLOW_CANCELLED,
            outcome="cancelled",
        )
        return
    await _record(
        request=request,
        target_id=request.use_case_id,
        event_type=AuditEventType.WORKFLOW_TERMINATED,
        outcome="failure",
        reason=reason,
        payload={"error": str(getattr(exc, "cause", None) or exc)},
    )


def _use_case_payload(
    *,
    mode: DriftResponseMode,
    scan: DriftScanResult,
    remediated: list[UUID],
    excluded: dict[UUID, ExclusionReason],
    reconcile_workflow_id: str | None,
) -> dict:
    excluded_counts: dict[str, int] = {}
    for reason in excluded.values():
        excluded_counts[reason.value] = excluded_counts.get(reason.value, 0) + 1
    return {
        "mode": mode.value,
        "scan_workflow_id": scan.workflow_id,
        "clean": scan.clean,
        "drifted": scan.drifted,
        "errored": scan.errored,
        "remediated": len(remediated),
        "excluded": excluded_counts,
        "reconcile_workflow_id": reconcile_workflow_id,
    }


def _remediation_result(reconcile: ReconcileResult | None, device_id: UUID) -> str:
    if reconcile is None:
        # Reconcile failed as a whole, before reporting per-device results; its
        # DeployIntent children's own records say what was written.
        return "unknown"
    outcome = reconcile.device_results.get(device_id)
    # No device result means reconcile skipped it (a deploy was in flight).
    if outcome is None:
        return "skipped"
    return "succeeded" if outcome.success else "failed"


def _device_payloads(
    *,
    scan: DriftScanResult,
    remediated: list[UUID],
    excluded: dict[UUID, ExclusionReason],
    reconcile: ReconcileResult | None,
    reconcile_workflow_id: str | None,
) -> list[tuple[UUID, dict[str, Any]]]:
    """One record per repaired or excluded device, so `snapl audit --device`
    shows "repaired by schedule X from scan Y" or "left alone, and why" (FR-020)."""
    records: list[tuple[UUID, dict[str, Any]]] = []
    for device_id in remediated:
        report = scan.reports[device_id]
        records.append(
            (
                device_id,
                {
                    "decision": "remediated",
                    "scan_workflow_id": scan.workflow_id,
                    "reconcile_workflow_id": reconcile_workflow_id,
                    "result": _remediation_result(reconcile, device_id),
                    "paths": [item.path for item in report.items],
                },
            )
        )
    for device_id, reason in excluded.items():
        report = scan.reports[device_id]
        payload: dict[str, Any] = {
            "decision": reason.value,
            "scan_workflow_id": scan.workflow_id,
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
        except (asyncio.CancelledError, ChildWorkflowError) as exc:
            # The scan itself failed (Source of Truth unreachable): nothing was
            # evaluated, so nothing can be reported or repaired. Re-raise so the
            # run shows as failed in the schedule's recent runs.
            await _record_end(request, exc, reason=WorkflowReason.INTENT_UNAVAILABLE)
            raise
        if scan.cancelled:
            # Cancelled on its own (this run's cancellation raises above): its
            # empty result is not a clean scan of zero devices.
            error = f"scan {scan.workflow_id} was cancelled; nothing was evaluated"
            await _record(
                request=request,
                target_id=request.use_case_id,
                event_type=AuditEventType.WORKFLOW_TERMINATED,
                outcome="failure",
                reason=WorkflowReason.CANCELLED,
                payload={"error": error},
            )
            raise ApplicationError(error, non_retryable=True)

        remediated: list[UUID] = []
        excluded: dict[UUID, ExclusionReason] = {}
        reconcile: ReconcileResult | None = None
        reconcile_failure: BaseException | None = None
        if request.mode is DriftResponseMode.REMEDIATE:
            # Only forward drift: an undesired-only device would be rewritten
            # every interval without converging, and an errored one was never
            # seen (FR-013). The repair is the operator's reconcile path,
            # unchanged — per-device serialization and in-flight skips included.
            remediated, excluded = select_remediation(scan)
            if remediated:
                try:
                    reconcile = await workflow.execute_child_workflow(
                        ReconcileDevicesWorkflow.run, remediated, id=f"{wf_id}-reconcile"
                    )
                except (asyncio.CancelledError, ChildWorkflowError) as exc:
                    # Its deploys may already have written, so every decision is
                    # still recorded below before the run fails (FR-020).
                    reconcile_failure = exc
        reconcile_workflow_id = f"{wf_id}-reconcile" if remediated else None

        for device_id, payload in _device_payloads(
            scan=scan,
            remediated=remediated,
            excluded=excluded,
            reconcile=reconcile,
            reconcile_workflow_id=reconcile_workflow_id,
        ):
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
            payload=_use_case_payload(
                mode=request.mode,
                scan=scan,
                remediated=remediated,
                excluded=excluded,
                reconcile_workflow_id=reconcile_workflow_id,
            ),
        )
        if reconcile_failure is not None:
            await _record_end(request, reconcile_failure, reason=WorkflowReason.REMEDIATION_FAILED)
            raise reconcile_failure
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
        await _record(
            request=request,
            target_id=request.use_case_id,
            event_type=AuditEventType.WORKFLOW_TERMINATED,
            outcome="success",
            reason=WorkflowReason.SUCCEEDED,
        )
        return result
