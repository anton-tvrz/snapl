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
    DriftScanResult,
    ReconcileResult,
    WorkflowReason,
    WorkflowResult,
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
