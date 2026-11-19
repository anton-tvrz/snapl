"""The dashboard's Prometheus client (spec 012 T011, research R7, data-model.md).

Every response comes from the captured Prometheus v3.10.0 fixtures in
fixtures/prometheus/, served through ``httpx.MockTransport``. Where a test needs
a value the capture doesn't hold, it edits a copy and says so.
"""

from __future__ import annotations

import copy
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from snapl_presentation.dashboard.models import MetricsViewName, SourceName
from snapl_presentation.dashboard.prometheus import (
    MAX_POINTS,
    VIEWS,
    PrometheusSource,
    RangePlan,
    choose_step,
    parse_matrix,
    plan_range,
    rate_window,
    render_query,
    window_note,
)
from snapl_presentation.dashboard.sources import SourceUnavailable

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent / "fixtures" / "prometheus"
ADDRESS = "http://prom.test:9090"
T0 = datetime(2026, 11, 8, 20, 0, tzinfo=UTC)


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def response_from(captured: dict[str, Any]) -> httpx.Response:
    if "body_text" in captured:
        return httpx.Response(captured["http_status"], text=captured["body_text"])
    return httpx.Response(captured["http_status"], json=captured["body"])


class FakeProm:
    """Answers query_range with one fixture and /-/ready with another, recording requests."""

    def __init__(self, query_range: dict[str, Any] | Exception, ready: dict[str, Any] | Exception | None = None):
        self.query_range = query_range
        self.ready = ready if ready is not None else fixture("ready")
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.ready if request.url.path == "/-/ready" else self.query_range
        if isinstance(answer, Exception):
            raise answer
        return response_from(answer)

    def range_queries(self) -> list[dict[str, list[str]]]:
        return [parse_qs(r.url.query.decode()) for r in self.requests if r.url.path == "/api/v1/query_range"]


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def source(prom: FakeProm, clock: Clock | None = None) -> PrometheusSource:
    return PrometheusSource(
        ADDRESS,
        "default",
        "dcfabric",
        timeout=1.0,
        transport=httpx.MockTransport(prom),
        clock=clock or Clock(),
    )


def an_hour() -> RangePlan:
    return plan_range(T0 - timedelta(hours=1), T0, 15)


# --- named views (FR-011) --------------------------------------------------------


def _rate(selector: str, window: str = "60s", seconds: int = 60) -> str:
    """A rate that also counts a series' first observation (#191), as data-model.md gives it."""
    return (
        f'((({selector} unless {selector} offset {window}) and on() (up{{job="snapl-worker"}} offset {window}))'
        f" / {seconds} or rate({selector}[{window}]))"
    )


EXPECTED_QUERIES = {
    MetricsViewName.WORKFLOW_DURATION: (
        "histogram_quantile(0.95, sum by (le, workflow_type) "
        f"({_rate('temporal_workflow_endtoend_latency_bucket{namespace="default"}')}))"
    ),
    MetricsViewName.GNMI_DURATION: (
        "histogram_quantile(0.95, sum by (le, device, operation) "
        f"({_rate('snapl_gnmi_call_duration_bucket{use_case="dcfabric"}')}))"
    ),
    MetricsViewName.GNMI_ERRORS: (
        f"sum by (device) ({_rate('snapl_gnmi_call_duration_count{use_case="dcfabric",outcome="error"}')})"
    ),
    MetricsViewName.WORKER_LAG: (
        "histogram_quantile(0.95, sum by (le) "
        f"({_rate('temporal_activity_schedule_to_start_latency_bucket{namespace="default"}')}))"
    ),
    MetricsViewName.WORKER_UP: 'up{job="snapl-worker"}',
    MetricsViewName.SOURCE_HEALTH: "snapl_dashboard_source_up",
    # Spec 015 (contracts/metrics-views.md): means, counts and current values.
    MetricsViewName.WORKFLOW_MEAN: (
        f"sum by (workflow_type) ({_rate('temporal_workflow_endtoend_latency_sum{namespace="default"}')})"
        f" / sum by (workflow_type) ({_rate('temporal_workflow_endtoend_latency_count{namespace="default"}')})"
    ),
    MetricsViewName.GNMI_MEAN: (
        f"sum by (device, operation) ({_rate('snapl_gnmi_call_duration_sum{use_case="dcfabric"}')})"
        f" / sum by (device, operation) ({_rate('snapl_gnmi_call_duration_count{use_case="dcfabric"}')})"
    ),
    MetricsViewName.WORKFLOWS_COMPLETED: (
        f"sum by (workflow_type) (({_rate('temporal_workflow_completed{namespace="default"}')} * 60))"
    ),
    MetricsViewName.ACTIVITY_TIME: (
        f"sum by (activity_type) ({_rate('temporal_activity_execution_latency_sum{namespace="default"}')})"
        f" / sum by (activity_type) ({_rate('temporal_activity_execution_latency_count{namespace="default"}')})"
    ),
    MetricsViewName.WORKER_SLOTS: 'sum by (worker_type) (temporal_worker_task_slots_used{namespace="default"})',
    MetricsViewName.SERVER_REQUEST_TIME: (
        f"sum({_rate('temporal_request_latency_sum{namespace="default"}')})"
        f" / sum({_rate('temporal_request_latency_count{namespace="default"}')})"
    ),
    MetricsViewName.SOURCE_FETCH_TIME: "snapl_dashboard_source_fetch_duration_milliseconds",
    # Spec 015 PR E: the worker's own counters.
    MetricsViewName.SCAN_RESULTS: (
        f"sum by (device, state) (({_rate('snapl_scan_results_total{use_case="dcfabric"}')} * 60))"
    ),
    MetricsViewName.DRIFT_PATHS: (
        f"sum by (device, kind) (({_rate('snapl_drift_paths_total{use_case="dcfabric"}')} * 60))"
    ),
    MetricsViewName.DEPLOY_OUTCOMES: (
        f"sum by (outcome, reason) (({_rate('snapl_deploy_ends_total{use_case="dcfabric"}')} * 60))"
    ),
    # Spec 016 (contracts/telemetry.md): the network.
    MetricsViewName.INTERFACE_TRAFFIC: (
        f"sum by (device, direction) ({_rate('snapl_interface_octets_total{use_case="dcfabric"}')})"
    ),
    MetricsViewName.INTERFACE_ERRORS: (
        f"sum by (device, interface) ({_rate('snapl_interface_errors_total{use_case="dcfabric"}')})"
    ),
    MetricsViewName.INTERFACE_DISCARDS: (
        f"sum by (device, interface) ({_rate('snapl_interface_discards_total{use_case="dcfabric"}')})"
    ),
    MetricsViewName.BGP_ESTABLISHED: 'sum by (device) (snapl_bgp_session_established{use_case="dcfabric"})',
    MetricsViewName.DEVICE_CPU: 'max by (device) (snapl_device_cpu_percent{use_case="dcfabric"})',
    MetricsViewName.DEVICE_MEMORY: 'max by (device) (snapl_device_memory_percent{use_case="dcfabric"})',
}


