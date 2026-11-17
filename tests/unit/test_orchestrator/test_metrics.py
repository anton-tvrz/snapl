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
