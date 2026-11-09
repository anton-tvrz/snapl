"""The projections stay fast on a large audit trail (spec 010 T034, SC-007).

100,000 events is years of a six-device fabric scanned every five minutes. The
read model is rebuilt from them in memory, so each projection must finish in
under a second over all of them.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from snapl_intent.models import DesiredState, Device
from snapl_orchestrator.models import AuditEvent, AuditEventType, WorkflowReason
from snapl_presentation.dashboard.models import Bucket, SeriesName
from snapl_presentation.dashboard.projections import project_devices, project_runs, project_series

pytestmark = [pytest.mark.unit, pytest.mark.slow]

T0 = datetime(2026, 1, 1, tzinfo=UTC)
DEVICES = [uuid4() for _ in range(6)]
LIMIT_S = 1.0


def _event(workflow_id, workflow_type, event_type, at, **kwargs) -> AuditEvent:
    return AuditEvent(
        event_id=uuid4(),
        workflow_id=workflow_id,
        workflow_type=workflow_type,
        event_type=event_type,
        timestamp=at,
        **kwargs,
    )


def _trail(size: int) -> list[AuditEvent]:
    """Scans every five minutes (start, six device records, end), with a deploy
    every tenth scan, until the trail holds ``size`` events."""
    events: list[AuditEvent] = []
    scan = 0
    while len(events) < size:
        at = T0 + timedelta(minutes=5 * scan)
        workflow_id = f"scan-drift-dcfabric-{scan}"
        events.append(_event(workflow_id, "ScanDrift", AuditEventType.WORKFLOW_STARTED, at, target_id="dcfabric"))
        for index, device_id in enumerate(DEVICES):
            drifted = scan % 7 == index
            payload = {"status": "drifted" if drifted else "clean", "drift_items": int(drifted)}
            if drifted:
                payload |= {"paths": ["/a"], "undesired_paths": []}
            events.append(
                _event(
                    workflow_id,
                    "ScanDrift",
                    AuditEventType.ACTIVITY_COMPLETED,
                    at + timedelta(seconds=1),
                    target_id=device_id,
                    activity_name="detect_drift",
                    outcome="success",
                    payload=payload,
                )
            )
        events.append(
            _event(
                workflow_id,
                "ScanDrift",
                AuditEventType.WORKFLOW_TERMINATED,
                at + timedelta(seconds=2),
                target_id="dcfabric",
                outcome="success",
                reason=WorkflowReason.SUCCEEDED,
                payload={"total": 6, "clean": 5, "drifted": 1, "errored": 0},
            )
        )
        if scan % 10 == 0:
            device_id = DEVICES[scan % 6]
            deploy = f"deploy-intent-{device_id}"
            events.append(
                _event(
                    deploy,
                    "DeployIntent",
                    AuditEventType.WORKFLOW_STARTED,
                    at + timedelta(seconds=3),
                    target_id=device_id,
                    payload={"parent_workflow_id": None},
                )
            )
            events.append(
                _event(
                    deploy,
                    "DeployIntent",
                    AuditEventType.WORKFLOW_TERMINATED,
                    at + timedelta(seconds=6),
                    target_id=device_id,
                    outcome="success",
                    reason=WorkflowReason.SUCCEEDED,
                )
            )
        scan += 1
    return events[:size]


@pytest.fixture(scope="module")
def trail() -> list[AuditEvent]:
    return _trail(100_000)


def _timed(fn) -> float:
    started = time.perf_counter()
    fn()
    return time.perf_counter() - started


def test_device_projection_is_under_a_second(trail):
    states = [
        DesiredState(
            device=Device(id=d, name=f"dev-{i}", management_address="10.0.0.1", role="leaf", use_case="dcfabric")
        )
        for i, d in enumerate(DEVICES)
    ]

    assert _timed(lambda: project_devices(trail, states, {})) < LIMIT_S


def test_run_projection_is_under_a_second(trail):
    now = trail[-1].timestamp

    elapsed = _timed(
        lambda: project_runs(trail, set(), now, names={}, ui_address="http://localhost:18034", namespace="default")
    )

    assert elapsed < LIMIT_S


def test_a_30_day_hourly_series_is_under_a_second(trail):
    end = trail[-1].timestamp
    start = end - timedelta(days=30)

    for name in SeriesName:
        assert _timed(lambda name=name: project_series(trail, name, start, end, Bucket.HOUR)) < LIMIT_S, name
