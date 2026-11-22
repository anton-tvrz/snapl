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
from temporalio.exceptions import ActivityError, ApplicationError, ChildWorkflowError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from snapl_orchestrator.activities.schedule import stand_down_schedule
    from snapl_orchestrator.models import (
        AuditEventType,
        DriftResponseMode,
        DriftResponseRequest,
        DriftResponseResult,
        DriftScanResult,
        ExclusionReason,
        ReconcileResult,
        StandDownRequest,
        WorkflowReason,
    )
    from snapl_orchestrator.remediation import select_remediation
    from snapl_orchestrator.workflows.audit import record_event
    from snapl_orchestrator.workflows.reconcile_devices import ReconcileDevicesWorkflow
    from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow


if TYPE_CHECKING:
    from uuid import UUID

_WORKFLOW_TYPE = "DriftResponse"

# Patch id for starting the reconcile child with ABANDON (#144). Runs already in
# flight when this shipped replay their original start command without it.
_ABANDON_REPAIR_PATCH = "drift-response-abandon-repair-on-close"


# Patch id for standing the schedule down after a failed repair (#136). A run
# in flight when this shipped replays without the extra activity.
_STAND_DOWN_PATCH = "drift-response-stand-down-after-failed-repair"
_STAND_DOWN_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3)

# Patch id for the blast-radius guard (spec 018, #136). A run in flight when
# this shipped replays its repair as it ran.
_GUARD_PATCH = "drift-response-blast-radius-guard"


def _too_wide(forward: int, total: int) -> bool:
    """More than one device with forward drift, and more than half of the scan (FR-003).

    The threshold is fixed at one half. ``total`` is every device the scan
    covered, errored ones included. One device is not a blast radius.
    """
    return forward > 1 and forward * 2 > total


async def _record(
    *,
    request: DriftResponseRequest,
    target_id,
    event_type: AuditEventType,
    outcome: str | None = None,
    reason: WorkflowReason | None = None,
    payload: dict | None = None,
) -> None:
    await record_event(
        workflow_type=_WORKFLOW_TYPE,
        target_id=target_id,
        event_type=event_type,
        outcome=outcome,
        reason=reason,
        payload=payload,
        # Unattended actions must be told apart from human ones in `snapl audit`.
        actor=f"schedule:{request.schedule_id}",
    )


async def _stand_down(request: DriftResponseRequest, reason: str) -> bool:
    """Return the schedule to report after a failed repair or a held guard (#136). Whether it changed.

    If the activity fails after its retries, the schedule stays in remediate
    and the next run tries again, as before #136: the repair's outcome is
    already recorded, so the run carries on.
    """
    try:
        return await workflow.execute_activity(
            stand_down_schedule,
            StandDownRequest(use_case_id=request.use_case_id, reason=reason),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_STAND_DOWN_RETRY,
        )
    except ActivityError as exc:
        if is_cancelled_exception(exc):
            raise
        workflow.logger.warning("could not stand down %s: %s", request.schedule_id, exc)
        return False


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
    guard: str | None = None,
) -> dict:
    excluded_counts: dict[str, int] = {}
    for reason in excluded.values():
        excluded_counts[reason.value] = excluded_counts.get(reason.value, 0) + 1
    payload: dict[str, Any] = {
        "mode": mode.value,
        "scan_workflow_id": scan.workflow_id,
        "clean": scan.clean,
        "drifted": scan.drifted,
        "errored": scan.errored,
        "remediated": len(remediated),
        "excluded": excluded_counts,
        "reconcile_workflow_id": reconcile_workflow_id,
    }
    if guard is not None:
        # Why the run repaired nothing: the note it left on the schedule (#136).
        payload["guard"] = guard
    return payload


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
        guard: str | None = None
        if request.mode is DriftResponseMode.REMEDIATE:
            # Only forward drift: an undesired-only device would be rewritten
            # every interval without converging, and an errored one was never
            # seen (FR-013). The repair is the operator's reconcile path,
            # unchanged — per-device serialization and in-flight skips included.
            remediated, excluded = select_remediation(scan)
            if _too_wide(len(remediated), scan.total) and workflow.patched(_GUARD_PATCH):
                # A bad intent edit, such as a wrong ASN, drifts every device at
                # once, and repairing would push it to all of them (#136). So
                # the run repairs nothing and the schedule goes back to report.
                guard = f"{wf_id}: {len(remediated)} of {scan.total} devices drifted, more than half; repaired nothing"
                excluded = {**dict.fromkeys(remediated, ExclusionReason.BLAST_RADIUS), **excluded}
                remediated = []
            if remediated:
                # ABANDON: when this run hits RUN_TIMEOUT the server closes it
                # without running workflow code. With the default TERMINATE that
                # also kills the repair, possibly between a DeployIntent's apply
                # and its verify, so no rollback runs and nothing records the cut
                # (#144). Abandoned, the repair finishes and writes its own
                # records. A later run can't collide with it: reconcile skips a
                # device whose deploy is still in flight. An operator cancel of
                # this run still cancels the repair (the default cancellation
                # type is unaffected by the close policy).
                close_policy = (
                    workflow.ParentClosePolicy.ABANDON
                    if workflow.patched(_ABANDON_REPAIR_PATCH)
                    else workflow.ParentClosePolicy.TERMINATE
                )
                try:
                    reconcile = await workflow.execute_child_workflow(
                        ReconcileDevicesWorkflow.run,
                        remediated,
                        id=f"{wf_id}-reconcile",
                        parent_close_policy=close_policy,
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
                guard=guard,
            ),
        )
        if reconcile_failure is not None:
            # The repair did not complete: its deploys may have written, and no
            # per-device result came back. Retrying it every interval is the
            # same loop as a failed device, so the schedule stands down too.
            # An operator's cancel is not a failed repair (#136).
            if not is_cancelled_exception(reconcile_failure) and workflow.patched(_STAND_DOWN_PATCH):
                cause = getattr(reconcile_failure, "cause", None) or reconcile_failure
                await _stand_down(request, f"{wf_id}: the repair did not complete ({cause})")
            await _record_end(request, reconcile_failure, reason=WorkflowReason.REMEDIATION_FAILED)
            raise reconcile_failure
        stood_down = False
        if guard is not None:
            stood_down = await _stand_down(request, guard)
        if reconcile is not None and reconcile.failed > 0 and workflow.patched(_STAND_DOWN_PATCH):
            # A device that rejects its intent would otherwise get one failed
            # repair per interval, indefinitely (#136, FR-018). A device skipped
            # because a deploy was in flight is not a failure. Detection keeps
            # running: the schedule goes back to report, it is not paused.
            failed = sorted(
                scan.reports[device_id].device_name
                for device_id, outcome in reconcile.device_results.items()
                if not outcome.success
            )
            stood_down = await _stand_down(request, f"{wf_id}: the repair failed on {', '.join(failed)}")
        result = DriftResponseResult(
            workflow_id=wf_id,
            schedule_id=request.schedule_id,
            use_case_id=request.use_case_id,
            mode=request.mode,
            scan=scan,
            remediated=remediated,
            excluded=excluded,
            reconcile=reconcile,
            stood_down=stood_down,
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