def test_every_view_has_exactly_the_documented_query() -> None:
    assert set(VIEWS) == set(MetricsViewName) == set(EXPECTED_QUERIES)
    for view, expected in EXPECTED_QUERIES.items():
        assert render_query(view, namespace="default", use_case="dcfabric", window_s=60) == expected


def test_the_rate_window_spans_at_least_four_scrapes_and_one_step() -> None:
    assert "[60s]" in render_query(MetricsViewName.GNMI_ERRORS, namespace="n", use_case="u", window_s=60)
    assert "[300s]" in render_query(MetricsViewName.GNMI_ERRORS, namespace="n", use_case="u", window_s=300)


def test_a_first_observation_is_divided_by_the_window_in_seconds() -> None:
    """A series with no sample one window ago started from zero inside it, so its
    value is its increase over the window (#191)."""
    query = render_query(MetricsViewName.GNMI_ERRORS, namespace="n", use_case="u", window_s=300)

    assert "offset 300s" in query
    assert "/ 300 or rate(" in query
    assert "$" not in query


def test_a_quote_in_a_setting_cannot_break_out_of_the_matcher() -> None:
    query = render_query(MetricsViewName.GNMI_ERRORS, namespace="n", use_case='x"}or vector(1)#', window_s=60)

    assert 'use_case="x\\"}or vector(1)#"' in query


