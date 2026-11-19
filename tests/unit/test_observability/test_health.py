"""The health rule (spec 016 T012, research R6): one test per row of the rule.

The rule is pure: ``now`` is passed in, so every time here is exact.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from snapl_observability.health import evaluate_health
from snapl_observability.models import (
    DeviceHealthState,
    HealthExpectation,
    HealthObservation,
    HealthReason,
    ObservedItem,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 11, 19, 22, 0, 0, tzinfo=UTC)
_INTERVAL_S = 10


def _ago(seconds: float) -> datetime:
    return _NOW - timedelta(seconds=seconds)


def _expect(up=(), down=(), sessions=()) -> HealthExpectation:
    return HealthExpectation(
        device_name="leaf-01",
        interfaces_up=frozenset(up),
        interfaces_down=frozenset(down),
        sessions_established=frozenset(sessions),
    )


def _observe(*, last=0.0, interfaces=None, sessions=None) -> HealthObservation:
    """``interfaces``/``sessions``: name -> (state, seconds since it was first read)."""
    return HealthObservation(
        last_sample_at=None if last is None else _ago(last),
        interfaces={n: ObservedItem(state=s, since=_ago(age)) for n, (s, age) in (interfaces or {}).items()},
        sessions={n: ObservedItem(state=s, since=_ago(age)) for n, (s, age) in (sessions or {}).items()},
    )


def _evaluate(expectation, observation):
    return evaluate_health(expectation, observation, now=_NOW, interval_s=_INTERVAL_S)


def _texts(health) -> list[str]:
    return [r.text for r in health.reasons]


class TestStaleness:
    def test_never_sampled_is_unknown(self):
        health = _evaluate(_expect(up=["ethernet-1/49"]), _observe(last=None))
        assert health.state is DeviceHealthState.UNKNOWN
        assert health.reasons == (HealthReason.no_telemetry(None),)
        assert _texts(health) == ["no telemetry yet"]

    def test_last_sample_31_s_ago_is_unknown_with_its_time(self):
        health = _evaluate(_expect(up=["ethernet-1/49"]), _observe(last=31, interfaces={"ethernet-1/49": ("up", 60)}))
        assert health.state is DeviceHealthState.UNKNOWN
        assert _texts(health) == ["no telemetry since 2026-11-19T21:59:29+00:00"]

    def test_last_sample_29_s_ago_is_judged(self):
        health = _evaluate(_expect(up=["ethernet-1/49"]), _observe(last=29, interfaces={"ethernet-1/49": ("up", 60)}))
        assert health.state is DeviceHealthState.HEALTHY
        assert health.reasons == ()

    def test_exactly_three_intervals_is_still_judged(self):
        health = _evaluate(_expect(up=["lo0"]), _observe(last=30, interfaces={"lo0": ("up", 60)}))
        assert health.state is DeviceHealthState.HEALTHY

    def test_unknown_hides_faults(self):
        """Stale state is not evidence: an old fault does not make a silent device unhealthy."""
        health = _evaluate(_expect(up=["lo0"]), _observe(last=45, interfaces={"lo0": ("down", 60)}))
        assert health.state is DeviceHealthState.UNKNOWN
        assert [r.kind for r in health.reasons] == ["no_telemetry"]


class TestInterfaces:
    def test_expected_up_and_down_for_5_s_is_healthy(self):
        health = _evaluate(_expect(up=["ethernet-1/49"]), _observe(interfaces={"ethernet-1/49": ("down", 5)}))
        assert health.state is DeviceHealthState.HEALTHY

    def test_expected_up_and_down_for_exactly_one_interval_is_healthy(self):
        health = _evaluate(_expect(up=["ethernet-1/49"]), _observe(interfaces={"ethernet-1/49": ("down", 10)}))
        assert health.state is DeviceHealthState.HEALTHY

    def test_expected_up_and_down_for_11_s_is_unhealthy(self):
        health = _evaluate(_expect(up=["ethernet-1/49"]), _observe(interfaces={"ethernet-1/49": ("down", 11)}))
        assert health.state is DeviceHealthState.UNHEALTHY
        assert health.reasons == (HealthReason.interface("ethernet-1/49", actual="down", expected="up"),)
        assert _texts(health) == ["ethernet-1/49 down (expected up)"]

    def test_intended_down_and_up_is_healthy(self):
        """Being up against intent is drift, which the scan reports; not a health fault."""
        health = _evaluate(_expect(down=["ethernet-1/50"]), _observe(interfaces={"ethernet-1/50": ("up", 600)}))
        assert health.state is DeviceHealthState.HEALTHY

    def test_intended_down_and_down_is_healthy(self):
        health = _evaluate(_expect(down=["ethernet-1/50"]), _observe(interfaces={"ethernet-1/50": ("down", 600)}))
        assert health.state is DeviceHealthState.HEALTHY

    def test_an_expected_interface_never_reported_is_absent(self):
        health = _evaluate(
            _expect(up=["ethernet-1/49", "ethernet-1/9"]), _observe(interfaces={"ethernet-1/49": ("up", 60)})
        )
        assert health.state is DeviceHealthState.UNHEALTHY
        assert _texts(health) == ["ethernet-1/9 absent (expected up)"]

    def test_an_interface_intended_down_and_never_reported_is_absent(self):
        health = _evaluate(_expect(down=["ethernet-1/50"]), _observe())
        assert health.state is DeviceHealthState.UNHEALTHY
        assert _texts(health) == ["ethernet-1/50 absent (expected down)"]

    def test_an_interface_intent_does_not_name_is_ignored(self):
        health = _evaluate(_expect(up=["lo0"]), _observe(interfaces={"lo0": ("up", 60), "mgmt0": ("down", 600)}))
        assert health.state is DeviceHealthState.HEALTHY


class TestSessions:
    def test_a_session_active_for_11_s_is_unhealthy(self):
        health = _evaluate(_expect(sessions=["10.10.1.1"]), _observe(sessions={"10.10.1.1": ("active", 11)}))
        assert health.state is DeviceHealthState.UNHEALTHY
        assert health.reasons == (HealthReason.bgp_session("10.10.1.1", actual="active"),)
        assert _texts(health) == ["BGP 10.10.1.1 active (expected established)"]

    def test_a_session_active_for_5_s_is_healthy(self):
        health = _evaluate(_expect(sessions=["10.10.1.1"]), _observe(sessions={"10.10.1.1": ("active", 5)}))
        assert health.state is DeviceHealthState.HEALTHY

    def test_an_established_session_is_healthy(self):
        health = _evaluate(_expect(sessions=["10.10.1.1"]), _observe(sessions={"10.10.1.1": ("established", 600)}))
        assert health.state is DeviceHealthState.HEALTHY

    def test_an_expected_session_never_reported_is_absent(self):
        health = _evaluate(_expect(sessions=["10.10.1.1"]), _observe())
        assert _texts(health) == ["BGP 10.10.1.1 absent (expected established)"]

    def test_a_session_intent_does_not_expect_is_ignored(self):
        health = _evaluate(_expect(), _observe(sessions={"10.10.9.9": ("idle", 600)}))
        assert health.state is DeviceHealthState.HEALTHY


class TestReasons:
    def test_a_link_down_and_its_session_down_list_both_interfaces_first(self):
        health = _evaluate(
            _expect(up=["ethernet-1/49"], sessions=["10.10.1.0"]),
            _observe(interfaces={"ethernet-1/49": ("down", 20)}, sessions={"10.10.1.0": ("idle", 20)}),
        )
        assert health.state is DeviceHealthState.UNHEALTHY
        assert _texts(health) == ["ethernet-1/49 down (expected up)", "BGP 10.10.1.0 idle (expected established)"]

    def test_reasons_are_ordered_by_name_then_peer(self):
        health = _evaluate(
            _expect(up=["ethernet-1/50", "ethernet-1/49"], down=["ethernet-1/1"], sessions=["10.10.2.0", "10.10.1.0"]),
            _observe(
                interfaces={"ethernet-1/50": ("down", 20), "ethernet-1/49": ("down", 20)},
                sessions={"10.10.2.0": ("active", 20), "10.10.1.0": ("active", 20)},
            ),
        )
        assert [r.item for r in health.reasons] == [
            "ethernet-1/1",
            "ethernet-1/49",
            "ethernet-1/50",
            "10.10.1.0",
            "10.10.2.0",
        ]

    def test_the_health_names_the_device_and_the_evaluation_time(self):
        health = _evaluate(_expect(up=["lo0"]), _observe(interfaces={"lo0": ("up", 60)}))
        assert health.device_name == "leaf-01"
        assert health.evaluated_at == _NOW


class TestCpuAndMemory:
    def test_cpu_and_memory_are_not_inputs(self):
        """FR-008: neither model has a place for them, so the rule cannot read them."""
        assert not {f for f in HealthExpectation.model_fields if "cpu" in f or "memory" in f}
        assert not {f for f in HealthObservation.model_fields if "cpu" in f or "memory" in f}


class TestInterval:
    def test_the_limits_follow_the_interval(self):
        observation = _observe(last=50, interfaces={"lo0": ("down", 25)})
        at_10 = evaluate_health(_expect(up=["lo0"]), observation, now=_NOW, interval_s=10)
        at_20 = evaluate_health(_expect(up=["lo0"]), observation, now=_NOW, interval_s=20)
        assert at_10.state is DeviceHealthState.UNKNOWN  # 50 s > 30 s
        assert at_20.state is DeviceHealthState.UNHEALTHY  # 50 s <= 60 s, and down 25 s > 20 s
