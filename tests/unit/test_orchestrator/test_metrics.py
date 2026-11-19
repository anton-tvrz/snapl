"""The worker's own metrics: gNMI timing (spec 012, T003, R5) and the three counters (spec 015, T031).

The meter is injected through the activity test environment, which is how the
Temporal runtime hands a meter to a real activity. No runtime is started.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from temporalio.testing import ActivityEnvironment

from snapl_observability.models import DriftItem, DriftReport, DriftStatus
from snapl_orchestrator import metrics as metrics_module
from snapl_orchestrator.metrics import (
    DEPLOY_ENDS,
    DRIFT_PATHS,
    GNMI_CALL_DURATION,
    SCAN_RESULTS,
    CallOutcome,
    DeployOutcome,
    GnmiOperation,
    PathKind,
    ScanState,
    record_deploy_end,
    record_drift_paths,
    record_gnmi_call,
    record_scan_result,
)
from snapl_orchestrator.models import WorkflowReason
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


# ---------------------------------------------------------------------------
# Spec 015, PR E: the worker's three counters (T031, FR-010 to FR-014, SC-003)
# ---------------------------------------------------------------------------

SCAN_LABELS = frozenset({"device", "use_case", "state"})
PATH_LABELS = frozenset({"device", "use_case", "kind"})
DEPLOY_LABELS = frozenset({"device", "use_case", "outcome", "reason"})


def _item(path: str, *, undesired: bool) -> DriftItem:
    if undesired:
        return DriftItem(path=path, desired=None, actual="x", entity_kind="interface", undesired=True)
    return DriftItem(path=path, desired="x", actual=None, entity_kind="interface")


def _report(device, status: DriftStatus, *, missing: int = 0, undesired: int = 0) -> DriftReport:
    items = [_item(f"/m/{i}", undesired=False) for i in range(missing)]
    items += [_item(f"/u/{i}", undesired=True) for i in range(undesired)]
    return DriftReport(
        device_id=device.id,
        device_name=device.name,
        status=status,
        items=items,
        error="unreachable" if status is DriftStatus.ERROR else None,
        timestamp=datetime.now(UTC),
    )


class TestRecordScanResult:
    @pytest.mark.parametrize(
        ("status", "missing", "undesired", "state"),
        [
            (DriftStatus.CLEAN, 0, 0, "in_sync"),
            (DriftStatus.DRIFTED, 2, 0, "missing"),
            (DriftStatus.DRIFTED, 0, 1, "undesired"),
            (DriftStatus.DRIFTED, 1, 1, "missing_and_undesired"),
            (DriftStatus.ERROR, 0, 0, "error"),
        ],
    )
    def test_adds_one_with_the_device_s_state(self, make_device, status, missing, undesired, state) -> None:
        meter = FakeMeter()
        device = make_device("leaf-01")

        _env(meter).run(record_scan_result, device, _report(device, status, missing=missing, undesired=undesired))

        assert meter.adds() == [
            ("snapl_scan_results_total", 1, {"device": "leaf-01", "use_case": "dcfabric", "state": state})
        ]

    def test_label_keys_are_exactly_the_documented_set(self, make_device) -> None:
        """FR-013: device, use case and state, nothing else."""
        meter = FakeMeter()
        device = make_device("leaf-01")
        for status, missing, undesired in [
            (DriftStatus.CLEAN, 0, 0),
            (DriftStatus.DRIFTED, 1, 1),
            (DriftStatus.ERROR, 0, 0),
        ]:
            _env(meter).run(record_scan_result, device, _report(device, status, missing=missing, undesired=undesired))

        assert {frozenset(attrs) for _, _, attrs in meter.adds()} == {SCAN_LABELS}

    def test_a_status_outside_the_enum_is_recorded_as_unknown(self, make_device) -> None:
        meter = FakeMeter()
        device = make_device("leaf-01")
        report = _report(device, DriftStatus.CLEAN).model_copy(update={"status": "sideways"})

        _env(meter).run(record_scan_result, device, report)

        [(_, _, attrs)] = meter.adds()
        assert attrs["state"] == "unknown"

    def test_the_states_are_bounded(self) -> None:
        """SC-003: at most seven series per device."""
        assert {s.value for s in ScanState} == {
            "in_sync",
            "missing",
            "undesired",
            "missing_and_undesired",
            "error",
            "unknown",
        }
        assert SCAN_RESULTS == "snapl_scan_results_total"

    def test_a_broken_meter_never_raises(self, make_device, caplog: pytest.LogCaptureFixture) -> None:
        device = make_device("leaf-01")

        _env(ExplodingMeter()).run(record_scan_result, device, _report(device, DriftStatus.CLEAN))

        assert any("snapl_scan_results_total" in r.getMessage() for r in caplog.records)

    def test_outside_an_activity_it_does_nothing(self, make_device) -> None:
        device = make_device("leaf-01")

        record_scan_result(device, _report(device, DriftStatus.CLEAN))


class TestRecordDriftPaths:
    def test_adds_the_number_of_paths_of_each_kind(self, make_device) -> None:
        meter = FakeMeter()
        device = make_device("leaf-01")

        _env(meter).run(record_drift_paths, device, _report(device, DriftStatus.DRIFTED, missing=3, undesired=1))

        assert sorted(meter.adds(), key=lambda a: a[2]["kind"]) == [
            ("snapl_drift_paths_total", 3, {"device": "leaf-01", "use_case": "dcfabric", "kind": "missing"}),
            ("snapl_drift_paths_total", 1, {"device": "leaf-01", "use_case": "dcfabric", "kind": "undesired"}),
        ]

    @pytest.mark.parametrize("status", [DriftStatus.CLEAN, DriftStatus.ERROR])
    def test_a_scan_without_paths_adds_nothing(self, make_device, status) -> None:
        meter = FakeMeter()
        device = make_device("leaf-01")

        _env(meter).run(record_drift_paths, device, _report(device, status))

        assert meter.adds() == []

    def test_label_keys_are_exactly_the_documented_set(self, make_device) -> None:
        """FR-013 and SC-003: no label carries a path."""
        meter = FakeMeter()
        device = make_device("leaf-01")

        _env(meter).run(record_drift_paths, device, _report(device, DriftStatus.DRIFTED, missing=2, undesired=2))

        assert {frozenset(attrs) for _, _, attrs in meter.adds()} == {PATH_LABELS}
        assert not any("/" in value for _, _, attrs in meter.adds() for value in attrs.values())

    def test_the_kinds_are_the_documented_three(self) -> None:
        assert {k.value for k in PathKind} == {"missing", "undesired", "unclassified"}
        assert DRIFT_PATHS == "snapl_drift_paths_total"

    def test_a_broken_meter_never_raises(self, make_device, caplog: pytest.LogCaptureFixture) -> None:
        device = make_device("leaf-01")

        _env(ExplodingMeter()).run(
            record_drift_paths, device, _report(device, DriftStatus.DRIFTED, missing=1, undesired=0)
        )

        assert any("snapl_drift_paths_total" in r.getMessage() for r in caplog.records)

    def test_outside_an_activity_it_does_nothing(self, make_device) -> None:
        device = make_device("leaf-01")

        record_drift_paths(device, _report(device, DriftStatus.DRIFTED, missing=1))


class TestRecordDeployEnd:
    """The deploy counter is recorded from the workflow, through workflow.metric_meter()."""

    @pytest.fixture
    def meter(self, monkeypatch: pytest.MonkeyPatch) -> FakeMeter:
        meter = FakeMeter()
        monkeypatch.setattr(metrics_module.workflow, "in_workflow", lambda: True)
        monkeypatch.setattr(metrics_module.workflow, "metric_meter", lambda: meter)
        return meter

    def test_a_success_adds_one_with_reason_none(self, meter: FakeMeter) -> None:
        record_deploy_end("leaf-01", "dcfabric", WorkflowReason.SUCCEEDED)

        assert meter.adds() == [
            (
                "snapl_deploy_ends_total",
                1,
                {"device": "leaf-01", "use_case": "dcfabric", "outcome": "succeeded", "reason": "none"},
            )
        ]

    def test_a_failure_adds_one_with_its_reason(self, meter: FakeMeter) -> None:
        record_deploy_end("leaf-01", "dcfabric", WorkflowReason.VERIFICATION_FAILED)

        [(_, value, attrs)] = meter.adds()
        assert value == 1
        assert attrs["outcome"] == "failed"
        assert attrs["reason"] == "verification_failed"

    def test_a_reason_outside_the_enum_is_recorded_as_unknown(self, meter: FakeMeter) -> None:
        record_deploy_end("leaf-01", "dcfabric", "something went sideways at 10.0.0.1")

        [(_, _, attrs)] = meter.adds()
        assert (attrs["outcome"], attrs["reason"]) == ("failed", "unknown")

    def test_label_keys_are_exactly_the_documented_set(self, meter: FakeMeter) -> None:
        """FR-013: outcome and reason per device, nothing else."""
        for reason in WorkflowReason:
            record_deploy_end("leaf-01", "dcfabric", reason)

        assert {frozenset(attrs) for _, _, attrs in meter.adds()} == {DEPLOY_LABELS}
        assert {(a["outcome"], a["reason"]) for _, _, a in meter.adds()} == {
            ("succeeded", "none"),
            *(("failed", r.value) for r in WorkflowReason if r is not WorkflowReason.SUCCEEDED),
        }

    def test_the_outcomes_are_the_documented_two(self) -> None:
        assert {o.value for o in DeployOutcome} == {"succeeded", "failed"}
        assert DEPLOY_ENDS == "snapl_deploy_ends_total"

    def test_a_broken_meter_never_raises(self, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
        monkeypatch.setattr(metrics_module.workflow, "in_workflow", lambda: True)
        monkeypatch.setattr(metrics_module.workflow, "metric_meter", ExplodingMeter)

        record_deploy_end("leaf-01", "dcfabric", WorkflowReason.APPLY_FAILED)

        assert any("snapl_deploy_ends_total" in r.getMessage() for r in caplog.records)

    def test_outside_a_workflow_it_does_nothing(self) -> None:
        record_deploy_end("leaf-01", "dcfabric", WorkflowReason.SUCCEEDED)


# ---------------------------------------------------------------------------
# Spec 016 T017 — the eight telemetry families (research R5, contracts/telemetry.md)
# ---------------------------------------------------------------------------


class TestTelemetryFamilies:
    """One test per family holds its name, its type and its label set (FR-013)."""

    @staticmethod
    def _interface(**values):
        from snapl_collector.models import InterfaceSample

        return InterfaceSample(name="ethernet-1/49", **values)

    def test_interface_up_is_a_gauge_of_one_or_zero(self, make_device) -> None:
        from snapl_orchestrator.metrics import INTERFACE_UP, CounterTotals, record_interface

        meter = FakeMeter()
        device = make_device("leaf-01")
        record_interface(meter, device, self._interface(oper_up=True), CounterTotals())
        record_interface(meter, device, self._interface(oper_up=False), CounterTotals())

        assert INTERFACE_UP == "snapl_interface_up"
        labels = {"device": "leaf-01", "use_case": device.use_case, "interface": "ethernet-1/49"}
        assert meter.sets(INTERFACE_UP) == [(INTERFACE_UP, 1, labels), (INTERFACE_UP, 0, labels)]
        assert meter.adds() == []

    @pytest.mark.parametrize(
        ("family", "name", "fields"),
        [
            ("INTERFACE_OCTETS", "snapl_interface_octets_total", ("in_octets", "out_octets")),
            ("INTERFACE_ERRORS", "snapl_interface_errors_total", ("in_errors", "out_errors")),
            ("INTERFACE_DISCARDS", "snapl_interface_discards_total", ("in_discards", "out_discards")),
        ],
    )
    def test_interface_counters_carry_a_direction(self, make_device, family, name, fields) -> None:
        import snapl_orchestrator.metrics as m

        meter = FakeMeter()
        device = make_device("leaf-01")
        totals = m.CounterTotals()
        m.record_interface(meter, device, self._interface(**dict.fromkeys(fields, 100)), totals)
        m.record_interface(meter, device, self._interface(**dict.fromkeys(fields, 130)), totals)

        assert getattr(m, family) == name
        base = {"device": "leaf-01", "use_case": device.use_case, "interface": "ethernet-1/49"}
        assert meter.adds(name) == [
            (name, 0, {**base, "direction": "in"}),
            (name, 0, {**base, "direction": "out"}),
            (name, 30, {**base, "direction": "in"}),
            (name, 30, {**base, "direction": "out"}),
        ]
        assert meter.sets() == []

    def test_direction_is_an_enum(self) -> None:
        from snapl_orchestrator.metrics import Direction

        assert [d.value for d in Direction] == ["in", "out"]

    def test_a_leaf_the_message_did_not_carry_records_nothing(self, make_device) -> None:
        from snapl_orchestrator.metrics import CounterTotals, record_interface

        meter = FakeMeter()
        record_interface(meter, make_device("leaf-01"), self._interface(), CounterTotals())

        assert meter.adds() == []
        assert meter.sets() == []

    def test_bgp_session_established_is_a_gauge_of_one_or_zero(self, make_device) -> None:
        from snapl_collector.models import BgpSessionSample
        from snapl_orchestrator.metrics import BGP_SESSION_ESTABLISHED, record_session

        meter = FakeMeter()
        device = make_device("spine-01")
        record_session(meter, device, BgpSessionSample(peer="10.10.1.1", state="established"))
        record_session(meter, device, BgpSessionSample(peer="10.10.1.1", state="active"))

        assert BGP_SESSION_ESTABLISHED == "snapl_bgp_session_established"
        labels = {"device": "spine-01", "use_case": device.use_case, "peer": "10.10.1.1"}
        assert meter.sets(BGP_SESSION_ESTABLISHED) == [
            (BGP_SESSION_ESTABLISHED, 1, labels),
            (BGP_SESSION_ESTABLISHED, 0, labels),
        ]

    def test_bgp_prefixes_received_is_a_gauge(self, make_device) -> None:
        from snapl_collector.models import BgpSessionSample
        from snapl_orchestrator.metrics import BGP_PREFIXES_RECEIVED, record_session

        meter = FakeMeter()
        device = make_device("spine-01")
        record_session(meter, device, BgpSessionSample(peer="10.10.1.1", prefixes_received=12))

        assert BGP_PREFIXES_RECEIVED == "snapl_bgp_prefixes_received"
        assert meter.sets() == [
            (BGP_PREFIXES_RECEIVED, 12, {"device": "spine-01", "use_case": device.use_case, "peer": "10.10.1.1"})
        ]

    @pytest.mark.parametrize(
        ("family", "name", "field"),
        [
            ("DEVICE_CPU_PERCENT", "snapl_device_cpu_percent", "cpu_percent"),
            ("DEVICE_MEMORY_PERCENT", "snapl_device_memory_percent", "memory_percent"),
        ],
    )
    def test_cpu_and_memory_are_gauges_by_device(self, make_device, family, name, field) -> None:
        import snapl_orchestrator.metrics as m
        from snapl_collector.models import PlatformSample

        meter = FakeMeter()
        device = make_device("leaf-01")
        m.record_platform(meter, device, PlatformSample(**{field: 42}))

        assert getattr(m, family) == name
        assert meter.sets() == [(name, 42, {"device": "leaf-01", "use_case": device.use_case})]

    def test_every_family_is_listed_once(self) -> None:
        from snapl_orchestrator.metrics import TELEMETRY_FAMILIES

        assert TELEMETRY_FAMILIES == {
            "snapl_interface_up": "gauge",
            "snapl_interface_octets_total": "counter",
            "snapl_interface_errors_total": "counter",
            "snapl_interface_discards_total": "counter",
            "snapl_bgp_session_established": "gauge",
            "snapl_bgp_prefixes_received": "gauge",
            "snapl_device_cpu_percent": "gauge",
            "snapl_device_memory_percent": "gauge",
        }


class TestCounterTotals:
    """Research R5: the device sends totals, the meter takes increments (FR-014)."""

    def test_the_first_total_adds_zero(self) -> None:
        from snapl_orchestrator.metrics import CounterTotals

        assert CounterTotals().delta("leaf-01", "ethernet-1/49", "in_octets", 2925084) == 0

    def test_a_rise_adds_the_difference(self) -> None:
        from snapl_orchestrator.metrics import CounterTotals

        totals = CounterTotals()
        totals.delta("leaf-01", "ethernet-1/49", "in_octets", 100)
        assert totals.delta("leaf-01", "ethernet-1/49", "in_octets", 130) == 30
        assert totals.delta("leaf-01", "ethernet-1/49", "in_octets", 130) == 0

    def test_a_fall_adds_the_new_total(self) -> None:
        """The device's counter restarted: everything since then is new."""
        from snapl_orchestrator.metrics import CounterTotals

        totals = CounterTotals()
        totals.delta("leaf-01", "ethernet-1/49", "in_octets", 1000)
        assert totals.delta("leaf-01", "ethernet-1/49", "in_octets", 40) == 40

    def test_series_are_kept_apart(self) -> None:
        from snapl_orchestrator.metrics import CounterTotals

        totals = CounterTotals()
        totals.delta("leaf-01", "ethernet-1/49", "in_octets", 100)
        assert totals.delta("leaf-01", "ethernet-1/49", "out_octets", 500) == 0
        assert totals.delta("leaf-02", "ethernet-1/49", "in_octets", 900) == 0

    def test_a_stream_restart_resets_that_devices_totals(self) -> None:
        from snapl_orchestrator.metrics import CounterTotals

        totals = CounterTotals()
        totals.delta("leaf-01", "ethernet-1/49", "in_octets", 100)
        totals.delta("leaf-02", "ethernet-1/49", "in_octets", 100)
        totals.reset("leaf-01")
        assert totals.delta("leaf-01", "ethernet-1/49", "in_octets", 5000) == 0
        assert totals.delta("leaf-02", "ethernet-1/49", "in_octets", 150) == 50


class TestTelemetryRecordersNeverRaise:
    def test_interface(self, make_device, caplog) -> None:
        from snapl_collector.models import InterfaceSample
        from snapl_orchestrator.metrics import CounterTotals, record_interface

        sample = InterfaceSample(name="ethernet-1/49", oper_up=True, in_octets=5, out_octets=7)
        with caplog.at_level("WARNING", logger="snapl_orchestrator.metrics"):
            record_interface(ExplodingMeter(), make_device("leaf-01"), sample, CounterTotals())  # type: ignore[arg-type]
        assert any("leaf-01" in r.getMessage() for r in caplog.records)

    def test_session(self, make_device) -> None:
        from snapl_collector.models import BgpSessionSample
        from snapl_orchestrator.metrics import record_session

        sample = BgpSessionSample(peer="10.10.1.1", state="active", prefixes_received=0)
        record_session(ExplodingMeter(), make_device("spine-01"), sample)  # type: ignore[arg-type]

    def test_platform(self, make_device) -> None:
        from snapl_collector.models import PlatformSample
        from snapl_orchestrator.metrics import record_platform

        record_platform(ExplodingMeter(), make_device("leaf-01"), PlatformSample(cpu_percent=5, memory_percent=94))  # type: ignore[arg-type]
