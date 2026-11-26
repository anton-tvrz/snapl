"""apply_config and collect_drift_state time their gNMI call (spec 012, T004, FR-004, FR-005).

Each activity runs in Temporal's activity test environment with a fake meter,
which is how a real worker hands the activity its meter.
"""

from __future__ import annotations

from datetime import timedelta  # noqa: TC003
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio.testing import ActivityEnvironment

from snapl_collector.models import CollectResult
from snapl_executor.models import ApplyResult
from snapl_orchestrator.activities import Activities, set_activities
from snapl_orchestrator.activities.collector import collect_drift_state
from snapl_orchestrator.activities.executor import apply_config
from tests.unit.test_orchestrator.metric_fakes import ExplodingMeter, FakeMeter

pytestmark = pytest.mark.unit


def _install(*, executor=None, collector=None) -> None:
    set_activities(
        Activities(
            intent_store=MagicMock(),
            executor=executor or MagicMock(),
            collector=collector or MagicMock(),
            observer=MagicMock(),
            audit_log=MagicMock(),
        )
    )


def teardown_function() -> None:
    import snapl_orchestrator.activities as a

    a._activities = None


def _env(meter: object) -> ActivityEnvironment:
    env = ActivityEnvironment()
    env.metric_meter = meter  # type: ignore[assignment]
    return env


def _apply_result(desired, *, success: bool) -> ApplyResult:
    return ApplyResult(
        device_id=desired.device.id,
        device_name=desired.device.name,
        success=success,
        payload={},
        error=None if success else "connectivity error",
    )


def _collect_result(device, *, success: bool) -> CollectResult:
    return CollectResult(
        device_id=device.id,
        device_name=device.name,
        success=success,
        data={},
        paths=[],
        error=None if success else "timeout",
    )


def _only_sample(meter: FakeMeter) -> tuple[timedelta, dict]:
    [(name, _unit, value, attrs)] = meter.samples()
    assert name == "snapl_gnmi_call_duration"
    return value, attrs


class TestApplyConfig:
    @pytest.mark.parametrize(("success", "outcome"), [(True, "ok"), (False, "error")])
    async def test_records_configure_with_the_result_outcome(self, dcfabric_desired_state, success, outcome) -> None:
        result = _apply_result(dcfabric_desired_state, success=success)
        executor = MagicMock()
        executor.apply = AsyncMock(return_value=result)
        _install(executor=executor)
        meter = FakeMeter()

        out = await _env(meter).run(apply_config, dcfabric_desired_state)

        assert out is result
        _, attrs = _only_sample(meter)
        device = dcfabric_desired_state.device
        assert attrs == {
            "device": device.name,
            "use_case": device.use_case,
            "operation": "configure",
            "outcome": outcome,
        }

    async def test_a_raised_exception_records_error_and_re_raises_it(self, dcfabric_desired_state) -> None:
        boom = ConnectionError("gNMI channel dropped")
        executor = MagicMock()
        executor.apply = AsyncMock(side_effect=boom)
        _install(executor=executor)
        meter = FakeMeter()

        with pytest.raises(ConnectionError) as caught:
            await _env(meter).run(apply_config, dcfabric_desired_state)

        assert caught.value is boom
        _, attrs = _only_sample(meter)
        assert (attrs["operation"], attrs["outcome"]) == ("configure", "error")

    async def test_a_broken_meter_does_not_change_the_result(self, dcfabric_desired_state) -> None:
        result = _apply_result(dcfabric_desired_state, success=True)
        executor = MagicMock()
        executor.apply = AsyncMock(return_value=result)
        _install(executor=executor)

        out = await _env(ExplodingMeter()).run(apply_config, dcfabric_desired_state)

        assert out is result

    async def test_a_broken_meter_does_not_replace_the_original_exception(self, dcfabric_desired_state) -> None:
        boom = ConnectionError("gNMI channel dropped")
        executor = MagicMock()
        executor.apply = AsyncMock(side_effect=boom)
        _install(executor=executor)

        with pytest.raises(ConnectionError) as caught:
            await _env(ExplodingMeter()).run(apply_config, dcfabric_desired_state)

        assert caught.value is boom


class TestCollectDriftState:
    @pytest.mark.parametrize(("success", "outcome"), [(True, "ok"), (False, "error")])
    async def test_records_collect_with_the_result_outcome(self, make_device, success, outcome) -> None:
        device = make_device("spine-01")
        result = _collect_result(device, success=success)
        collector = MagicMock()
        collector.collect = AsyncMock(return_value=result)
        _install(collector=collector)
        meter = FakeMeter()

        out = await _env(meter).run(collect_drift_state, device)

        assert out is result
        _, attrs = _only_sample(meter)
        assert attrs == {
            "device": "spine-01",
            "use_case": device.use_case,
            "operation": "collect",
            "outcome": outcome,
        }

    async def test_a_raised_exception_records_error_and_re_raises_it(self, make_device) -> None:
        boom = TimeoutError("no answer")
        collector = MagicMock()
        collector.collect = AsyncMock(side_effect=boom)
        _install(collector=collector)
        meter = FakeMeter()

        with pytest.raises(TimeoutError) as caught:
            await _env(meter).run(collect_drift_state, make_device("spine-01"))

        assert caught.value is boom
        _, attrs = _only_sample(meter)
        assert (attrs["operation"], attrs["outcome"]) == ("collect", "error")
