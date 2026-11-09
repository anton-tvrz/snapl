"""The metrics store as a dashboard source (spec 012, research R7).

Requests choose a named view, never a query: ``VIEWS`` is the only place PromQL
lives, and only the server's namespace and use case are put into it (FR-011).
The range and step come from the request after validation. The step is raised
when needed so that no series asks Prometheus for more points than it allows
(FR-013).

``refresh()`` is 010's source protocol: it probes ``/-/ready`` for
``/api/v1/sources``. The metrics routes call ``query()``, which caches each view
and range for one scrape interval, so polling never asks faster than data
arrives.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from snapl_presentation.dashboard.models import (
    MetricPoint,
    MetricsData,
    MetricSeries,
    MetricsViewName,
    MetricUnit,
    SourceName,
    View,
)
from snapl_presentation.dashboard.sources import STACK_REMEDY, SourceUnavailable, utcnow
from snapl_presentation.exceptions import first_line

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "MAX_POINTS",
    "SCRAPE_INTERVAL_S",
    "VIEWS",
    "MetricsViewSpec",
    "PrometheusSource",
    "RangePlan",
    "choose_step",
    "parse_matrix",
    "plan_range",
    "render_query",
]

# development/prometheus/prometheus.yml scrapes every 15 s.
SCRAPE_INTERVAL_S = 15
# Prometheus refuses more than 11,000 points per series (captured). Stay under.
MAX_POINTS = 10_000


@dataclass(frozen=True)
class MetricsViewSpec:
    # `$ns`, `$uc` and `$w` are filled by render_query, and nothing else is.
    query: str
    unit: MetricUnit
    labels: tuple[str, ...]


VIEWS: dict[MetricsViewName, MetricsViewSpec] = {
    MetricsViewName.WORKFLOW_DURATION: MetricsViewSpec(
        "histogram_quantile(0.95, sum by (le, workflow_type) "
        '(rate(temporal_workflow_endtoend_latency_bucket{namespace="$ns"}[$w])))',
        "ms",
        ("workflow_type",),
    ),
    MetricsViewName.GNMI_DURATION: MetricsViewSpec(
        "histogram_quantile(0.95, sum by (le, device, operation) "
        '(rate(snapl_gnmi_call_duration_bucket{use_case="$uc"}[$w])))',
        "ms",
        ("device", "operation"),
    ),
    MetricsViewName.GNMI_ERRORS: MetricsViewSpec(
        'sum by (device) (rate(snapl_gnmi_call_duration_count{use_case="$uc",outcome="error"}[$w]))',
        "per_second",
        ("device",),
    ),
    MetricsViewName.WORKER_LAG: MetricsViewSpec(
        "histogram_quantile(0.95, sum by (le) "
        '(rate(temporal_activity_schedule_to_start_latency_bucket{namespace="$ns"}[$w])))',
        "ms",
        (),
    ),
    MetricsViewName.WORKER_UP: MetricsViewSpec('up{job="snapl-worker"}', "up", ()),
    MetricsViewName.SOURCE_HEALTH: MetricsViewSpec("snapl_dashboard_source_up", "up", ("source",)),
}


def _label_value(value: str) -> str:
    """Escape a value for a double-quoted PromQL label matcher."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_query(view: MetricsViewName, *, namespace: str, use_case: str, window_s: int) -> str:
    spec = VIEWS[view]
    return (
        spec.query.replace("$ns", _label_value(namespace))
        .replace("$uc", _label_value(use_case))
        .replace("$w", f"{window_s}s")
    )


def choose_step(start: datetime, end: datetime, requested: int) -> int:
    """The requested step, or the smallest coarser one that keeps a series under ``MAX_POINTS``."""
    span = (end - start).total_seconds()
    return max(requested, math.ceil(span / (MAX_POINTS - 1)), 1)


@dataclass(frozen=True)
class RangePlan:
    """A range as it is asked for: both ends rounded down to the step.

    Rounding makes nearby requests share one cache entry, and the step used is
    reported back with a note when it is coarser than requested (FR-013).
    """

    start: datetime
    end: datetime
    step: int
    note: str | None = None


def _floor(moment: datetime, step: int) -> datetime:
    seconds = int(moment.timestamp())
    return datetime.fromtimestamp(seconds - seconds % step, UTC)