def test_units_and_series_labels_follow_the_data_model() -> None:
    assert {v: spec.unit for v, spec in VIEWS.items()} == {
        MetricsViewName.WORKFLOW_DURATION: "ms",
        MetricsViewName.GNMI_DURATION: "ms",
        MetricsViewName.GNMI_ERRORS: "per_second",
        MetricsViewName.WORKER_LAG: "ms",
        MetricsViewName.WORKER_UP: "up",
        MetricsViewName.SOURCE_HEALTH: "up",
        MetricsViewName.WORKFLOW_MEAN: "ms",
        MetricsViewName.GNMI_MEAN: "ms",
        MetricsViewName.WORKFLOWS_COMPLETED: "count",
        MetricsViewName.ACTIVITY_TIME: "ms",
        MetricsViewName.WORKER_SLOTS: "count",
        MetricsViewName.SERVER_REQUEST_TIME: "ms",
        MetricsViewName.SOURCE_FETCH_TIME: "ms",
        MetricsViewName.SCAN_RESULTS: "count",
        MetricsViewName.DRIFT_PATHS: "count",
        MetricsViewName.DEPLOY_OUTCOMES: "count",
        MetricsViewName.INTERFACE_TRAFFIC: "bytes_per_second",
        MetricsViewName.INTERFACE_ERRORS: "per_second",
        MetricsViewName.INTERFACE_DISCARDS: "per_second",
        MetricsViewName.BGP_ESTABLISHED: "count",
        MetricsViewName.DEVICE_CPU: "percent",
        MetricsViewName.DEVICE_MEMORY: "percent",
    }
    assert {v: spec.labels for v, spec in VIEWS.items()} == {
        MetricsViewName.WORKFLOW_DURATION: ("workflow_type",),
        MetricsViewName.GNMI_DURATION: ("device", "operation"),
        MetricsViewName.GNMI_ERRORS: ("device",),
        MetricsViewName.WORKER_LAG: (),
        MetricsViewName.WORKER_UP: (),
        MetricsViewName.SOURCE_HEALTH: ("source",),
        MetricsViewName.WORKFLOW_MEAN: ("workflow_type",),
        MetricsViewName.GNMI_MEAN: ("device", "operation"),
        MetricsViewName.WORKFLOWS_COMPLETED: ("workflow_type",),
        MetricsViewName.ACTIVITY_TIME: ("activity_type",),
        MetricsViewName.WORKER_SLOTS: ("worker_type",),
        MetricsViewName.SERVER_REQUEST_TIME: (),
        MetricsViewName.SOURCE_FETCH_TIME: ("source",),
        MetricsViewName.SCAN_RESULTS: ("device", "state"),
        MetricsViewName.DRIFT_PATHS: ("device", "kind"),
        MetricsViewName.DEPLOY_OUTCOMES: ("outcome", "reason"),
        MetricsViewName.INTERFACE_TRAFFIC: ("device", "direction"),
        MetricsViewName.INTERFACE_ERRORS: ("device", "interface"),
        MetricsViewName.INTERFACE_DISCARDS: ("device", "interface"),
        MetricsViewName.BGP_ESTABLISHED: ("device",),
        MetricsViewName.DEVICE_CPU: ("device",),
        MetricsViewName.DEVICE_MEMORY: ("device",),
    }


EVENT_VIEWS = {
    MetricsViewName.WORKFLOWS_COMPLETED,
    MetricsViewName.SCAN_RESULTS,
    MetricsViewName.DRIFT_PATHS,
    MetricsViewName.DEPLOY_OUTCOMES,
}


def test_only_the_event_views_count_events_in_a_window() -> None:
    """A gauge's `count` is a current value: worker-slots says nothing about a window (research R4)."""
    assert {v for v, spec in VIEWS.items() if spec.counts_events} == EVENT_VIEWS


def test_a_count_of_events_is_the_first_observation_rate_times_the_window() -> None:
    """increase() is rate() times the window, so a first completion counts too (#191)."""
    query = render_query(MetricsViewName.WORKFLOWS_COMPLETED, namespace="n", use_case="u", window_s=300)

    assert "offset 300s" in query
    assert "/ 300 or rate(" in query
    assert query.endswith(" * 300))")
    assert "$" not in query


# --- the window of a count (research R4) -----------------------------------------


@pytest.mark.parametrize(
    ("step", "expected"),
    [
        (15, "events in the last 60 s"),  # the default hour: each point overlaps three neighbours
        (59, "events in the last 60 s"),
        (60, None),  # the window is the step: a point is the events in its own step
        (360, None),
    ],
)
def test_a_count_says_its_window_when_the_window_is_longer_than_the_step(step: int, expected: str | None) -> None:
    for view in EVENT_VIEWS:
        assert window_note(view, step) == expected, view


def test_views_that_do_not_count_events_have_no_window_note() -> None:
    for view in MetricsViewName:
        if view not in EVENT_VIEWS:
            assert window_note(view, 15) is None, view


def test_the_window_spans_four_scrapes_or_one_step() -> None:
    assert rate_window(15) == 60
    assert rate_window(300) == 300


async def test_only_query_start_end_and_step_reach_prometheus() -> None:
    prom = FakeProm(fixture("range_gnmi_errors"))

    await source(prom).query(MetricsViewName.GNMI_ERRORS, an_hour())

    [params] = prom.range_queries()
    assert set(params) == {"query", "start", "end", "step"}
    assert params["query"] == [EXPECTED_QUERIES[MetricsViewName.GNMI_ERRORS]]
    assert params["step"] == ["15"]
    assert params["start"] == [str(int((T0 - timedelta(hours=1)).timestamp()))]
    assert params["end"] == [str(int(T0.timestamp()))]


# --- step and range (FR-013) -----------------------------------------------------


def _points(start: datetime, end: datetime, step: int) -> int:
    return int((end - start).total_seconds()) // step + 1


@pytest.mark.parametrize(
    ("span", "requested"),
    [
        (timedelta(hours=1), 15),
        (timedelta(days=7), 15),
        (timedelta(days=400), 1),
        (timedelta(seconds=9999), 1),
        (timedelta(seconds=10000), 1),
    ],
)
def test_choose_step_never_yields_more_than_the_point_limit(span: timedelta, requested: int) -> None:
    step = choose_step(T0 - span, T0, requested)

    assert step >= requested
    assert _points(T0 - span, T0, step) <= MAX_POINTS == 10_000


