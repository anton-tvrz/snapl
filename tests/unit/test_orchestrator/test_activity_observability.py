"""Unit tests for the detect_drift activity."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from snapl_collector.models import CollectResult
from snapl_observability.models import DriftReport, DriftStatus
from snapl_orchestrator.activities import Activities, set_activities
from snapl_orchestrator.activities.observability import detect_drift

pytestmark = pytest.mark.unit


def _install(observer) -> None:
    set_activities(
        Activities(
            intent_store=MagicMock(),
            executor=MagicMock(),
            collector=MagicMock(),
            observer=observer,
            audit_log=MagicMock(),
        )
    )


def teardown_function() -> None:
    import snapl_orchestrator.activities as a

    a._activities = None


@pytest.mark.asyncio
async def test_detect_drift_normalizes_collected_state_before_observer(dcfabric_desired_state) -> None:
    # Raw SR Linux collector output (keyed by requested gNMI path) must be
    # translated to the diff contract before the Observer sees it (#32).
    device_id = dcfabric_desired_state.device.id
    collected = CollectResult(
        device_id=device_id,
        device_name=dcfabric_desired_state.device.name,
        success=True,
        data={
            "/interface": {
                "srl_nokia-interfaces:interface": [
                    {"name": "ethernet-1/1", "admin-state": "enable", "mtu": 9232},
                ],
            },
        },
        paths=["/interface"],
    )
    report = DriftReport(
        device_id=device_id,
        device_name=dcfabric_desired_state.device.name,
        status=DriftStatus.CLEAN,
        items=[],
        timestamp=datetime.now(tz=UTC),
    )
    observer = MagicMock()
    observer.detect_drift = AsyncMock(return_value=report)
    _install(observer)

    out = await detect_drift(dcfabric_desired_state, collected)

    assert out is report
    passed = observer.detect_drift.await_args.args[1]
    # The Observer receives entity-keyed, flat-field data — not the raw shape.
    assert "/interface[name=ethernet-1/1]" in passed.data
    assert passed.data["/interface[name=ethernet-1/1]"]["enabled"] is True
    assert "/interface" not in passed.data


@pytest.mark.asyncio
async def test_detect_drift_passes_failed_collection_through_unchanged(dcfabric_desired_state) -> None:
    # A failed collect carries no data; it must reach the Observer untouched so
    # an ERROR report is still produced.
    device_id = dcfabric_desired_state.device.id
    collected = CollectResult(
        device_id=device_id,
        device_name=dcfabric_desired_state.device.name,
        success=False,
        error="timeout after 30s",
        paths=["/interface"],
    )
    report = DriftReport(
        device_id=device_id,
        device_name=dcfabric_desired_state.device.name,
        status=DriftStatus.ERROR,
        items=[],
        error="timeout after 30s",
        timestamp=datetime.now(tz=UTC),
    )
    observer = MagicMock()
    observer.detect_drift = AsyncMock(return_value=report)
    _install(observer)

    out = await detect_drift(dcfabric_desired_state, collected)

    assert out is report
    observer.detect_drift.assert_awaited_once_with(dcfabric_desired_state, collected)
