"""Shared fixtures for Presentation CLI tests (spec 006)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from snapl_observability.models import DriftItem, DriftReport, DriftStatus
from snapl_orchestrator.models import (
    AuditEvent,
    AuditEventType,
    DriftScanResult,
    ReconcileResult,
    WorkflowReason,
    WorkflowResult,
)

_START = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    return _START


def _state(name: str, device_id: UUID, use_case: str = "dcfabric") -> MagicMock:
    state = MagicMock()
    state.device.id = device_id
    state.device.name = name
    state.device.use_case = use_case
    return state


@pytest.fixture
def device_ids() -> dict[str, UUID]:
    return {"spine-01": uuid4(), "spine-02": uuid4(), "leaf-01": uuid4()}


@pytest.fixture
def states(device_ids: dict[str, UUID]) -> list[MagicMock]:
    return [_state(name, device_id) for name, device_id in device_ids.items()]


@pytest.fixture
def make_state():
    return _state


@pytest.fixture
def workflow_result(device_ids: dict[str, UUID]):
    def _build(*, success: bool = True, reason: WorkflowReason = WorkflowReason.SUCCEEDED) -> WorkflowResult:
        return WorkflowResult(
            workflow_id=f"deploy-intent-{device_ids['spine-01']}",
            workflow_type="DeployIntent",
            target_id=device_ids["spine-01"],
            success=success,
            reason=reason,
            detail=None if success else "apply rejected by device",
            started_at=_START,
            ended_at=_START + timedelta(seconds=4),
        )

    return _build


@pytest.fixture
def drift_item() -> DriftItem:
    return DriftItem(
        path="/interface[name=ethernet-1/1]/admin-state",
        desired="enable",
        actual="disable",
        entity_kind="Interface",
    )


@pytest.fixture
def scan_result(device_ids: dict[str, UUID], drift_item: DriftItem):
    def _build(*, drifted: int = 0, errored: int = 0) -> DriftScanResult:
        reports: dict[UUID, DriftReport] = {}
        names = list(device_ids)
        for index, name in enumerate(names):
            device_id = device_ids[name]
            if index < drifted:
                report = DriftReport(
                    device_id=device_id,
                    device_name=name,
                    status=DriftStatus.DRIFTED,
                    items=[drift_item],
                    timestamp=_START,
                )
            elif index < drifted + errored:
                report = DriftReport(
                    device_id=device_id,
                    device_name=name,
                    status=DriftStatus.ERROR,
                    items=[],
                    error="gNMI dial failed: connection refused",
                    timestamp=_START,
                )
            else:
                report = DriftReport(
                    device_id=device_id,
                    device_name=name,
                    status=DriftStatus.CLEAN,
                    items=[],
                    timestamp=_START,
                )
            reports[device_id] = report

        total = len(names)
        return DriftScanResult(
            workflow_id="scan-drift-dcfabric-1",
            use_case_id="dcfabric",
            reports=reports,
            total=total,
            clean=total - drifted - errored,
            drifted=drifted,
            errored=errored,
            started_at=_START,
            ended_at=_START + timedelta(seconds=9),
        )

    return _build


@pytest.fixture
def reconcile_result(device_ids: dict[str, UUID], workflow_result):
    def _build(*, succeeded: int = 1, failed: int = 0, skipped: int = 0) -> ReconcileResult:
        results = {}
        names = list(device_ids)
        for index in range(succeeded + failed):
            device_id = device_ids[names[index]]
            success = index < succeeded
            results[device_id] = WorkflowResult(
                workflow_id=f"deploy-intent-{device_id}",
                workflow_type="DeployIntent",
                target_id=device_id,
                success=success,
                reason=WorkflowReason.SUCCEEDED if success else WorkflowReason.APPLY_FAILED,
                started_at=_START,
                ended_at=_START + timedelta(seconds=3),
            )
        return ReconcileResult(
            workflow_id="reconcile-1",
            device_results=results,
            total=succeeded + failed + skipped,
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
            started_at=_START,
            ended_at=_START + timedelta(seconds=12),
        )

    return _build


@pytest.fixture
def audit_events(device_ids: dict[str, UUID]) -> list[AuditEvent]:
    return [
        AuditEvent(
            event_id=uuid4(),
            workflow_id=f"deploy-intent-{device_ids['spine-01']}",
            workflow_type="DeployIntent",
            target_id=device_ids["spine-01"],
            event_type=AuditEventType.WORKFLOW_STARTED,
            timestamp=_START,
        ),
        AuditEvent(
            event_id=uuid4(),
            workflow_id=f"deploy-intent-{device_ids['spine-01']}",
            workflow_type="DeployIntent",
            target_id=device_ids["spine-01"],
            event_type=AuditEventType.ACTIVITY_COMPLETED,
            activity_name="apply_config",
            outcome="success",
            timestamp=_START + timedelta(seconds=2),
        ),
    ]


@pytest.fixture
def temporal_client():
    """A Temporal client whose execute_workflow returns queued results."""

    def _build(*results):
        client = MagicMock()
        client.execute_workflow = AsyncMock(side_effect=list(results))
        return client

    return _build