def test_choose_step_keeps_the_requested_step_when_it_fits() -> None:
    assert choose_step(T0 - timedelta(hours=1), T0, 15) == 15
    assert choose_step(T0 - timedelta(days=1), T0, 3600) == 3600


def test_plan_range_rounds_both_ends_down_to_the_step() -> None:
    plan = plan_range(datetime(2026, 11, 8, 19, 0, 7, tzinfo=UTC), datetime(2026, 11, 8, 20, 0, 44, tzinfo=UTC), 15)

    assert plan.start == datetime(2026, 11, 8, 19, 0, 0, tzinfo=UTC)
    assert plan.end == datetime(2026, 11, 8, 20, 0, 30, tzinfo=UTC)
    assert plan.step == 15
    assert plan.note is None


def test_plan_range_says_when_it_raised_the_step() -> None:
    plan = plan_range(T0 - timedelta(days=400), T0, 1)

    assert plan.step > 1
    assert plan.note == f"step raised to {plan.step} s to stay under the point limit"


# --- parsing ---------------------------------------------------------------------


def test_parse_matrix_keeps_only_the_view_labels_and_parses_values() -> None:
    body = fixture("range_source_up")["body"]

    series = parse_matrix(body, ("source",))

    assert [s.labels for s in series] == [{"source": r["metric"]["source"]} for r in body["data"]["result"]]
    first = body["data"]["result"][0]["values"][0]
    assert series[0].points[0].t == datetime.fromtimestamp(first[0], UTC)
    assert series[0].points[0].value == float(first[1])
    assert all(isinstance(p.value, float) for s in series for p in s.points)


def test_parse_matrix_drops_name_job_and_instance_for_an_unlabelled_view() -> None:
    body = fixture("range_source_up")["body"]

    series = parse_matrix(body, ())

    assert all(s.labels == {} for s in series)


def _matrix(results: list[tuple[dict[str, str], list[tuple[int, str]]]]) -> dict[str, Any]:
    """A query_range answer in the captured format (fixtures/prometheus/), built by hand."""
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{"metric": metric, "values": [[ts, raw] for ts, raw in values]} for metric, values in results],
        },
    }


_T = int(T0.timestamp())

# Built by hand with the label sets the live store held on 2026-11-15 (research R2).
HAND_BUILT: dict[MetricsViewName, tuple[list[tuple[dict[str, str], list[tuple[int, str]]]], list[dict[str, str]]]] = {
    MetricsViewName.WORKFLOW_MEAN: (
        [({"workflow_type": "ScanDrift"}, [(_T, "1580.2")]), ({"workflow_type": "DeployIntent"}, [(_T, "NaN")])],
        [{"workflow_type": "ScanDrift"}, {"workflow_type": "DeployIntent"}],
    ),
    MetricsViewName.GNMI_MEAN: (
        [({"device": "leaf-01", "operation": "collect"}, [(_T, "212.5")])],
        [{"device": "leaf-01", "operation": "collect"}],
    ),
    MetricsViewName.WORKFLOWS_COMPLETED: (
        [({"workflow_type": wt}, [(_T, "1.0666666666666667")]) for wt in ("DeployIntent", "ScanDrift")],
        [{"workflow_type": "DeployIntent"}, {"workflow_type": "ScanDrift"}],
    ),
    MetricsViewName.ACTIVITY_TIME: (
        [({"activity_type": "detect_drift"}, [(_T, "41")])],
        [{"activity_type": "detect_drift"}],
    ),
    MetricsViewName.WORKER_SLOTS: (
        [({"worker_type": wt}, [(_T, "0")]) for wt in ("ActivityWorker", "LocalActivityWorker", "WorkflowWorker")],
        [{"worker_type": "ActivityWorker"}, {"worker_type": "LocalActivityWorker"}, {"worker_type": "WorkflowWorker"}],
    ),
    MetricsViewName.SERVER_REQUEST_TIME: ([({}, [(_T, "18.4")])], [{}]),
    MetricsViewName.SOURCE_FETCH_TIME: (
        [
            (
                {
                    "__name__": "snapl_dashboard_source_fetch_duration_milliseconds",
                    "instance": "host.docker.internal:18030",
                    "job": "snapl-dashboard",
                    "source": "audit",
                },
                [(_T, "3.2")],
            )
        ],
        [{"source": "audit"}],
    ),
    # Spec 015 PR E: the label sets the worker writes (contracts/metrics-views.md), with
    # the runtime's own labels, which the sum drops, left out as Prometheus would.
    MetricsViewName.SCAN_RESULTS: (
        [
            ({"device": "leaf-01", "state": "undesired"}, [(_T, "1")]),
            ({"device": "spine-01", "state": "in_sync"}, [(_T, "4")]),
        ],
        [{"device": "leaf-01", "state": "undesired"}, {"device": "spine-01", "state": "in_sync"}],
    ),
    MetricsViewName.DRIFT_PATHS: (
        [({"device": "leaf-01", "kind": "undesired"}, [(_T, "2")])],
        [{"device": "leaf-01", "kind": "undesired"}],
    ),
    MetricsViewName.DEPLOY_OUTCOMES: (
        [
            ({"outcome": "succeeded", "reason": "none"}, [(_T, "1")]),
            ({"outcome": "failed", "reason": "verification_failed"}, [(_T, "NaN")]),
        ],
        [{"outcome": "succeeded", "reason": "none"}, {"outcome": "failed", "reason": "verification_failed"}],
    ),
    # Spec 016: the label sets the worker writes (contracts/telemetry.md), after the sum or max.
    MetricsViewName.INTERFACE_TRAFFIC: (
        [
            ({"device": "leaf-01", "direction": "in"}, [(_T, "1250.5")]),
            ({"device": "leaf-01", "direction": "out"}, [(_T, "980")]),
        ],
        [{"device": "leaf-01", "direction": "in"}, {"device": "leaf-01", "direction": "out"}],
    ),
    MetricsViewName.INTERFACE_ERRORS: (
        [({"device": "leaf-02", "interface": "ethernet-1/49"}, [(_T, "0.2")])],
        [{"device": "leaf-02", "interface": "ethernet-1/49"}],
    ),
    MetricsViewName.INTERFACE_DISCARDS: (
        [({"device": "leaf-02", "interface": "ethernet-1/50"}, [(_T, "0")])],
        [{"device": "leaf-02", "interface": "ethernet-1/50"}],
    ),
    MetricsViewName.BGP_ESTABLISHED: (
        [({"device": "spine-01"}, [(_T, "0")]), ({"device": "leaf-01"}, [(_T, "2")])],
        [{"device": "spine-01"}, {"device": "leaf-01"}],
    ),
    MetricsViewName.DEVICE_CPU: ([({"device": "spine-01"}, [(_T, "4")])], [{"device": "spine-01"}]),
    MetricsViewName.DEVICE_MEMORY: ([({"device": "spine-01"}, [(_T, "94")])], [{"device": "spine-01"}]),
}


