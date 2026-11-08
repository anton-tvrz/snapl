"""record_gnmi_call: the one place the gNMI timing metric is named (spec 012, T003, R5).

The meter is injected through the activity test environment, which is how the
Temporal runtime hands a meter to a real activity. No runtime is started.
"""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from temporalio.testing import ActivityEnvironment

from snapl_orchestrator.metrics import GNMI_CALL_DURATION, CallOutcome, GnmiOperation, record_gnmi_call
from tests.unit.test_orchestrator.metric_fakes import ExplodingMeter, FakeMeter

pytestmark = pytest.mark.unit


def _env(meter: object) -> ActivityEnvironment:
    env = ActivityEnvironment()
    env.metric_meter = meter  # type: ignore[assignment]
    return env


def test_records_one_sample_in_milliseconds_with_the_four_labels(make_device) -> None:
    meter = FakeMeter()
    device = make_device("leaf-01")
    started = time.monotonic() - 0.25

    _env(meter).run(record_gnmi_call, device, GnmiOperation.CONFIGURE, started, CallOutcome.OK)

    [(name, unit, value, attrs)] = meter.samples()
    assert name == "snapl_gnmi_call_duration"
    assert unit == "ms"
    assert value >= timedelta(milliseconds=250)
    assert attrs == {
        "device": "leaf-01",
        "use_case": device.use_case,
        "operation": "configure",
        "outcome": "ok",
    }


def test_label_keys_are_exactly_the_documented_set(make_device) -> None:
    """SC-006: no other label may reach the metric."""
    meter = FakeMeter()
    for operation in GnmiOperation:
        for outcome in CallOutcome:
            _env(meter).run(record_gnmi_call, make_device("spine-01"), operation, time.monotonic(), outcome)

    keys = {frozenset(attrs) for _, _, _, attrs in meter.samples()}
    assert keys == {frozenset({"device", "use_case", "operation", "outcome"})}


def test_label_values_are_plain_strings(make_device) -> None:
    meter = FakeMeter()

    _env(meter).run(
        record_gnmi_call, make_device("spine-01"), GnmiOperation.COLLECT, time.monotonic(), CallOutcome.ERROR
    )

    [(_, _, _, attrs)] = meter.samples()
    assert attrs["operation"] == "collect"
    assert attrs["outcome"] == "error"
    assert all(type(v) is str for v in attrs.values())


def test_the_enums_hold_exactly_the_documented_values() -> None:
    assert {o.value for o in GnmiOperation} == {"configure", "collect"}
    assert {o.value for o in CallOutcome} == {"ok", "error"}
    assert GNMI_CALL_DURATION == "snapl_gnmi_call_duration"


def test_a_broken_meter_never_raises(make_device, caplog: pytest.LogCaptureFixture) -> None:
    """Metrics must never fail a gNMI call (FR-002, FR-004): the recorder runs in a finally."""
    _env(ExplodingMeter()).run(
        record_gnmi_call, make_device("spine-01"), GnmiOperation.CONFIGURE, time.monotonic(), CallOutcome.OK
    )

    assert any("snapl_gnmi_call_duration" in r.getMessage() for r in caplog.records)


def test_outside_an_activity_it_does_nothing(make_device) -> None:
    """No activity context means no meter; that is not an error worth raising."""
    record_gnmi_call(make_device("spine-01"), GnmiOperation.CONFIGURE, time.monotonic(), CallOutcome.OK)