def plan_range(start: datetime, end: datetime, requested_step: int) -> RangePlan:
    step = choose_step(start, end, requested_step)
    note = f"step raised to {step} s to stay under the point limit" if step > requested_step else None
    return RangePlan(start=_floor(start, step), end=_floor(end, step), step=step, note=note)


def _value(raw: str) -> float | None:
    value = float(raw)
    return value if math.isfinite(value) else None


def parse_matrix(body: dict[str, Any], labels: tuple[str, ...]) -> list[MetricSeries]:
    """A ``query_range`` answer as series carrying only the view's labels."""
    return [
        MetricSeries(
            labels={key: result["metric"][key] for key in labels if key in result["metric"]},
            points=[MetricPoint(t=datetime.fromtimestamp(ts, UTC), value=_value(raw)) for ts, raw in result["values"]],
        )
        for result in body["data"]["result"]
    ]


class PrometheusSource:
    """Prometheus at ``address``, read for one Temporal namespace and one use case."""

    def __init__(
        self,
        address: str,
        namespace: str,
        use_case: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = utcnow,
        ttl: timedelta = timedelta(seconds=SCRAPE_INTERVAL_S),
    ) -> None:
        self._address = address.rstrip("/")
        self._namespace = namespace
        self._use_case = use_case
        self._timeout = timeout
        self._transport = transport
        self._clock = clock
        self._ttl = ttl
        self._cache: dict[tuple[MetricsViewName, RangePlan], tuple[datetime, MetricsData]] = {}
        self._error_remedy = f"Run the view's query in Prometheus's own UI, {self._address}/query, to see more."

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._address, timeout=self._timeout, transport=self._transport)

    def _unreachable(self, exc: Exception) -> SourceUnavailable:
        detail = first_line(str(exc)) or type(exc).__name__
        return SourceUnavailable(f"Prometheus not reachable at {self._address}: {detail}", STACK_REMEDY)

    async def refresh(self) -> View[None]:
        now = self._clock()
        try:
            async with self._client() as client:
                response = await client.get("/-/ready")
        except httpx.HTTPError as exc:
            failure = self._unreachable(exc)
            return View(source=SourceName.PROMETHEUS, available=False, reason=failure.reason, remedy=failure.remedy)
        if response.status_code != httpx.codes.OK:
            return View(
                source=SourceName.PROMETHEUS,
                available=False,
                reason=f"Prometheus at {self._address} is not ready (HTTP {response.status_code})",
                remedy=STACK_REMEDY,
            )
        return View(source=SourceName.PROMETHEUS, available=True, fetched_at=now)

    async def query(self, view: MetricsViewName, plan: RangePlan) -> MetricsData:
        """One view over ``plan``'s range.

        Raises:
            SourceUnavailable: Prometheus could not be reached, or answered
                with an error, whose text becomes the reason.
        """
        now = self._clock()
        self._cache = {key: entry for key, entry in self._cache.items() if now - entry[0] < self._ttl}
        cached = self._cache.get((view, plan))
        if cached is not None:
            return cached[1]

        spec = VIEWS[view]
        window = max(4 * SCRAPE_INTERVAL_S, plan.step)
        params = {
            "query": render_query(view, namespace=self._namespace, use_case=self._use_case, window_s=window),
            "start": str(int(plan.start.timestamp())),
            "end": str(int(plan.end.timestamp())),
            "step": str(plan.step),
        }
        try:
            async with self._client() as client:
                response = await client.get("/api/v1/query_range", params=params)
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        body = self._body(response)

        series = parse_matrix(body, spec.labels)
        notes = [note for note in (plan.note, None if series else "no data in range") if note]
        data = MetricsData(
            view=view,
            unit=spec.unit,
            start=plan.start,
            end=plan.end,
            step=plan.step,
            series=series,
            note="; ".join(notes) or None,
        )
        self._cache[(view, plan)] = (now, data)
        return data

    def _body(self, response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and body.get("status") == "error" and body.get("error"):
            raise SourceUnavailable(str(body["error"]), self._error_remedy)
        if (
            response.status_code != httpx.codes.OK
            or not isinstance(body, dict)
            or body.get("status") != "success"
            or body.get("data", {}).get("resultType") != "matrix"
        ):
            raise SourceUnavailable(
                f"Prometheus at {self._address} gave an unexpected answer (HTTP {response.status_code})",
                self._error_remedy,
            )
        return body