def test_every_new_view_has_a_hand_built_matrix() -> None:
    assert set(HAND_BUILT) == set(MetricsViewName) - {
        MetricsViewName.WORKFLOW_DURATION,
        MetricsViewName.GNMI_DURATION,
        MetricsViewName.GNMI_ERRORS,
        MetricsViewName.WORKER_LAG,
        MetricsViewName.WORKER_UP,
        MetricsViewName.SOURCE_HEALTH,
    }


@pytest.mark.parametrize("view", sorted(HAND_BUILT))
async def test_a_new_view_parses_its_matrix_to_series_with_its_labels(view: MetricsViewName) -> None:
    results, labels = HAND_BUILT[view]
    prom = FakeProm({"http_status": 200, "body": _matrix(results)})

    data = await source(prom).query(view, an_hour())

    assert data.view is view
    assert data.unit == VIEWS[view].unit
    assert [s.labels for s in data.series] == labels
    [params] = prom.range_queries()
    assert params["query"] == [EXPECTED_QUERIES[view]]
    for series, (_, values) in zip(data.series, results, strict=True):
        [(ts, raw)] = values
        assert series.points[0].t == datetime.fromtimestamp(ts, UTC)
        assert series.points[0].value == (None if raw == "NaN" else float(raw))


async def test_a_count_over_the_default_hour_carries_its_window() -> None:
    results, _ = HAND_BUILT[MetricsViewName.WORKFLOWS_COMPLETED]
    prom = FakeProm({"http_status": 200, "body": _matrix(results)})

    data = await source(prom).query(MetricsViewName.WORKFLOWS_COMPLETED, an_hour())

    assert data.note == "events in the last 60 s"


async def test_a_count_keeps_every_note_in_order() -> None:
    prom = FakeProm(fixture("range_absent_metric"))

    data = await source(prom).query(MetricsViewName.WORKFLOWS_COMPLETED, plan_range(T0 - timedelta(hours=1), T0, 1))

    assert data.note == "events in the last 60 s; no data in range"

    data = await source(prom).query(MetricsViewName.WORKFLOWS_COMPLETED, plan_range(T0 - timedelta(days=400), T0, 1))
    assert data.note is not None
    assert data.note.startswith("step raised to ")
    assert data.note.endswith("; no data in range")
    assert "events in the last" not in data.note  # the raised step is longer than four scrapes


