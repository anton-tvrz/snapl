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

import asyncio
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from snapl_presentation.dashboard.models import (
    DeviceNetwork,
    FabricNetwork,
    InterfaceState,
    MetricPoint,
    MetricsData,
    MetricSeries,
    MetricsViewName,
    MetricUnit,
    SessionState,
    SourceName,
    View,
)
from snapl_presentation.dashboard.projections import tier_key
from snapl_presentation.dashboard.sources import STACK_REMEDY, SourceUnavailable, utcnow
from snapl_presentation.exceptions import first_line

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from uuid import UUID

    from snapl_intent.models import DesiredState

__all__ = [
    "MAX_POINTS",
    "NETWORK_QUERIES",
    "SCRAPE_INTERVAL_S",
    "VIEWS",
    "WORKER_SCRAPE_INTERVAL_S",
    "MetricsViewSpec",
    "NetworkSample",
    "PrometheusSource",
    "RangePlan",
    "build_network",
    "choose_step",
    "parse_matrix",
    "plan_range",
    "rate_window",
    "render_query",
]

# development/prometheus/prometheus.yml scrapes every 15 s.
SCRAPE_INTERVAL_S = 15
# ...except the worker, every 5 s, so a downed link or session reaches the network
# view within 30 s: sample (10 s) + scrape + this cache + the page's refresh (#348).
WORKER_SCRAPE_INTERVAL_S = 5
# Prometheus refuses more than 11,000 points per series (captured). Stay under.
MAX_POINTS = 10_000


def _rate(selector: str) -> str:
    """``rate(selector[$w])`` that also counts a series' first observation (#191).

    A series is created by its first observation, so ``rate()`` has no earlier
    sample to compare with and sees no increase: a device's first gNMI error
    never showed. A series with no sample one window ago started from zero
    inside the window, so its value is its increase, and dividing by the
    window's seconds gives the rate. That only holds if Prometheus was scraping
    the worker then, which ``up ... offset $w`` checks. Without it, a counter
    that grew while Prometheus was down would show as one burst. In the first
    window after Prometheus starts, a first observation is still missed.
    """
    return (
        f'((({selector} unless {selector} offset $w) and on() (up{{job="snapl-worker"}} offset $w))'
        f" / $sec or rate({selector}[$w]))"
    )


def _step_increase(selector: str) -> str:
    """The counter's increase over the step ending at each point (#308).

    The difference with the latest sample at or before one step ago. Consecutive
    points telescope, so each is a whole number and they add up to the range's
    events. No range selector, so nothing is extrapolated: ``increase()`` over a
    window turned one scan into 5.33 at a 15 s step. A reset (the worker
    restarted) is counted from zero, as the value since it, instead of going
    negative. A first observation is the whole value, under #191's guard that
    Prometheus was scraping the worker one step ago. The three terms never
    overlap.
    """
    return (
        f"((({selector} - {selector} offset $step) >= 0)"
        f" or ({selector} < {selector} offset $step)"
        f" or (({selector} unless {selector} offset $step)"
        f' and on() (up{{job="snapl-worker"}} offset $step)))'
    )


def _mean(sum_selector: str, count_selector: str, by: str) -> str:
    """The mean of a histogram over the window: exact with any buckets (spec 015 research R3)."""
    return f"{by}({_rate(sum_selector)}) / {by}({_rate(count_selector)})"


@dataclass(frozen=True)
class MetricsViewSpec:
    # `$ns`, `$uc`, `$w`, `$sec` and `$step` are filled by render_query, and nothing else is.
    query: str
    unit: MetricUnit
    labels: tuple[str, ...]
    # True for a count of events in each step, as opposed to a gauge's current count (research R4, #308).
    counts_events: bool = False
    # Said with every answer of the view.
    note: str | None = None


