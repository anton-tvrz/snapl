"""Unit tests for the apply_config activity."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from snapl_executor.models import ApplyResult
from snapl_orchestrator.activities import Activities, set_activities
from snapl_orchestrator.activities.executor import apply_config

pytestmark = pytest.mark.unit


def _install(executor) -> None:
    set_activities(
        Activities(
            intent_store=MagicMock(),
            executor=executor,
            collector=MagicMock(),
            observer=MagicMock(),
            audit_log=MagicMock(),
        )
    )


def teardown_function() -> None:
    import snapl_orchestrator.activities as a

    a._activities = None


@pytest.mark.asyncio
async def test_apply_config_delegates_to_executor(dcfabric_desired_state) -> None:
    apply_result = ApplyResult(
        device_id=dcfabric_desired_state.device.id,
        device_name=dcfabric_desired_state.device.name,
        success=True,
        payload={"/interface": [{"name": "ethernet-1/1"}]},
        duration_ms=42,
    )
    executor = MagicMock()
    executor.apply = AsyncMock(return_value=apply_result)
    _install(executor)

    out = await apply_config(dcfabric_desired_state)

    assert out is apply_result
    executor.apply.assert_awaited_once_with(dcfabric_desired_state)


@pytest.mark.asyncio
async def test_apply_config_returns_failure_result(dcfabric_desired_state) -> None:
    apply_result = ApplyResult(
        device_id=dcfabric_desired_state.device.id,
        device_name=dcfabric_desired_state.device.name,
        success=False,
        payload={},
        error="connectivity error",
    )
    executor = MagicMock()
    executor.apply = AsyncMock(return_value=apply_result)
    _install(executor)

    out = await apply_config(dcfabric_desired_state)
    assert out.success is False
    assert out.error == "connectivity error"


# ---------------------------------------------------------------------------
# dry_run_remove (spec 013 T015)
# ---------------------------------------------------------------------------

_PORT = "/interface[name=ethernet-1/7]"


@pytest.mark.asyncio
async def test_dry_run_remove_delegates_to_the_executor_and_dials_nothing(dcfabric_desired_state) -> None:
    from temporalio.testing import ActivityEnvironment

    from snapl_executor.models import RemoveResult
    from snapl_orchestrator.activities.executor import dry_run_remove
    from tests.unit.test_orchestrator.metric_fakes import FakeMeter

    device = dcfabric_desired_state.device
    checked = RemoveResult(device_id=device.id, device_name=device.name, success=True, paths=[_PORT], dry_run=True)
    executor = MagicMock()
    executor.dry_run_remove = AsyncMock(return_value=checked)
    _install(executor)
    env = ActivityEnvironment()
    meter = FakeMeter()
    env.metric_meter = meter  # type: ignore[assignment]

    out = await env.run(dry_run_remove, device, [_PORT])

    assert out is checked
    executor.dry_run_remove.assert_awaited_once_with(device, [_PORT])
    assert meter.samples() == []