async def test_bgp_established_has_no_window_note_and_says_a_removed_session_still_counts() -> None:
    """A gauge count (as worker-slots); the runtime's meter cannot drop a series (research R5)."""
    results, _ = HAND_BUILT[MetricsViewName.BGP_ESTABLISHED]
    prom = FakeProm({"http_status": 200, "body": _matrix(results)})

    data = await source(prom).query(MetricsViewName.BGP_ESTABLISHED, an_hour())

    assert not VIEWS[MetricsViewName.BGP_ESTABLISHED].counts_events
    assert data.note == "a session removed from intent keeps counting until the worker restarts"


async def test_a_gauge_count_has_no_window_note() -> None:
    results, _ = HAND_BUILT[MetricsViewName.WORKER_SLOTS]
    prom = FakeProm({"http_status": 200, "body": _matrix(results)})

    data = await source(prom).query(MetricsViewName.WORKER_SLOTS, an_hour())

    assert data.unit == "count"
    assert data.note is None


def _two_finite_points(body: dict[str, Any]) -> tuple[int, int]:
    """The first series and index whose point and the next one are both finite."""
    for s, result in enumerate(body["data"]["result"]):
        values = result["values"]
        for i in range(len(values) - 1):
            if all(math.isfinite(float(v)) for _, v in values[i : i + 2]):
                return s, i
    raise AssertionError("the capture has no two adjacent finite points")


@pytest.mark.parametrize("raw", ["NaN", "+Inf", "-Inf"])
def test_non_finite_values_become_none(raw: str) -> None:
    # Synthesised for each spelling: one finite point is replaced, its neighbour is kept.
    body = copy.deepcopy(fixture("range_gnmi_duration_p95")["body"])
    s, i = _two_finite_points(body)
    body["data"]["result"][s]["values"][i + 1][1] = raw

    series = parse_matrix(body, ("device", "operation"))

    assert series[s].points[i + 1].value is None
    assert series[s].points[i].value is not None
    assert math.isfinite(series[s].points[i].value)


def test_the_captures_own_nan_points_become_none() -> None:
    # The live capture (T043) holds real "NaN": histogram_quantile over a zero rate (#191).
    body = fixture("range_gnmi_duration_p95")["body"]
    raw = [v for result in body["data"]["result"] for _, v in result["values"]]
    assert "NaN" in raw

    points = [p for s in parse_matrix(body, ("device", "operation")) for p in s.points]

    assert sum(p.value is None for p in points) == raw.count("NaN")
    assert all(math.isfinite(p.value) for p in points if p.value is not None)


async def test_a_view_answers_with_series_from_the_capture() -> None:
    prom = FakeProm(fixture("range_gnmi_duration_p95"))

    data = await source(prom).query(MetricsViewName.GNMI_DURATION, an_hour())

    assert data.view is MetricsViewName.GNMI_DURATION
    assert data.unit == "ms"
    assert (data.start, data.end, data.step) == (T0 - timedelta(hours=1), T0, 15)
    assert len(data.series) == 9  # T043's live capture: 6 devices collecting, 3 of them also configured
    assert set(data.series[0].labels) == {"device", "operation"}
    assert data.note is None


# Spec 015's views, captured live on 2026-11-18 (T049): the unit and the series
# labels each must give, as contracts/metrics-views.md lists them.
SPEC_015_CAPTURES = [
    (MetricsViewName.WORKFLOW_MEAN, "range_workflow_mean", "ms", {"workflow_type"}),
    (MetricsViewName.GNMI_MEAN, "range_gnmi_mean", "ms", {"device", "operation"}),
    (MetricsViewName.WORKFLOWS_COMPLETED, "range_workflows_completed", "count", {"workflow_type"}),
    (MetricsViewName.ACTIVITY_TIME, "range_activity_time", "ms", {"activity_type"}),
    (MetricsViewName.WORKER_SLOTS, "range_worker_slots", "count", {"worker_type"}),
    (MetricsViewName.SERVER_REQUEST_TIME, "range_server_request_time", "ms", set()),
    (MetricsViewName.SOURCE_FETCH_TIME, "range_source_fetch_time", "ms", {"source"}),
    (MetricsViewName.SCAN_RESULTS, "range_scan_results", "count", {"device", "state"}),
    (MetricsViewName.DRIFT_PATHS, "range_drift_paths", "count", {"device", "kind"}),
    (MetricsViewName.DEPLOY_OUTCOMES, "range_deploy_outcomes", "count", {"outcome", "reason"}),
]


@pytest.mark.parametrize(("view", "name", "unit", "labels"), SPEC_015_CAPTURES, ids=[c[1] for c in SPEC_015_CAPTURES])
async def test_each_spec_015_view_reads_its_live_capture(view, name, unit, labels) -> None:
    data = await source(FakeProm(fixture(name))).query(view, an_hour())

    assert data.view is view
    assert data.unit == unit
    assert data.series, f"{name} has no series"
    assert all(set(s.labels) == labels for s in data.series)