VIEWS: dict[MetricsViewName, MetricsViewSpec] = {
    MetricsViewName.WORKFLOW_DURATION: MetricsViewSpec(
        "histogram_quantile(0.95, sum by (le, workflow_type) "
        f"({_rate('temporal_workflow_endtoend_latency_bucket{namespace="$ns"}')}))",
        "ms",
        ("workflow_type",),
    ),
    MetricsViewName.GNMI_DURATION: MetricsViewSpec(
        "histogram_quantile(0.95, sum by (le, device, operation) "
        f"({_rate('snapl_gnmi_call_duration_bucket{use_case="$uc"}')}))",
        "ms",
        ("device", "operation"),
    ),
    MetricsViewName.GNMI_ERRORS: MetricsViewSpec(
        f"sum by (device) ({_rate('snapl_gnmi_call_duration_count{use_case="$uc",outcome="error"}')})",
        "per_second",
        ("device",),
    ),
    MetricsViewName.WORKER_LAG: MetricsViewSpec(
        "histogram_quantile(0.95, sum by (le) "
        f"({_rate('temporal_activity_schedule_to_start_latency_bucket{namespace="$ns"}')}))",
        "ms",
        (),
    ),
    MetricsViewName.WORKER_UP: MetricsViewSpec('up{job="snapl-worker"}', "up", ()),
    MetricsViewName.SOURCE_HEALTH: MetricsViewSpec("snapl_dashboard_source_up", "up", ("source",)),
    # Spec 015 (contracts/metrics-views.md, research R2 to R4).
    MetricsViewName.WORKFLOW_MEAN: MetricsViewSpec(
        _mean(
            'temporal_workflow_endtoend_latency_sum{namespace="$ns"}',
            'temporal_workflow_endtoend_latency_count{namespace="$ns"}',
            "sum by (workflow_type) ",
        ),
        "ms",
        ("workflow_type",),
    ),
    MetricsViewName.GNMI_MEAN: MetricsViewSpec(
        _mean(
            'snapl_gnmi_call_duration_sum{use_case="$uc"}',
            'snapl_gnmi_call_duration_count{use_case="$uc"}',
            "sum by (device, operation) ",
        ),
        "ms",
        ("device", "operation"),
    ),
    MetricsViewName.WORKFLOWS_COMPLETED: MetricsViewSpec(
        f"sum by (workflow_type) ({_step_increase('temporal_workflow_completed{namespace="$ns"}')})",
        "count",
        ("workflow_type",),
        counts_events=True,
    ),
    # Summed over workflow_type: six activity types fit the palette, twelve pairs would not (R2).
    MetricsViewName.ACTIVITY_TIME: MetricsViewSpec(
        _mean(
            'temporal_activity_execution_latency_sum{namespace="$ns"}',
            'temporal_activity_execution_latency_count{namespace="$ns"}',
            "sum by (activity_type) ",
        ),
        "ms",
        ("activity_type",),
    ),
    MetricsViewName.WORKER_SLOTS: MetricsViewSpec(
        'sum by (worker_type) (temporal_worker_task_slots_used{namespace="$ns"})', "count", ("worker_type",)
    ),
    # Summed over operation: the SDK's own operations grow with its version (R2).
    MetricsViewName.SERVER_REQUEST_TIME: MetricsViewSpec(
        _mean(
            'temporal_request_latency_sum{namespace="$ns"}',
            'temporal_request_latency_count{namespace="$ns"}',
            "sum",
        ),
        "ms",
        (),
    ),
    MetricsViewName.SOURCE_FETCH_TIME: MetricsViewSpec(
        "snapl_dashboard_source_fetch_duration_milliseconds", "ms", ("source",)
    ),
    # Spec 015 PR E: the worker's counters (FR-010 to FR-012), summed over the runtime's labels.
    MetricsViewName.SCAN_RESULTS: MetricsViewSpec(
        f"sum by (device, state) ({_step_increase('snapl_scan_results_total{use_case="$uc"}')})",
        "count",
        ("device", "state"),
        counts_events=True,
    ),
    MetricsViewName.DRIFT_PATHS: MetricsViewSpec(
        f"sum by (device, kind) ({_step_increase('snapl_drift_paths_total{use_case="$uc"}')})",
        "count",
        ("device", "kind"),
        counts_events=True,
    ),
    MetricsViewName.DEPLOY_OUTCOMES: MetricsViewSpec(
        f"sum by (outcome, reason) ({_step_increase('snapl_deploy_ends_total{use_case="$uc"}')})",
        "count",
        ("outcome", "reason"),
        counts_events=True,
    ),
    # Spec 016 (contracts/telemetry.md): the network, summed or maxed over the runtime's label.
    # One series per device, both directions summed: the drawer has each interface's in and out (#312).
    MetricsViewName.INTERFACE_TRAFFIC: MetricsViewSpec(
        f"sum by (device) ({_rate('snapl_interface_octets_total{use_case="$uc"}')})",
        "bytes_per_second",
        ("device",),
    ),
    MetricsViewName.INTERFACE_ERRORS: MetricsViewSpec(
        f"sum by (device, interface) ({_rate('snapl_interface_errors_total{use_case="$uc"}')})",
        "per_second",
        ("device", "interface"),
    ),
    MetricsViewName.INTERFACE_DISCARDS: MetricsViewSpec(
        f"sum by (device, interface) ({_rate('snapl_interface_discards_total{use_case="$uc"}')})",
        "per_second",
        ("device", "interface"),
    ),
    # The runtime's meter cannot drop a series (016 research R5).
    MetricsViewName.BGP_ESTABLISHED: MetricsViewSpec(
        'sum by (device) (snapl_bgp_session_established{use_case="$uc"})',
        "count",
        ("device",),
        note="a session removed from intent keeps counting until the worker restarts",
    ),
    MetricsViewName.DEVICE_CPU: MetricsViewSpec(
        'max by (device) (snapl_device_cpu_percent{use_case="$uc"})', "percent", ("device",)
    ),
    MetricsViewName.DEVICE_MEMORY: MetricsViewSpec(
        'max by (device) (snapl_device_memory_percent{use_case="$uc"})', "percent", ("device",)
    ),
}


