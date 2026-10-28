"""Unit tests for the collect_drift_state activity."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from snapl_collector.models import CollectResult
from snapl_orchestrator.activities import Activities, set_activities
from snapl_orchestrator.activities.collector import collect_drift_state
from snapl_orchestrator.adapters.srlinux import DRIFT_PATHS

pytestmark = pytest.mark.unit


def _install(collector) -> None:
    set_activities(
        Activities(
            intent_store=MagicMock(),
            executor=MagicMock(),
            collector=collector,
            observer=MagicMock(),
            audit_log=MagicMock(),
        )
    )


def teardown_function() -> None:
    import snapl_orchestrator.activities as a

    a._activities = None


@pytest.mark.asyncio
async def test_collect_drift_state_requests_the_adapter_drift_paths(make_device) -> None:
    """The activity, not the workflow, owns which vendor paths drift detection needs (#119)."""
    device = make_device("spine-01")
    cr = CollectResult(
        device_id=device.id,
        device_name=device.name,
        success=True,
        data={"/interface": [{"name": "ethernet-1/1"}]},
        paths=list(DRIFT_PATHS),
    )
    collector = MagicMock()
    collector.collect = AsyncMock(return_value=cr)
    collector.get_running_config = AsyncMock()
    _install(collector)

    out = await collect_drift_state(device)
    assert out is cr
    collector.collect.assert_awaited_once_with(device, list(DRIFT_PATHS))
    collector.get_running_config.assert_not_awaited()