async def test_the_live_capture_counts_the_drift_the_lab_was_given() -> None:
    """T037 gave leaf-01 one undesired interface, then repaired it; T058 left spine-01 missing a path."""
    paths = await source(FakeProm(fixture("range_drift_paths"))).query(MetricsViewName.DRIFT_PATHS, an_hour())
    scans = await source(FakeProm(fixture("range_scan_results"))).query(MetricsViewName.SCAN_RESULTS, an_hour())
    deploys = await source(FakeProm(fixture("range_deploy_outcomes"))).query(MetricsViewName.DEPLOY_OUTCOMES, an_hour())

    assert {(s.labels["device"], s.labels["kind"]) for s in paths.series} == {
        ("leaf-01", "undesired"),
        ("spine-01", "missing"),
    }
    assert ("leaf-01", "undesired") in {(s.labels["device"], s.labels["state"]) for s in scans.series}
    assert {(s.labels["outcome"], s.labels["reason"]) for s in deploys.series} == {("succeeded", "none")}


async def test_no_data_in_range_is_a_note_not_an_error() -> None:
    prom = FakeProm(fixture("range_absent_metric"))

    data = await source(prom).query(MetricsViewName.WORKER_LAG, an_hour())

    assert data.series == []
    assert data.note == "no data in range"


async def test_both_notes_are_kept() -> None:
    prom = FakeProm(fixture("range_absent_metric"))

    data = await source(prom).query(MetricsViewName.WORKER_LAG, plan_range(T0 - timedelta(days=400), T0, 1))

    assert data.note is not None
    assert data.note.startswith("step raised to ")
    assert data.note.endswith("; no data in range")


# --- failures (FR-012) -----------------------------------------------------------


@pytest.mark.parametrize("name", ["error_bad_query", "error_too_many_points"])
async def test_a_prometheus_error_is_unavailable_with_its_error_text(name: str) -> None:
    captured = fixture(name)
    prom = FakeProm(captured)

    with pytest.raises(SourceUnavailable) as caught:
        await source(prom).query(MetricsViewName.GNMI_DURATION, an_hour())

    assert caught.value.reason == captured["body"]["error"]
    assert caught.value.remedy


async def test_a_refused_connection_is_unavailable_with_the_dev_deps_remedy() -> None:
    prom = FakeProm(httpx.ConnectError("connection refused"))

    with pytest.raises(SourceUnavailable) as caught:
        await source(prom).query(MetricsViewName.GNMI_DURATION, an_hour())

    assert ADDRESS in caught.value.reason
    assert caught.value.remedy == "uv run invoke dev.deps"


async def test_an_answer_that_is_not_prometheus_json_is_unavailable() -> None:
    prom = FakeProm({"http_status": 502, "body_text": "<html>bad gateway</html>"})

    with pytest.raises(SourceUnavailable) as caught:
        await source(prom).query(MetricsViewName.GNMI_DURATION, an_hour())

    assert "502" in caught.value.reason


# --- the cache (R7) --------------------------------------------------------------


async def test_the_same_range_within_15_s_costs_one_request() -> None:
    prom = FakeProm(fixture("range_gnmi_errors"))
    clock = Clock()
    prometheus = source(prom, clock)

    first = await prometheus.query(MetricsViewName.GNMI_ERRORS, an_hour())
    clock.now += timedelta(seconds=14)
    second = await prometheus.query(MetricsViewName.GNMI_ERRORS, an_hour())

    assert len(prom.range_queries()) == 1
    assert second == first

    clock.now += timedelta(seconds=2)
    await prometheus.query(MetricsViewName.GNMI_ERRORS, an_hour())
    assert len(prom.range_queries()) == 2


async def test_another_view_or_range_is_its_own_entry() -> None:
    prom = FakeProm(fixture("range_gnmi_errors"))
    prometheus = source(prom)

    await prometheus.query(MetricsViewName.GNMI_ERRORS, an_hour())
    await prometheus.query(MetricsViewName.GNMI_DURATION, an_hour())
    await prometheus.query(MetricsViewName.GNMI_ERRORS, plan_range(T0 - timedelta(hours=2), T0, 15))

    assert len(prom.range_queries()) == 3


async def test_a_failure_is_not_cached() -> None:
    prom = FakeProm(httpx.ConnectError("connection refused"))
    prometheus = source(prom)
    with pytest.raises(SourceUnavailable):
        await prometheus.query(MetricsViewName.GNMI_ERRORS, an_hour())

    prom.query_range = fixture("range_gnmi_errors")
    data = await prometheus.query(MetricsViewName.GNMI_ERRORS, an_hour())

    assert len(data.series) == 1  # T043's live capture: leaf-04, paused for four scans


# --- /sources probe (010's source protocol) --------------------------------------


async def test_refresh_probes_ready_and_reports_available() -> None:
    prom = FakeProm(fixture("range_gnmi_errors"))

    view = await source(prom).refresh()

    assert [r.url.path for r in prom.requests] == ["/-/ready"]
    assert view.source is SourceName.PROMETHEUS
    assert view.available is True
    assert view.fetched_at == T0
    assert view.data is None