# Spec 016 (contracts/telemetry.md): the instant figures /network reads, one query
# per family, each reduced to the labels intent names (the runtime adds `service_name`).
NETWORK_QUERIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "interface_up": ('max by (device, interface) (snapl_interface_up{use_case="$uc"})', ("device", "interface")),
    "octets": (
        f"sum by (device, interface, direction) ({_rate('snapl_interface_octets_total{use_case="$uc"}')})",
        ("device", "interface", "direction"),
    ),
    "errors": (
        f"sum by (device, interface, direction) ({_rate('snapl_interface_errors_total{use_case="$uc"}')})",
        ("device", "interface", "direction"),
    ),
    "discards": (
        f"sum by (device, interface, direction) ({_rate('snapl_interface_discards_total{use_case="$uc"}')})",
        ("device", "interface", "direction"),
    ),
    "established": ('max by (device, peer) (snapl_bgp_session_established{use_case="$uc"})', ("device", "peer")),
    "prefixes": ('max by (device, peer) (snapl_bgp_prefixes_received{use_case="$uc"})', ("device", "peer")),
    "cpu": ('max by (device) (snapl_device_cpu_percent{use_case="$uc"})', ("device",)),
    "memory": ('max by (device) (snapl_device_memory_percent{use_case="$uc"})', ("device",)),
}

# Keyed by the query's labels in order: (device, interface[, direction]), (device, peer) or (device,).
Figures = dict[tuple[str, ...], float | None]


@dataclass(frozen=True)
class NetworkSample:
    """One read of every network family at ``at``. A key with no series is absent."""

    at: datetime
    interface_up: Figures = field(default_factory=dict)
    octets: Figures = field(default_factory=dict)
    errors: Figures = field(default_factory=dict)
    discards: Figures = field(default_factory=dict)
    established: Figures = field(default_factory=dict)
    prefixes: Figures = field(default_factory=dict)
    cpu: Figures = field(default_factory=dict)
    memory: Figures = field(default_factory=dict)


