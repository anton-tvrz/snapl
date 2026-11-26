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


# ---------------------------------------------------------------------------
# remove_config (spec 013 T028)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("success", "outcome"), [(True, "ok"), (False, "error")])
async def test_remove_config_delegates_and_records_a_configure_call(dcfabric_desired_state, success, outcome) -> None:
    from temporalio.testing import ActivityEnvironment

    from snapl_executor.models import RemoveResult
    from snapl_orchestrator.activities.executor import remove_config
    from tests.unit.test_orchestrator.metric_fakes import FakeMeter

    device = dcfabric_desired_state.device
    sent = RemoveResult(
        device_id=device.id,
        device_name=device.name,
        success=success,
        paths=[_PORT],
        dry_run=False,
        device_response="ok" if success else None,
        error=None if success else "[FailedPrecondition] subinterface ethernet-1/7.0 not found",
    )
    executor = MagicMock()
    executor.remove = AsyncMock(return_value=sent)
    _install(executor)
    env = ActivityEnvironment()
    meter = FakeMeter()
    env.metric_meter = meter  # type: ignore[assignment]

    out = await env.run(remove_config, device, [_PORT])

    assert out is sent
    executor.remove.assert_awaited_once_with(device, [_PORT])
    [(name, _, _, attrs)] = meter.samples()
    assert name == "snapl_gnmi_call_duration"
    assert (attrs["operation"], attrs["outcome"]) == ("configure", outcome)


@pytest.mark.asyncio
async def test_remove_config_records_an_error_when_the_executor_raises(dcfabric_desired_state) -> None:
    from temporalio.testing import ActivityEnvironment

    from snapl_orchestrator.activities.executor import remove_config
    from tests.unit.test_orchestrator.metric_fakes import FakeMeter

    executor = MagicMock()
    executor.remove = AsyncMock(side_effect=RuntimeError("boom"))
    _install(executor)
    env = ActivityEnvironment()
    meter = FakeMeter()
    env.metric_meter = meter  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="boom"):
        await env.run(remove_config, dcfabric_desired_state.device, [_PORT])

    [(_, _, _, attrs)] = meter.samples()
    assert attrs["outcome"] == "error"