async def test_refresh_reports_an_unreachable_prometheus_with_the_remedy() -> None:
    prom = FakeProm(fixture("range_gnmi_errors"), ready=httpx.ConnectError("connection refused"))

    view = await source(prom).refresh()

    assert view.available is False
    assert view.reason is not None
    assert ADDRESS in view.reason
    assert view.remedy == "uv run invoke dev.deps"


async def test_refresh_reports_a_prometheus_that_is_not_ready() -> None:
    prom = FakeProm(
        fixture("range_gnmi_errors"),
        ready={"http_status": 503, "body_text": "Service Unavailable"},
    )

    view = await source(prom).refresh()

    assert view.available is False
    assert view.reason is not None
    assert "503" in view.reason


# --- the network's instant figures (spec 016 T038) ----------------------------------


def _vector(results: list[tuple[dict[str, str], str]]) -> dict[str, Any]:
    return {
        "status": "success",
        "data": {"resultType": "vector", "result": [{"metric": m, "value": [_T, raw]} for m, raw in results]},
    }


class FakeInstant:
    """Answers each instant query with the vector named for its family, recording the requests."""

    def __init__(self, by_metric: dict[str, Any] | Exception) -> None:
        self.by_metric = by_metric
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.by_metric, Exception):
            raise self.by_metric
        query = parse_qs(request.url.query.decode())["query"][0]
        for metric, answer in self.by_metric.items():
            if metric in query:
                return httpx.Response(200, json=answer)
        return httpx.Response(200, json=_vector([]))


def _instant_source(fake: FakeInstant, clock: Clock | None = None) -> PrometheusSource:
    return PrometheusSource(ADDRESS, "default", "dcfabric", transport=httpx.MockTransport(fake), clock=clock or Clock())


async def test_the_network_asks_one_instant_query_per_family_at_now() -> None:
    from snapl_presentation.dashboard.prometheus import NETWORK_QUERIES

    fake = FakeInstant({})
    await _instant_source(fake).network()

    assert {r.url.path for r in fake.requests} == {"/api/v1/query"}
    params = [parse_qs(r.url.query.decode()) for r in fake.requests]
    assert [p["time"] for p in params] == [[str(T0.timestamp())]] * len(NETWORK_QUERIES)
    queries = [p["query"][0] for p in params]
    assert queries[0] == 'max by (device, interface) (snapl_interface_up{use_case="dcfabric"})'
    assert queries[1] == (
        f"sum by (device, interface, direction) ({_rate('snapl_interface_octets_total{use_case="dcfabric"}')})"
    )
    assert queries[-2:] == [
        'max by (device) (snapl_device_cpu_percent{use_case="dcfabric"})',
        'max by (device) (snapl_device_memory_percent{use_case="dcfabric"})',
    ]
    assert all("$" not in q for q in queries)


async def test_the_network_keys_each_figure_by_its_labels() -> None:
    fake = FakeInstant(
        {
            "snapl_interface_octets_total": _vector(
                [
                    ({"device": "leaf-01", "interface": "ethernet-1/49", "direction": "in"}, "1250.5"),
                    ({"device": "leaf-01", "interface": "ethernet-1/49"}, "1"),  # no direction: dropped
                ]
            ),
            "snapl_device_cpu_percent": _vector([({"device": "leaf-01"}, "NaN")]),
            "snapl_bgp_session_established": _vector([({"device": "spine-01", "peer": "10.10.1.1"}, "0")]),
        }
    )

    sample = await _instant_source(fake).network()

    assert sample.at == T0
    assert sample.octets == {("leaf-01", "ethernet-1/49", "in"): 1250.5}
    assert sample.cpu == {("leaf-01",): None}
    assert sample.established == {("spine-01", "10.10.1.1"): 0.0}
    assert sample.memory == {}


async def test_the_network_is_read_once_per_scrape_interval() -> None:
    fake = FakeInstant({})
    clock = Clock()
    source_ = _instant_source(fake, clock)

    await source_.network()
    clock.now = T0 + timedelta(seconds=14)
    await source_.network()
    first = len(fake.requests)
    clock.now = T0 + timedelta(seconds=15)
    await source_.network()

    assert len(fake.requests) == 2 * first


async def test_the_network_with_prometheus_down_is_unavailable() -> None:
    fake = FakeInstant(httpx.ConnectError("refused"))

    with pytest.raises(SourceUnavailable, match=r"Prometheus not reachable at http://prom\.test:9090: refused"):
        await _instant_source(fake).network()


async def test_the_network_with_a_query_error_says_prometheus_s_error() -> None:
    error = {"status": "error", "errorType": "bad_data", "error": "parse error"}
    fake = FakeInstant({"snapl_interface_up": error})

    with pytest.raises(SourceUnavailable, match="parse error"):
        await _instant_source(fake).network()