def _flag(value: float | None) -> bool | None:
    return None if value is None else value >= 1


def _interfaces(device: str, intended: Mapping[str, bool], sample: NetworkSample) -> list[InterfaceState]:
    def per_second(figures: Figures, name: str, direction: str) -> float | None:
        return figures.get((device, name, direction))

    return [
        InterfaceState(
            name=name,
            expected="up" if enabled else "down",
            up=_flag(sample.interface_up.get((device, name))),
            in_bps=per_second(sample.octets, name, "in"),
            out_bps=per_second(sample.octets, name, "out"),
            in_errors_ps=per_second(sample.errors, name, "in"),
            out_errors_ps=per_second(sample.errors, name, "out"),
            in_discards_ps=per_second(sample.discards, name, "in"),
            out_discards_ps=per_second(sample.discards, name, "out"),
        )
        for name, enabled in sorted(intended.items())
    ]


def _sessions(device: str, intended: Mapping[str, bool], sample: NetworkSample) -> list[SessionState]:
    states = []
    for peer, enabled in sorted(intended.items()):
        prefixes = sample.prefixes.get((device, peer))
        states.append(
            SessionState(
                peer=peer,
                expected="established" if enabled else None,
                established=_flag(sample.established.get((device, peer))),
                prefixes_received=None if prefixes is None else round(prefixes),
            )
        )
    return states


def build_network(
    states: Iterable[DesiredState] | None, names: Mapping[UUID, str], sample: NetworkSample
) -> FabricNetwork:
    """Intent's interfaces and sessions with their latest figures (contracts/telemetry.md).

    Items come from intent: what the store holds of anything else is not shown.
    With ``states`` None (the SoT down), the devices are those it last named,
    with no items. A device in two services is merged into one, as the worker does.
    """
    devices: dict[UUID, tuple[str, str | None]] = {}
    interfaces: dict[UUID, dict[str, bool]] = {}
    sessions: dict[UUID, dict[str, bool]] = {}
    if states is None:
        devices = {device_id: (name, None) for device_id, name in names.items()}
    for state in states or ():
        device_id = state.device.id
        devices.setdefault(device_id, (state.device.name, state.device.role))
        named = interfaces.setdefault(device_id, {})
        for interface in state.interfaces:
            named[interface.name] = named.get(interface.name, False) or interface.enabled
        peers = sessions.setdefault(device_id, {})
        for session in state.bgp_sessions:
            peers[session.peer_address] = peers.get(session.peer_address, False) or session.enabled
    ordered = sorted(devices.items(), key=lambda item: tier_key(item[1][1], item[1][0]))
    return FabricNetwork(
        at=sample.at,
        devices=[
            DeviceNetwork(
                device_id=device_id,
                name=name,
                interfaces=_interfaces(name, interfaces.get(device_id, {}), sample),
                sessions=_sessions(name, sessions.get(device_id, {}), sample),
                cpu_percent=sample.cpu.get((name,)),
                memory_percent=sample.memory.get((name,)),
            )
            for device_id, (name, _) in ordered
        ],
    )


def parse_vector(body: dict[str, Any], labels: tuple[str, ...]) -> Figures:
    """A ``query`` answer keyed by the query's labels. A result missing one is dropped."""
    return {
        tuple(result["metric"][key] for key in labels): _value(result["value"][1])
        for result in body["data"]["result"]
        if all(key in result["metric"] for key in labels)
    }


