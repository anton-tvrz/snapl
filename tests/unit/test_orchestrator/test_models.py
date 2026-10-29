"""Unit tests for snapl_orchestrator.models — pydantic invariants."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from snapl_observability.models import DriftItem, DriftReport, DriftStatus
from snapl_orchestrator.models import (
    AuditEvent,
    AuditEventType,
    DriftResponseMode,
    DriftResponseRequest,
    DriftResponseResult,
    DriftScanResult,
    DriftScheduleView,
    ExclusionReason,
    ReconcileResult,
    RunSummary,
    ScheduleChange,
    ScheduledRunInfo,
    WorkflowReason,
    WorkflowResult,
    drift_schedule_id,
)

pytestmark = pytest.mark.unit


_NOW = datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC)
_LATER = _NOW + timedelta(seconds=5)


# ---------------------------------------------------------------------------
# WorkflowResult
# ---------------------------------------------------------------------------


def test_workflow_result_success_requires_succeeded_reason() -> None:
    wr = WorkflowResult(
        workflow_id="deploy-intent-abc",
        workflow_type="DeployIntent",
        target_id=uuid4(),
        success=True,
        reason=WorkflowReason.SUCCEEDED,
        started_at=_NOW,
        ended_at=_LATER,
    )
    assert wr.success is True
    assert wr.reason == WorkflowReason.SUCCEEDED


def test_workflow_result_success_with_non_succeeded_reason_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkflowResult(
            workflow_id="deploy-intent-abc",
            workflow_type="DeployIntent",
            target_id=uuid4(),
            success=True,
            reason=WorkflowReason.APPLY_FAILED,
            started_at=_NOW,
            ended_at=_LATER,
        )


def test_workflow_result_failure_with_succeeded_reason_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkflowResult(
            workflow_id="deploy-intent-abc",
            workflow_type="DeployIntent",
            target_id=uuid4(),
            success=False,
            reason=WorkflowReason.SUCCEEDED,
            started_at=_NOW,
            ended_at=_LATER,
        )


def test_workflow_result_drift_items_only_for_verification_failed() -> None:
    drift_item = DriftItem(
        path="/interface[name=ethernet-1/1]/admin-state",
        desired="enable",
        actual="disable",
        entity_kind="Interface",
    )
    # Allowed: VERIFICATION_FAILED with drift items.
    wr = WorkflowResult(
        workflow_id="deploy-intent-abc",
        workflow_type="DeployIntent",
        target_id=uuid4(),
        success=False,
        reason=WorkflowReason.VERIFICATION_FAILED,
        detail="post-apply drift",
        started_at=_NOW,
        ended_at=_LATER,
        drift_items=[drift_item],
    )
    assert wr.drift_items == [drift_item]
    # Rejected: drift items with a non-VERIFICATION_FAILED reason.
    with pytest.raises(ValidationError):
        WorkflowResult(
            workflow_id="deploy-intent-abc",
            workflow_type="DeployIntent",
            target_id=uuid4(),
            success=False,
            reason=WorkflowReason.APPLY_FAILED,
            detail="boom",
            started_at=_NOW,
            ended_at=_LATER,
            drift_items=[drift_item],
        )


def test_workflow_result_ended_before_started_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkflowResult(
            workflow_id="deploy-intent-abc",
            workflow_type="DeployIntent",
            target_id=uuid4(),
            success=True,
            reason=WorkflowReason.SUCCEEDED,
            started_at=_LATER,
            ended_at=_NOW,
        )


def test_workflow_result_is_frozen_and_extra_forbidden() -> None:
    wr = WorkflowResult(
        workflow_id="deploy-intent-abc",
        workflow_type="DeployIntent",
        target_id=uuid4(),
        success=True,
        reason=WorkflowReason.SUCCEEDED,
        started_at=_NOW,
        ended_at=_LATER,
    )
    with pytest.raises(ValidationError):
        wr.workflow_id = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        WorkflowResult(
            workflow_id="x",
            workflow_type="DeployIntent",
            target_id=uuid4(),
            success=True,
            reason=WorkflowReason.SUCCEEDED,
            started_at=_NOW,
            ended_at=_LATER,
            unknown="oops",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# DriftScanResult
# ---------------------------------------------------------------------------


def _make_report(device_id: UUID, status: DriftStatus = DriftStatus.CLEAN) -> DriftReport:
    if status is DriftStatus.CLEAN:
        return DriftReport(
            device_id=device_id,
            device_name=f"dev-{device_id}",
            status=status,
            items=[],
            timestamp=_NOW,
        )
    if status is DriftStatus.DRIFTED:
        return DriftReport(
            device_id=device_id,
            device_name=f"dev-{device_id}",
            status=status,
            items=[
                DriftItem(
                    path="/x",
                    desired="a",
                    actual="b",
                    entity_kind="Test",
                )
            ],
            timestamp=_NOW,
        )
    return DriftReport(
        device_id=device_id,
        device_name=f"dev-{device_id}",
        status=DriftStatus.ERROR,
        items=[],
        error="kaboom",
        timestamp=_NOW,
    )


def test_drift_scan_result_counts_sum_to_total() -> None:
    ids = [uuid4() for _ in range(3)]
    reports = {
        ids[0]: _make_report(ids[0], DriftStatus.CLEAN),
        ids[1]: _make_report(ids[1], DriftStatus.DRIFTED),
        ids[2]: _make_report(ids[2], DriftStatus.ERROR),
    }
    scan = DriftScanResult(
        workflow_id="scan-drift-dcfabric-xyz",
        use_case_id="dcfabric",
        reports=reports,
        total=3,
        clean=1,
        drifted=1,
        errored=1,
        started_at=_NOW,
        ended_at=_LATER,
    )
    assert scan.total == 3


def test_drift_scan_result_mismatched_counts_rejected() -> None:
    ids = [uuid4() for _ in range(2)]
    reports = {ids[0]: _make_report(ids[0]), ids[1]: _make_report(ids[1])}
    with pytest.raises(ValidationError):
        DriftScanResult(
            workflow_id="scan",
            use_case_id="dcfabric",
            reports=reports,
            total=2,
            clean=1,
            drifted=0,
            errored=0,  # sums to 1, not 2 → reject
            started_at=_NOW,
            ended_at=_LATER,
        )


def test_drift_scan_result_reports_len_must_match_total() -> None:
    ids = [uuid4()]
    reports = {ids[0]: _make_report(ids[0])}
    with pytest.raises(ValidationError):
        DriftScanResult(
            workflow_id="scan",
            use_case_id="dcfabric",
            reports=reports,
            total=2,
            clean=2,
            drifted=0,
            errored=0,
            started_at=_NOW,
            ended_at=_LATER,
        )


# ---------------------------------------------------------------------------
# ReconcileResult
# ---------------------------------------------------------------------------


def _make_workflow_result(device_id: UUID, success: bool = True) -> WorkflowResult:
    return WorkflowResult(
        workflow_id=f"deploy-intent-{device_id}",
        workflow_type="DeployIntent",
        target_id=device_id,
        success=success,
        reason=WorkflowReason.SUCCEEDED if success else WorkflowReason.APPLY_FAILED,
        detail=None if success else "boom",
        started_at=_NOW,
        ended_at=_LATER,
    )


def test_reconcile_result_counts_sum_to_total() -> None:
    ids = [uuid4() for _ in range(3)]
    device_results = {
        ids[0]: _make_workflow_result(ids[0], success=True),
        ids[1]: _make_workflow_result(ids[1], success=False),
    }
    result = ReconcileResult(
        workflow_id="reconcile-devices-xyz",
        device_results=device_results,
        total=3,
        succeeded=1,
        failed=1,
        skipped=1,
        started_at=_NOW,
        ended_at=_LATER,
    )
    assert result.total == 3


def test_reconcile_result_results_plus_skipped_must_equal_total() -> None:
    # First invariant satisfied (succeeded+failed+skipped = 1+0+1 = 2 = total),
    # but device_results is empty (0) + skipped (1) = 1 ≠ total (2) → reject.
    with pytest.raises(ValidationError):
        ReconcileResult(
            workflow_id="reconcile",
            device_results={},
            total=2,
            succeeded=1,
            failed=0,
            skipped=1,
            started_at=_NOW,
            ended_at=_LATER,
        )


# ---------------------------------------------------------------------------
# AuditEvent
# ---------------------------------------------------------------------------


def test_audit_event_activity_started_requires_activity_name() -> None:
    with pytest.raises(ValidationError):
        AuditEvent(
            event_id=uuid4(),
            workflow_id="wf",
            workflow_type="DeployIntent",
            target_id=uuid4(),
            event_type=AuditEventType.ACTIVITY_STARTED,
            timestamp=_NOW,
        )


def test_audit_event_activity_completed_requires_activity_name() -> None:
    with pytest.raises(ValidationError):
        AuditEvent(
            event_id=uuid4(),
            workflow_id="wf",
            workflow_type="DeployIntent",
            target_id=uuid4(),
            event_type=AuditEventType.ACTIVITY_COMPLETED,
            outcome="success",
            timestamp=_NOW,
        )


def test_audit_event_workflow_terminated_requires_reason() -> None:
    with pytest.raises(ValidationError):
        AuditEvent(
            event_id=uuid4(),
            workflow_id="wf",
            workflow_type="DeployIntent",
            target_id=uuid4(),
            event_type=AuditEventType.WORKFLOW_TERMINATED,
            timestamp=_NOW,
        )


def test_audit_event_outcome_must_be_known_value_or_none() -> None:
    with pytest.raises(ValidationError):
        AuditEvent(
            event_id=uuid4(),
            workflow_id="wf",
            workflow_type="DeployIntent",
            target_id=uuid4(),
            event_type=AuditEventType.WORKFLOW_STARTED,
            outcome="not-a-valid-outcome",
            timestamp=_NOW,
        )


def test_audit_event_workflow_started_minimal() -> None:
    e = AuditEvent(
        event_id=uuid4(),
        workflow_id="wf",
        workflow_type="DeployIntent",
        target_id=uuid4(),
        event_type=AuditEventType.WORKFLOW_STARTED,
        timestamp=_NOW,
    )
    assert e.event_type == AuditEventType.WORKFLOW_STARTED
    assert e.activity_name is None
    assert e.payload == {}


def test_audit_event_workflow_terminated_with_reason() -> None:
    e = AuditEvent(
        event_id=uuid4(),
        workflow_id="wf",
        workflow_type="DeployIntent",
        target_id=uuid4(),
        event_type=AuditEventType.WORKFLOW_TERMINATED,
        reason=WorkflowReason.SUCCEEDED,
        outcome="success",
        timestamp=_NOW,
    )
    assert e.reason == WorkflowReason.SUCCEEDED


def test_audit_event_is_frozen_and_extra_forbidden() -> None:
    e = AuditEvent(
        event_id=uuid4(),
        workflow_id="wf",
        workflow_type="DeployIntent",
        target_id=uuid4(),
        event_type=AuditEventType.WORKFLOW_STARTED,
        timestamp=_NOW,
    )
    with pytest.raises(ValidationError):
        e.workflow_id = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        AuditEvent(
            event_id=uuid4(),
            workflow_id="wf",
            workflow_type="DeployIntent",
            target_id=uuid4(),
            event_type=AuditEventType.WORKFLOW_STARTED,
            timestamp=_NOW,
            unknown="x",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# Drift schedules (spec 009)
# ---------------------------------------------------------------------------


def test_drift_response_enums_have_the_documented_values() -> None:
    assert [m.value for m in DriftResponseMode] == ["report", "remediate"]
    assert [m.value for m in ExclusionReason] == ["undesired_only", "errored"]
    assert [m.value for m in ScheduleChange] == [
        "created",
        "interval_changed",
        "response_changed",
        "paused",
        "resumed",
        "triggered",
        "deleted",
    ]
    assert AuditEventType.SCHEDULE_CHANGED.value == "schedule_changed"
    assert AuditEventType.DRIFT_RESPONSE.value == "drift_response"


def _schedule_event(**overrides) -> AuditEvent:
    fields = {
        "event_id": uuid4(),
        "workflow_id": "drift-response-dcfabric",
        "workflow_type": "DriftSchedule",
        "target_id": "dcfabric",
        "event_type": AuditEventType.SCHEDULE_CHANGED,
        "payload": {"change": "paused", "reason": "maintenance"},
        "timestamp": _NOW,
        "actor": "anton",
    }
    fields.update(overrides)
    return AuditEvent(**fields)


def test_schedule_changed_event_with_actor_and_known_change_is_valid() -> None:
    assert _schedule_event().payload["change"] == "paused"


def test_schedule_changed_event_requires_actor() -> None:
    with pytest.raises(ValidationError, match="actor"):
        _schedule_event(actor=None)


@pytest.mark.parametrize("payload", [{}, {"change": "exploded"}])
def test_schedule_changed_event_requires_a_known_change(payload: dict) -> None:
    with pytest.raises(ValidationError, match="change"):
        _schedule_event(payload=payload)


def test_drift_schedule_id_is_derived_from_the_use_case() -> None:
    assert drift_schedule_id("dcfabric") == "drift-response-dcfabric"


def test_drift_response_request_defaults_to_report() -> None:
    request = DriftResponseRequest(use_case_id="dcfabric", schedule_id="drift-response-dcfabric")
    assert request.mode is DriftResponseMode.REPORT


def test_drift_response_request_rejects_a_mismatched_schedule_id() -> None:
    with pytest.raises(ValidationError, match="schedule_id"):
        DriftResponseRequest(use_case_id="dcfabric", schedule_id="drift-response-site-edge")


def test_drift_response_request_rejects_an_empty_use_case() -> None:
    with pytest.raises(ValidationError):
        DriftResponseRequest(use_case_id="", schedule_id="drift-response-")


def _undesired_report(device_id: UUID) -> DriftReport:
    return DriftReport(
        device_id=device_id,
        device_name=f"dev-{device_id}",
        status=DriftStatus.DRIFTED,
        items=[DriftItem(path="/y", desired=None, actual="z", entity_kind="Test", undesired=True)],
        timestamp=_NOW,
    )


def _scan(reports: dict[UUID, DriftReport]) -> DriftScanResult:
    statuses = [r.status for r in reports.values()]
    return DriftScanResult(
        workflow_id="drift-response-dcfabric-x-scan",
        use_case_id="dcfabric",
        reports=reports,
        total=len(reports),
        clean=statuses.count(DriftStatus.CLEAN),
        drifted=statuses.count(DriftStatus.DRIFTED),
        errored=statuses.count(DriftStatus.ERROR),
        started_at=_NOW,
        ended_at=_LATER,
    )


def _reconcile(results: dict[UUID, WorkflowResult], skipped: int = 0) -> ReconcileResult:
    succeeded = sum(1 for r in results.values() if r.success)
    return ReconcileResult(
        workflow_id="drift-response-dcfabric-x-reconcile",
        device_results=results,
        total=len(results) + skipped,
        succeeded=succeeded,
        failed=len(results) - succeeded,
        skipped=skipped,
        started_at=_NOW,
        ended_at=_LATER,
    )


def _response(**overrides) -> DriftResponseResult:
    fields = {
        "workflow_id": "drift-response-dcfabric-x",
        "schedule_id": "drift-response-dcfabric",
        "use_case_id": "dcfabric",
        "mode": DriftResponseMode.REPORT,
        "scan": _scan({}),
        "started_at": _NOW,
        "ended_at": _LATER,
    }
    fields.update(overrides)
    return DriftResponseResult(**fields)


def test_report_result_with_a_drifted_scan_is_valid() -> None:
    drifted = uuid4()
    result = _response(scan=_scan({drifted: _make_report(drifted, DriftStatus.DRIFTED)}))
    assert result.remediated == []
    assert result.excluded == {}
    assert result.reconcile is None


def test_report_result_must_not_remediate() -> None:
    drifted = uuid4()
    with pytest.raises(ValidationError, match="REPORT"):
        _response(
            scan=_scan({drifted: _make_report(drifted, DriftStatus.DRIFTED)}),
            remediated=[drifted],
            reconcile=_reconcile({drifted: _make_workflow_result(drifted)}),
        )


def test_report_result_must_not_exclude() -> None:
    errored = uuid4()
    with pytest.raises(ValidationError, match="REPORT"):
        _response(
            scan=_scan({errored: _make_report(errored, DriftStatus.ERROR)}),
            excluded={errored: ExclusionReason.ERRORED},
        )


def test_remediate_result_with_eligible_and_excluded_devices_is_valid() -> None:
    forward, undesired, errored = uuid4(), uuid4(), uuid4()
    result = _response(
        mode=DriftResponseMode.REMEDIATE,
        scan=_scan(
            {
                forward: _make_report(forward, DriftStatus.DRIFTED),
                undesired: _undesired_report(undesired),
                errored: _make_report(errored, DriftStatus.ERROR),
            }
        ),
        remediated=[forward],
        excluded={undesired: ExclusionReason.UNDESIRED_ONLY, errored: ExclusionReason.ERRORED},
        reconcile=_reconcile({forward: _make_workflow_result(forward)}),
    )
    assert result.remediated == [forward]


def test_remediate_result_reconcile_presence_must_match_remediated() -> None:
    forward = uuid4()
    scan = _scan({forward: _make_report(forward, DriftStatus.DRIFTED)})
    with pytest.raises(ValidationError, match="reconcile"):
        _response(mode=DriftResponseMode.REMEDIATE, scan=scan, remediated=[forward])
    with pytest.raises(ValidationError, match="reconcile"):
        _response(
            mode=DriftResponseMode.REMEDIATE,
            scan=scan,
            reconcile=_reconcile({forward: _make_workflow_result(forward)}),
        )


def test_remediate_result_reconcile_total_must_match_remediated() -> None:
    a, b = uuid4(), uuid4()
    with pytest.raises(ValidationError, match="total"):
        _response(
            mode=DriftResponseMode.REMEDIATE,
            scan=_scan({a: _make_report(a, DriftStatus.DRIFTED), b: _make_report(b, DriftStatus.DRIFTED)}),
            remediated=[a, b],
            reconcile=_reconcile({a: _make_workflow_result(a)}),
        )


def test_remediate_result_rejects_a_device_both_remediated_and_excluded() -> None:
    forward = uuid4()
    with pytest.raises(ValidationError, match="disjoint"):
        _response(
            mode=DriftResponseMode.REMEDIATE,
            scan=_scan({forward: _make_report(forward, DriftStatus.DRIFTED)}),
            remediated=[forward],
            excluded={forward: ExclusionReason.ERRORED},
            reconcile=_reconcile({forward: _make_workflow_result(forward)}),
        )


def test_remediate_result_rejects_a_device_the_scan_never_saw() -> None:
    stranger = uuid4()
    with pytest.raises(ValidationError, match="scan"):
        _response(
            mode=DriftResponseMode.REMEDIATE,
            excluded={stranger: ExclusionReason.ERRORED},
        )


@pytest.mark.parametrize("ineligible", ["clean", "undesired_only", "errored"])
def test_remediate_result_rejects_remediating_an_ineligible_device(ineligible: str) -> None:
    device = uuid4()
    report = {
        "clean": _make_report(device, DriftStatus.CLEAN),
        "undesired_only": _undesired_report(device),
        "errored": _make_report(device, DriftStatus.ERROR),
    }[ineligible]
    with pytest.raises(ValidationError, match="forward drift"):
        _response(
            mode=DriftResponseMode.REMEDIATE,
            scan=_scan({device: report}),
            remediated=[device],
            reconcile=_reconcile({device: _make_workflow_result(device)}),
        )


def test_drift_response_result_ended_before_started_rejected() -> None:
    with pytest.raises(ValidationError, match="ended_at"):
        _response(ended_at=_NOW - timedelta(seconds=1))


def test_run_summary_of_a_report_run_did_not_write() -> None:
    drifted = uuid4()
    summary = RunSummary.from_result(_response(scan=_scan({drifted: _make_report(drifted, DriftStatus.DRIFTED)})))
    assert (summary.total, summary.clean, summary.drifted, summary.errored) == (1, 0, 1, 0)
    assert (summary.remediated, summary.succeeded, summary.failed) == (0, 0, 0)
    assert summary.wrote is False


@pytest.mark.parametrize("success", [True, False])
def test_run_summary_counts_any_attempted_apply_as_a_write(success: bool) -> None:
    forward = uuid4()
    result = _response(
        mode=DriftResponseMode.REMEDIATE,
        scan=_scan({forward: _make_report(forward, DriftStatus.DRIFTED)}),
        remediated=[forward],
        reconcile=_reconcile({forward: _make_workflow_result(forward, success=success)}),
    )
    summary = RunSummary.from_result(result)
    assert summary.wrote is True
    assert (summary.succeeded, summary.failed) == ((1, 0) if success else (0, 1))


def test_run_summary_of_an_all_skipped_reconcile_did_not_write() -> None:
    forward = uuid4()
    result = _response(
        mode=DriftResponseMode.REMEDIATE,
        scan=_scan({forward: _make_report(forward, DriftStatus.DRIFTED)}),
        remediated=[forward],
        reconcile=_reconcile({}, skipped=1),
    )
    assert RunSummary.from_result(result).wrote is False


def test_run_summary_rejects_an_inconsistent_wrote_flag() -> None:
    with pytest.raises(ValidationError, match="wrote"):
        RunSummary(total=1, clean=1, drifted=0, errored=0, remediated=0, succeeded=0, failed=0, wrote=True)


def test_drift_schedule_view_dumps_interval_as_seconds() -> None:
    view = DriftScheduleView(
        schedule_id="drift-response-dcfabric",
        use_case_id="dcfabric",
        interval=timedelta(minutes=5),
        mode=DriftResponseMode.REPORT,
        paused=False,
        note=None,
        next_runs=[_LATER],
        recent_runs=[
            ScheduledRunInfo(
                workflow_id="drift-response-dcfabric-2026-05-21T12:00:00Z",
                scheduled_at=_NOW,
                started_at=_NOW,
                status="completed",
                summary=None,
            )
        ],
        running=[],
        skipped_overlap=0,
    )
    dumped = view.model_dump(mode="json")
    assert dumped["interval"] == 300
    assert dumped["recent_runs"][0]["status"] == "completed"
    with pytest.raises(ValidationError):
        view.paused = True  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ScheduledRunInfo(
            workflow_id="x",
            scheduled_at=_NOW,
            started_at=_NOW,
            status="completed",
            unknown=1,  # type: ignore[call-arg]
        )
