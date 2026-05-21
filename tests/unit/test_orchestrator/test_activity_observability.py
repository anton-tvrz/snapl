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
async def test_detect_drift_delegates_to_observer(dcfabric_desired_state) -> None:
    device_id = dcfabric_desired_state.device.id
    collected = CollectResult(
        device_id=device_id,
        device_name=dcfabric_desired_state.device.name,
        success=True,
        data={"/interface": []},
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
    observer.detect_drift.assert_awaited_once_with(dcfabric_desired_state, collected)