def _label_value(value: str) -> str:
    """Escape a value for a double-quoted PromQL label matcher."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_query(view: MetricsViewName, *, namespace: str, use_case: str, window_s: int, step_s: int) -> str:
    return _render(VIEWS[view].query, namespace=namespace, use_case=use_case, window_s=window_s, step_s=step_s)


def _render(query: str, *, namespace: str, use_case: str, window_s: int, step_s: int) -> str:
    return (
        query.replace("$ns", _label_value(namespace))
        .replace("$uc", _label_value(use_case))
        .replace("$step", f"{step_s}s")
        .replace("$w", f"{window_s}s")
        .replace("$sec", str(window_s))
    )


def rate_window(step: int) -> int:
    """The window ``$w`` of a range at ``step``: at least four scrapes, and at least one step."""
    return max(4 * SCRAPE_INTERVAL_S, step)


def choose_step(start: datetime, end: datetime, requested: int) -> int:
    """The requested step, or the smallest coarser one that keeps a series under ``MAX_POINTS``."""
    span = (end - start).total_seconds()
    return max(requested, math.ceil(span / (MAX_POINTS - 1)), 1)


@dataclass(frozen=True)
class RangePlan:
    """A range as it is asked for, on whole steps.

    A range following now has both ends rounded down to the step, so nearby
    requests share one cache entry. A range with an explicit end keeps it, to
    the second, and starts whole steps before it (#306). The step used is
    reported back with a note when it is coarser than requested (FR-013).
    """

    start: datetime
    end: datetime
    step: int
    note: str | None = None


def _floor(moment: datetime, step: int) -> datetime:
    seconds = int(moment.timestamp())
    return datetime.fromtimestamp(seconds - seconds % step, UTC)


def plan_range(start: datetime, end: datetime, requested_step: int, *, align_end: bool = False) -> RangePlan:
    """``start`` to ``end`` on whole steps; ``align_end`` keeps an explicit end (#306).

    Floored, a 7-day export at a 42 min step lost up to 42 min of its newest
    data, which is often the incident it was made for.
    """
    step = choose_step(start, end, requested_step)
    note = f"step raised to {step} s to stay under the point limit" if step > requested_step else None
    if align_end:
        last = _floor(end, 1)
        steps = math.ceil((last - start).total_seconds() / step)
        return RangePlan(start=last - timedelta(seconds=steps * step), end=last, step=step, note=note)
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
        network_ttl: timedelta = timedelta(seconds=WORKER_SCRAPE_INTERVAL_S),
    ) -> None:
        self._address = address.rstrip("/")
        self._namespace = namespace
        self._use_case = use_case
        self._timeout = timeout
        self._transport = transport
        self._clock = clock
        self._ttl = ttl
        self._network_ttl = network_ttl
        self._cache: dict[tuple[MetricsViewName, RangePlan], tuple[datetime, MetricsData]] = {}
        self._network: NetworkSample | None = None
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
        window = rate_window(plan.step)
        params = {
            "query": render_query(
                view, namespace=self._namespace, use_case=self._use_case, window_s=window, step_s=plan.step
            ),
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
        notes = [note for note in (plan.note, spec.note, None if series else "no data in range") if note]
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

    async def network(self) -> NetworkSample:
        """Every network family now, by instant query (spec 016). Cached for one worker scrape (#348).

        Raises:
            SourceUnavailable: as ``query``.
        """
        now = self._clock()
        if self._network is not None and now - self._network.at < self._network_ttl:
            return self._network
        window = rate_window(SCRAPE_INTERVAL_S)
        try:
            async with self._client() as client:
                responses = await asyncio.gather(
                    *(
                        client.get(
                            "/api/v1/query",
                            params={
                                "query": _render(
                                    query,
                                    namespace=self._namespace,
                                    use_case=self._use_case,
                                    window_s=window,
                                    step_s=SCRAPE_INTERVAL_S,
                                ),
                                "time": str(now.timestamp()),
                            },
                        )
                        for query, _ in NETWORK_QUERIES.values()
                    )
                )
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        figures = {
            name: parse_vector(self._body(response, "vector"), labels)
            for (name, (_, labels)), response in zip(NETWORK_QUERIES.items(), responses, strict=True)
        }
        self._network = NetworkSample(at=now, **figures)
        return self._network

    def _body(self, response: httpx.Response, result_type: str = "matrix") -> dict[str, Any]:
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
            or body.get("data", {}).get("resultType") != result_type
        ):
            raise SourceUnavailable(
                f"Prometheus at {self._address} gave an unexpected answer (HTTP {response.status_code})",
                self._error_remedy,
            )
        return body
