"""Unit tests for the collect_running_state activity."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from snapl_collector.models import CollectResult
from snapl_orchestrator.activities import Activities, set_activities
from snapl_orchestrator.activities.collector import collect_running_state

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
async def test_collect_running_state_with_paths_uses_collect(make_device) -> None:
    device = make_device("spine-01")
    cr = CollectResult(
        device_id=device.id,
        device_name=device.name,
        success=True,
        data={"/interface": [{"name": "ethernet-1/1"}]},
        paths=["/interface"],
    )
    collector = MagicMock()
    collector.collect = AsyncMock(return_value=cr)
    collector.get_running_config = AsyncMock()
    _install(collector)

    out = await collect_running_state(device, ["/interface"])
    assert out is cr
    collector.collect.assert_awaited_once_with(device, ["/interface"])
    collector.get_running_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_collect_running_state_empty_paths_uses_get_running_config(make_device) -> None:
    device = make_device("spine-01")
    cr = CollectResult(
        device_id=device.id,
        device_name=device.name,
        success=True,
        data={"/": {"interface": []}},
        paths=["/"],
    )
    collector = MagicMock()
    collector.collect = AsyncMock()
    collector.get_running_config = AsyncMock(return_value=cr)
    _install(collector)

    out = await collect_running_state(device, [])
    assert out is cr
    collector.get_running_config.assert_awaited_once_with(device)
    collector.collect.assert_not_awaited()
