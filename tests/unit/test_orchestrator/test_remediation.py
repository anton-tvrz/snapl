"""Unit tests for snapl_orchestrator.remediation — which drifted devices a scheduled repair may touch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from snapl_observability.models import DriftItem, DriftReport, DriftStatus
from snapl_orchestrator.models import DriftScanResult, ExclusionReason, has_forward_drift
from snapl_orchestrator.remediation import select_remediation

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 28, 3, 0, 0, tzinfo=UTC)


def _item(*, undesired: bool) -> DriftItem:
    if undesired:
        return DriftItem(path="/u", desired=None, actual="hand-added", entity_kind="Test", undesired=True)
    return DriftItem(path="/f", desired=True, actual=False, entity_kind="Test")


def _report(device_id: UUID, status: DriftStatus, items: list[DriftItem] | None = None) -> DriftReport:
    return DriftReport(
        device_id=device_id,
        device_name=f"dev-{device_id}",
        status=status,
        items=items or [],
        error="unreachable" if status is DriftStatus.ERROR else None,
        timestamp=_NOW,
    )


def _scan(*reports: DriftReport) -> DriftScanResult:
    statuses = [r.status for r in reports]
    return DriftScanResult(
        workflow_id="scan",
        use_case_id="dcfabric",
        reports={r.device_id: r for r in reports},
        total=len(reports),
        clean=statuses.count(DriftStatus.CLEAN),
        drifted=statuses.count(DriftStatus.DRIFTED),
        errored=statuses.count(DriftStatus.ERROR),
        started_at=_NOW,
        ended_at=_NOW + timedelta(seconds=3),
    )


def test_clean_devices_are_neither_remediated_nor_excluded() -> None:
    assert select_remediation(_scan(_report(uuid4(), DriftStatus.CLEAN))) == ([], {})


def test_an_errored_device_is_excluded_as_errored() -> None:
    device = uuid4()
    assert select_remediation(_scan(_report(device, DriftStatus.ERROR))) == ([], {device: ExclusionReason.ERRORED})


def test_an_undesired_only_device_is_excluded() -> None:
    device = uuid4()
    scan = _scan(_report(device, DriftStatus.DRIFTED, [_item(undesired=True), _item(undesired=True)]))
    assert select_remediation(scan) == ([], {device: ExclusionReason.UNDESIRED_ONLY})


def test_a_device_with_forward_drift_is_remediated() -> None:
    device = uuid4()
    assert select_remediation(_scan(_report(device, DriftStatus.DRIFTED, [_item(undesired=False)]))) == ([device], {})


def test_a_device_with_both_kinds_of_drift_is_remediated() -> None:
    device = uuid4()
    scan = _scan(_report(device, DriftStatus.DRIFTED, [_item(undesired=True), _item(undesired=False)]))
    assert select_remediation(scan) == ([device], {})


def test_remediated_devices_keep_scan_order() -> None:
    ids = [uuid4() for _ in range(4)]
    scan = _scan(
        _report(ids[0], DriftStatus.DRIFTED, [_item(undesired=False)]),
        _report(ids[1], DriftStatus.CLEAN),
        _report(ids[2], DriftStatus.DRIFTED, [_item(undesired=False)]),
        _report(ids[3], DriftStatus.ERROR),
    )
    remediated, excluded = select_remediation(scan)
    assert remediated == [ids[0], ids[2]]
    assert excluded == {ids[3]: ExclusionReason.ERRORED}


def test_an_empty_scan_selects_nothing() -> None:
    assert select_remediation(_scan()) == ([], {})


def test_remediation_matches_the_rule_drift_response_result_enforces() -> None:
    """DriftResponseResult refuses a remediated device without forward drift.

    If select_remediation ever disagreed with it, the workflow would fail its
    own validator; one predicate keeps them from drifting apart.
    """
    reports = [
        _report(uuid4(), DriftStatus.CLEAN),
        _report(uuid4(), DriftStatus.ERROR),
        _report(uuid4(), DriftStatus.DRIFTED, [_item(undesired=True)]),
        _report(uuid4(), DriftStatus.DRIFTED, [_item(undesired=False)]),
        _report(uuid4(), DriftStatus.DRIFTED, [_item(undesired=True), _item(undesired=False)]),
    ]

    remediated, _ = select_remediation(_scan(*reports))

    assert remediated == [r.device_id for r in reports if has_forward_drift(r)]
