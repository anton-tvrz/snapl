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
    assert [m.value for m in ExclusionReason] == ["undesired_only", "errored", "blast_radius"]
    assert [m.value for m in ScheduleChange] == [
        "created",
        "interval_changed",
        "response_changed",
        "paused",
        "resumed",
        "triggered",
        "deleted",
        "stood_down",
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


def test_a_guarded_remediate_result_that_stood_down_is_valid() -> None:
    """The blast-radius guard held: nothing repaired, the schedule stood down (spec 018, #136)."""
    a, b = uuid4(), uuid4()
    result = _response(
        mode=DriftResponseMode.REMEDIATE,
        scan=_scan({a: _make_report(a, DriftStatus.DRIFTED), b: _make_report(b, DriftStatus.DRIFTED)}),
        excluded={a: ExclusionReason.BLAST_RADIUS, b: ExclusionReason.BLAST_RADIUS},
        stood_down=True,
    )
    assert result.remediated == []
    assert result.reconcile is None


def test_a_guarded_remediate_result_must_repair_nothing() -> None:
    a, b = uuid4(), uuid4()
    with pytest.raises(ValidationError, match="guard"):
        _response(
            mode=DriftResponseMode.REMEDIATE,
            scan=_scan({a: _make_report(a, DriftStatus.DRIFTED), b: _make_report(b, DriftStatus.DRIFTED)}),
            remediated=[a],
            excluded={b: ExclusionReason.BLAST_RADIUS},
            reconcile=_reconcile({a: _make_workflow_result(a)}),
        )


def test_a_result_stands_down_only_after_a_failed_repair_or_a_held_guard() -> None:
    forward = uuid4()
    with pytest.raises(ValidationError, match="stands its schedule down"):
        _response(
            mode=DriftResponseMode.REMEDIATE,
            scan=_scan({forward: _make_report(forward, DriftStatus.DRIFTED)}),
            remediated=[forward],
            reconcile=_reconcile({forward: _make_workflow_result(forward)}),
            stood_down=True,
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


# ---------------------------------------------------------------------------
# Spec 016 T015 — the health_changed audit event (research R7)
# ---------------------------------------------------------------------------


def _health_event(**overrides) -> AuditEvent:
    fields = {
        "event_id": uuid4(),
        "workflow_id": "telemetry:spine-01",
        "workflow_type": "DeviceTelemetry",
        "target_id": uuid4(),
        "event_type": AuditEventType.HEALTH_CHANGED,
        "payload": {
            "before": "unknown",
            "after": "unhealthy",
            "reasons": [
                {
                    "kind": "bgp_session",
                    "item": "10.10.1.1",
                    "expected": "established",
                    "actual": "active",
                    "text": "BGP 10.10.1.1 active (expected established)",
                }
            ],
            "last_sample_at": "2026-11-19T22:00:00+00:00",
        },
        "timestamp": _NOW,
        "actor": "worker",
    }
    fields.update(overrides)
    return AuditEvent(**fields)


def test_health_changed_value() -> None:
    assert AuditEventType.HEALTH_CHANGED.value == "health_changed"


def test_health_changed_event_with_actor_and_known_states_is_valid() -> None:
    event = _health_event()
    assert (event.payload["before"], event.payload["after"]) == ("unknown", "unhealthy")


def test_health_changed_event_requires_actor() -> None:
    with pytest.raises(ValidationError, match="health_changed requires actor"):
        _health_event(actor=None)


@pytest.mark.parametrize("key", ["before", "after"])
@pytest.mark.parametrize("value", [None, "sick", ""])
def test_health_changed_event_requires_known_states(key: str, value: str | None) -> None:
    payload = {"before": "healthy", "after": "unhealthy"}
    if value is None:
        del payload[key]
    else:
        payload[key] = value
    with pytest.raises(ValidationError, match=f"payload\\['{key}'\\]"):
        _health_event(payload=payload)


def test_health_changed_event_leaves_other_payload_keys_free() -> None:
    event = _health_event(payload={"before": "healthy", "after": "unknown", "anything": [1, 2]})
    assert event.payload["anything"] == [1, 2]


# ---------------------------------------------------------------------------
# Removal (spec 013, T005)
# ---------------------------------------------------------------------------

_REMOVAL_DEVICE = UUID("00000000-0000-0000-0000-000000000004")
_PORT = "/interface[name=ethernet-1/7]"
_NEIGHBOR = "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=192.0.2.0]"


def _removal_plan(*delete_paths: str):
    from snapl_observability.models import PlannedDelete, RemovalPlan

    return RemovalPlan(
        device_id=_REMOVAL_DEVICE,
        device_name="leaf-04",
        scanned_at=_NOW,
        deletes=[
            PlannedDelete(path=path, entity_kind="interface", answers=[f"{path}/description"])
            for path in sorted(delete_paths)
        ],
        refused=[],
        forward_drift=[],
    )


def _verification(*, gone=(_PORT,), remaining=(), new_forward=(), still_refused=()):
    from snapl_orchestrator.models import RemovalVerification

    return RemovalVerification(
        gone=list(gone),
        remaining=list(remaining),
        new_forward=list(new_forward),
        still_refused=list(still_refused),
    )


def _removal_result(**overrides):
    from snapl_orchestrator.models import RemovalResult

    fields = {
        "workflow_id": f"remove-config-{_REMOVAL_DEVICE}",
        "device_id": _REMOVAL_DEVICE,
        "device_name": "leaf-04",
        "success": True,
        "reason": WorkflowReason.SUCCEEDED,
        "plan": _removal_plan(_PORT),
        "deleted": [_PORT],
        "device_response": "ok",
        "verification": _verification(),
        "started_at": _NOW,
        "ended_at": _LATER,
        **overrides,
    }
    return RemovalResult(**fields)


class TestRemovalReasons:
    def test_the_new_reasons(self) -> None:
        assert WorkflowReason.PLAN_CHANGED.value == "plan_changed"
        assert WorkflowReason.DEPLOY_IN_FLIGHT.value == "deploy_in_flight"
        assert WorkflowReason.REMOVE_FAILED.value == "remove_failed"


class TestRemovalRequest:
    def _request(self, **overrides):
        from snapl_orchestrator.models import RemovalRequest

        fields = {"device_id": _REMOVAL_DEVICE, "actor": "anton", "confirmed_paths": [_PORT, _NEIGHBOR], **overrides}
        return RemovalRequest(**fields)

    def test_holds_the_confirmation(self) -> None:
        r = self._request()
        assert r.actor == "anton"
        assert r.confirmed_paths == [_PORT, _NEIGHBOR]

    @pytest.mark.parametrize("actor", ["", "   "])
    def test_rejects_an_empty_actor(self, actor) -> None:
        with pytest.raises(ValidationError, match="actor"):
            self._request(actor=actor)

    def test_rejects_no_paths(self) -> None:
        with pytest.raises(ValidationError, match="confirmed_paths"):
            self._request(confirmed_paths=[])

    def test_rejects_a_duplicate_path(self) -> None:
        with pytest.raises(ValidationError, match="duplicate"):
            self._request(confirmed_paths=[_PORT, _PORT])

    def test_is_frozen(self) -> None:
        r = self._request()
        with pytest.raises(ValidationError):
            r.actor = "someone"  # type: ignore[misc]


class TestRemovalVerification:
    def test_passes_when_nothing_remains_and_nothing_new_is_missing(self) -> None:
        assert _verification(still_refused=["/routing-policy/policy[name=p]/default_action"]).passed

    def test_fails_with_something_remaining(self) -> None:
        assert not _verification(gone=(), remaining=(_PORT,)).passed

    def test_fails_with_new_forward_drift(self) -> None:
        assert not _verification(new_forward=["/interface[name=ethernet-1/49]/enabled"]).passed

    def test_planned_is_gone_and_remaining(self) -> None:
        v = _verification(gone=(_PORT,), remaining=(_NEIGHBOR,))
        assert v.planned == sorted([_PORT, _NEIGHBOR])

    def test_a_path_cannot_be_both_gone_and_remaining(self) -> None:
        with pytest.raises(ValidationError, match="both"):
            _verification(gone=(_PORT,), remaining=(_PORT,))


class TestRemovalResult:
    def test_a_verified_removal(self) -> None:
        r = _removal_result()
        assert r.success
        assert r.verification is not None
        assert r.verification.passed

    def test_success_requires_the_succeeded_reason(self) -> None:
        with pytest.raises(ValidationError, match="SUCCEEDED"):
            _removal_result(reason=WorkflowReason.VERIFICATION_FAILED)

    def test_failure_rejects_the_succeeded_reason(self) -> None:
        with pytest.raises(ValidationError, match="SUCCEEDED"):
            _removal_result(success=False)

    def test_success_requires_a_passed_verification(self) -> None:
        with pytest.raises(ValidationError, match="verification"):
            _removal_result(verification=None)
        with pytest.raises(ValidationError, match="verification"):
            _removal_result(verification=_verification(gone=(), remaining=(_PORT,)))

    def test_a_failed_verification(self) -> None:
        r = _removal_result(
            success=False,
            reason=WorkflowReason.VERIFICATION_FAILED,
            verification=_verification(gone=(), remaining=(_PORT,)),
        )
        assert not r.success

    def test_a_failed_verification_needs_one_that_failed(self) -> None:
        with pytest.raises(ValidationError, match="verification"):
            _removal_result(success=False, reason=WorkflowReason.VERIFICATION_FAILED)

    def test_a_changed_plan_writes_nothing_and_carries_the_new_plan(self) -> None:
        r = _removal_result(
            success=False,
            reason=WorkflowReason.PLAN_CHANGED,
            plan=_removal_plan(_PORT, _NEIGHBOR),
            deleted=[],
            device_response=None,
            verification=None,
        )
        assert r.plan is not None
        assert r.plan.delete_paths == [_PORT, _NEIGHBOR]
        with pytest.raises(ValidationError, match="plan"):
            _removal_result(
                success=False,
                reason=WorkflowReason.PLAN_CHANGED,
                plan=None,
                deleted=[],
                device_response=None,
                verification=None,
            )

    @pytest.mark.parametrize(
        "reason",
        [
            WorkflowReason.PLAN_CHANGED,
            WorkflowReason.DEPLOY_IN_FLIGHT,
            WorkflowReason.REMOVE_FAILED,
            WorkflowReason.DEVICE_NOT_FOUND,
            WorkflowReason.INTENT_UNAVAILABLE,
        ],
    )
    def test_reasons_that_write_nothing_have_nothing_deleted(self, reason) -> None:
        with pytest.raises(ValidationError, match="deleted"):
            _removal_result(success=False, reason=reason, verification=None)

    def test_a_verification_needs_a_write(self) -> None:
        with pytest.raises(ValidationError, match="verification"):
            _removal_result(success=False, reason=WorkflowReason.REMOVE_FAILED, deleted=[])

    def test_a_collect_failure_after_the_write_keeps_what_was_deleted(self) -> None:
        r = _removal_result(success=False, reason=WorkflowReason.COLLECT_FAILED, verification=None)
        assert r.deleted == [_PORT]
        assert r.verification is None

    def test_a_collect_failure_before_the_write(self) -> None:
        r = _removal_result(
            success=False,
            reason=WorkflowReason.COLLECT_FAILED,
            plan=None,
            deleted=[],
            device_response=None,
            verification=None,
        )
        assert r.deleted == []

    def test_intent_unread_leaves_no_device_name(self) -> None:
        r = _removal_result(
            success=False,
            reason=WorkflowReason.INTENT_UNAVAILABLE,
            device_name=None,
            plan=None,
            deleted=[],
            device_response=None,
            verification=None,
        )
        assert r.device_name is None

    def test_ended_before_started_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="ended_at"):
            _removal_result(ended_at=_NOW - timedelta(seconds=1))

    def test_round_trips_through_json(self) -> None:
        from snapl_orchestrator.models import RemovalResult

        r = _removal_result()
        assert RemovalResult.model_validate_json(r.model_dump_json()) == r


class TestRemoveConfigAuditStart:
    def _start(self, **overrides) -> AuditEvent:
        fields = {
            "event_id": uuid4(),
            "workflow_id": f"remove-config-{_REMOVAL_DEVICE}",
            "workflow_type": "RemoveConfig",
            "target_id": _REMOVAL_DEVICE,
            "event_type": AuditEventType.WORKFLOW_STARTED,
            "payload": {"confirmed_paths": [_PORT]},
            "timestamp": _NOW,
            "actor": "anton",
            **overrides,
        }
        return AuditEvent(**fields)

    def test_a_removal_start_names_who_and_what(self) -> None:
        e = self._start()
        assert e.actor == "anton"

    def test_a_removal_start_requires_an_actor(self) -> None:
        with pytest.raises(ValidationError, match="actor"):
            self._start(actor=None)

    def test_a_removal_start_requires_the_confirmed_paths(self) -> None:
        with pytest.raises(ValidationError, match="confirmed_paths"):
            self._start(payload={})

    def test_other_events_of_a_removal_are_unaffected(self) -> None:
        e = self._start(
            event_type=AuditEventType.WORKFLOW_TERMINATED,
            reason=WorkflowReason.SUCCEEDED,
            actor=None,
            payload={},
        )
        assert e.actor is None

    @pytest.mark.parametrize("workflow_type", ["DeployIntent", "PlanRemoval", "ScanDrift"])
    def test_other_workflow_starts_are_unaffected(self, workflow_type) -> None:
        e = self._start(workflow_type=workflow_type, actor=None, payload={})
        assert e.actor is None


def test_workflow_result_carries_no_undesired_paths_by_default() -> None:
    wr = WorkflowResult(
        workflow_id="w",
        workflow_type="DeployIntent",
        target_id=uuid4(),
        success=True,
        reason=WorkflowReason.SUCCEEDED,
        started_at=_NOW,
        ended_at=_NOW,
    )
    assert wr.undesired_paths == []


def test_workflow_result_undesired_paths_only_on_success() -> None:
    """A failed verification already lists them in drift_items (spec 008 FR-022)."""
    with pytest.raises(ValidationError, match="undesired_paths populated only for SUCCEEDED"):
        WorkflowResult(
            workflow_id="w",
            workflow_type="DeployIntent",
            target_id=uuid4(),
            success=False,
            reason=WorkflowReason.APPLY_FAILED,
            started_at=_NOW,
            ended_at=_NOW,
            undesired_paths=["/interface[name=ethernet-1/7]/description"],
        )
