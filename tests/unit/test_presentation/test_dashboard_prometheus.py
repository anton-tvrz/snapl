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
    render_query,
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

EXPECTED_QUERIES = {
    MetricsViewName.WORKFLOW_DURATION: (
        "histogram_quantile(0.95, sum by (le, workflow_type) "
        '(rate(temporal_workflow_endtoend_latency_bucket{namespace="default"}[60s])))'
    ),
    MetricsViewName.GNMI_DURATION: (
        "histogram_quantile(0.95, sum by (le, device, operation) "
        '(rate(snapl_gnmi_call_duration_bucket{use_case="dcfabric"}[60s])))'
    ),
    MetricsViewName.GNMI_ERRORS: (
        'sum by (device) (rate(snapl_gnmi_call_duration_count{use_case="dcfabric",outcome="error"}[60s]))'
    ),
    MetricsViewName.WORKER_LAG: (
        "histogram_quantile(0.95, sum by (le) "
        '(rate(temporal_activity_schedule_to_start_latency_bucket{namespace="default"}[60s])))'
    ),
    MetricsViewName.WORKER_UP: 'up{job="snapl-worker"}',
    MetricsViewName.SOURCE_HEALTH: "snapl_dashboard_source_up",
}


def test_every_view_has_exactly_the_documented_query() -> None:
    assert set(VIEWS) == set(MetricsViewName)
    for view, expected in EXPECTED_QUERIES.items():
        assert render_query(view, namespace="default", use_case="dcfabric", window_s=60) == expected


def test_the_rate_window_spans_at_least_four_scrapes_and_one_step() -> None:
    assert "[60s]" in render_query(MetricsViewName.GNMI_ERRORS, namespace="n", use_case="u", window_s=60)
    assert "[300s]" in render_query(MetricsViewName.GNMI_ERRORS, namespace="n", use_case="u", window_s=300)


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
    }
    assert {v: spec.labels for v, spec in VIEWS.items()} == {
        MetricsViewName.WORKFLOW_DURATION: ("workflow_type",),
        MetricsViewName.GNMI_DURATION: ("device", "operation"),
        MetricsViewName.GNMI_ERRORS: ("device",),
        MetricsViewName.WORKER_LAG: (),
        MetricsViewName.WORKER_UP: (),
        MetricsViewName.SOURCE_HEALTH: ("source",),
    }


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


@pytest.mark.parametrize("raw", ["NaN", "+Inf", "-Inf"])
def test_non_finite_values_become_none(raw: str) -> None:
    # Synthesised: the capture holds only finite values, so one is replaced.
    body = copy.deepcopy(fixture("range_gnmi_duration_p95")["body"])
    body["data"]["result"][0]["values"][1][1] = raw

    [first, *_] = parse_matrix(body, ("device", "operation"))

    assert first.points[1].value is None
    assert first.points[0].value is not None
    assert math.isfinite(first.points[0].value)


async def test_a_view_answers_with_series_from_the_capture() -> None:
    prom = FakeProm(fixture("range_gnmi_duration_p95"))

    data = await source(prom).query(MetricsViewName.GNMI_DURATION, an_hour())

    assert data.view is MetricsViewName.GNMI_DURATION
    assert data.unit == "ms"
    assert (data.start, data.end, data.step) == (T0 - timedelta(hours=1), T0, 15)
    assert len(data.series) == 6
    assert set(data.series[0].labels) == {"device", "operation"}
    assert data.note is None


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

    assert len(data.series) == 3


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
