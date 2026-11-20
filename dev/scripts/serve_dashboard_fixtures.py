"""Serve the dashboard on captured fixtures, for the UI's browser check (spec 011 research R16).

The real app, with:
- the audit trail captured from a live demo arc (tests/unit/test_presentation/fixtures/README.md),
  opened read-only through the normal audit source;
- intent built from dcfabric's seed file, with the device ids that arc recorded;
- every other source unavailable, each with a reason and a remedy, or with
  `--sources up` the other sources answering with canned data (an active
  schedule, one worker, a scan running, CI on main and two PRs, lab runs of
  both suites, and synthetic metrics over any range), for the panels that read them;
- the device health changes of today's lab added to that trail (spec 016: both
  spines unhealthy for their sessions, leaf-01 unhealthy for an hour, leaf-03
  unknown for two minutes);
- the clock fixed just after the arc's last scan, so ages and default ranges are stable;
- with `--scan-steps`, the arc with the step events a scan records since spec 015
  added (FR-016), so a scan's loop strip shows its blocks.

Nothing here reaches a lab, the stack or the network.

Usage:
    uv run python dev/scripts/serve_dashboard_fixtures.py [--port 18931] [--sources down|up] [--scan-steps]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from snapl_orchestrator.models import (  # noqa: E402 — after the path is set
    DriftResponseMode,
    DriftScheduleView,
    RunSummary,
    ScheduledRunInfo,
)
from snapl_orchestrator.worker.client import RunningWorkflowInfo  # noqa: E402
from snapl_presentation.dashboard.app import create_app  # noqa: E402
from snapl_presentation.dashboard.models import (  # noqa: E402
    CiJob,
    CiStatus,
    CiTarget,
    JobResult,
    LabEvidence,
    LabRun,
    LabRunStatus,
    MetricPoint,
    MetricsData,
    MetricSeries,
    MetricsViewName,
    SourceName,
    View,
)
from snapl_presentation.dashboard.prometheus import VIEWS, NetworkSample, RangePlan  # noqa: E402
from snapl_presentation.dashboard.sources import AuditSource, Sources, TemporalSnapshot  # noqa: E402
from snapl_presentation.settings import CliSettings  # noqa: E402
from tests.unit.test_presentation.dashboard_fakes import (  # noqa: E402
    FIXTURE_NOW,
    arc_states,
    write_arc_with_health,
    write_arc_with_scan_steps,
)

NOW = FIXTURE_NOW
REMEDIES = {
    SourceName.TEMPORAL: "uv run invoke dev.deps",
    SourceName.CI: "export GITHUB_TOKEN=…",
    SourceName.LAB_RUNS: "uv run invoke test-e2e",
    SourceName.PROMETHEUS: "uv run invoke dev.deps",
}


class _Intent:
    """dcfabric's seeded devices and links, as the Source of Truth would answer."""

    def __init__(self) -> None:
        self._states = arc_states()
        self.names = {state.device.id: state.device.name for state in self._states}

    async def refresh(self) -> View[Any]:
        return View(source=SourceName.SOT, available=True, fetched_at=NOW, data=self._states)


class _Down:
    def __init__(self, source: SourceName) -> None:
        self._source = source

    async def refresh(self) -> View[Any]:
        return View(
            source=self._source,
            available=False,
            reason=f"{self._source.value} is not part of the fixture server",
            remedy=REMEDIES[self._source],
        )

    async def query(self, *_: Any) -> Any:
        from snapl_presentation.dashboard.sources import SourceUnavailable  # noqa: PLC0415

        raise SourceUnavailable("prometheus is not part of the fixture server", REMEDIES[SourceName.PROMETHEUS])

    async def network(self) -> Any:
        return await self.query()


class _Up:
    """A source answering with canned data, read just now."""

    def __init__(self, source: SourceName, data: Any) -> None:
        self._source = source
        self._data = data

    async def refresh(self) -> View[Any]:
        return View(source=self._source, available=True, fetched_at=NOW, data=self._data)


def _temporal_up() -> TemporalSnapshot:
    """An active remediating schedule, one worker polling, and a scan running for 12 s."""
    last = NOW - timedelta(minutes=5)
    schedule = DriftScheduleView(
        schedule_id="drift-response-dcfabric",
        use_case_id="dcfabric",
        interval=timedelta(minutes=5),
        mode=DriftResponseMode.REMEDIATE,
        paused=False,
        next_runs=[NOW + timedelta(minutes=4)],
        recent_runs=[
            ScheduledRunInfo(
                workflow_id=f"drift-response-dcfabric-{last:%Y-%m-%dT%H:%M:%SZ}",
                scheduled_at=last,
                started_at=last + timedelta(seconds=1),
                status="COMPLETED",
                summary=RunSummary(
                    total=6, clean=5, drifted=1, errored=0, remediated=0, succeeded=0, failed=0, wrote=False
                ),
            )
        ],
    )
    running = [
        RunningWorkflowInfo(
            workflow_id="scan-drift-dcfabric-live",
            workflow_type="ScanDrift",
            start_time=NOW - timedelta(seconds=12),
            task_queue="snapl-tasks",
        )
    ]
    return TemporalSnapshot(running=running, pollers=1, schedule=schedule)


def _ci_up() -> CiStatus:
    """main green with coverage, one PR whose unit tests failed, one passing, and three left out."""
    actions = "https://github.com/anton-tvrz/snapl/actions/runs"

    def job(name: str, result: JobResult, run: int) -> CiJob:
        return CiJob(name=name, result=result, url=f"{actions}/{run}/job/{run + 1}")

    return CiStatus(
        repo="anton-tvrz/snapl",
        authenticated=False,
        omitted_prs=3,
        targets=[
            CiTarget(
                kind="main",
                head_sha="e534978" + "0" * 33,
                gate=JobResult.SUCCESS,
                jobs=[job("Unit Tests", JobResult.SUCCESS, 100), job("Dashboard UI", JobResult.SUCCESS, 100)],
                run_url=f"{actions}/100",
                coverage_pct=91.4,
            ),
            CiTarget(
                kind="pull_request",
                number=212,
                title="feat(presentation): dashboard panels",
                head_sha="884d9b1" + "0" * 33,
                gate=JobResult.FAILURE,
                jobs=[job("Unit Tests", JobResult.FAILURE, 200), job("Dashboard UI", JobResult.SUCCESS, 200)],
                run_url=f"{actions}/200",
            ),
            CiTarget(
                kind="pull_request",
                number=210,
                title="docs: dashboard guide",
                head_sha="9d24992" + "0" * 33,
                gate=JobResult.SUCCESS,
                jobs=[job("Unit Tests", JobResult.SUCCESS, 300)],
                run_url=f"{actions}/300",
            ),
        ],
    )


def _lab_up() -> LabEvidence:
    """e2e passed 3 commits ago; integration's commit is not on main; one run incomplete, one unreadable."""
    e2e = LabRun(
        id="20261107T201500Z-e2e",
        suite="e2e",
        status=LabRunStatus.PASSED,
        started_at=NOW - timedelta(hours=1, minutes=5),
        ended_at=NOW - timedelta(hours=1),
        commit="b" * 40,
        branch="main",
        dirty=False,
        passed=14,
        failed=0,
        skipped=1,
    )
    integration = LabRun(
        id="20261106T180000Z-integration",
        suite="integration",
        status=LabRunStatus.FAILED,
        started_at=NOW - timedelta(days=1, hours=3),
        ended_at=NOW - timedelta(days=1, hours=2, minutes=50),
        commit="c" * 40,
        branch="overnight/011-e1-replay",
        dirty=True,
        passed=20,
        failed=2,
        skipped=0,
    )
    incomplete = LabRun(id="20261107T190000Z-e2e", suite="e2e", status=LabRunStatus.INCOMPLETE)
    unreadable = LabRun(
        id="20261105T120000Z-integration",
        suite="integration",
        status=LabRunStatus.UNREADABLE,
        problem="run.json is not JSON",
    )
    return LabEvidence(
        latest={"e2e": e2e, "integration": integration},
        commits_on_main_since={"e2e": 3, "integration": None},
        matched_by={"e2e": "ancestor"},
        commits_unknown_reason={"integration": "commit cccccccc is not in the local history"},
        runs=[e2e, incomplete, integration, unreadable],
    )


# Synthetic values for each view, by point index: deterministic, with a gap
# (None) or a down stretch somewhere, so the page shows both.
_SERIES: dict[MetricsViewName, list[tuple[dict[str, str], Any]]] = {
    MetricsViewName.WORKFLOW_DURATION: [
        ({"workflow_type": "DeployIntent"}, lambda i: 900.0 + 150 * (i * 7 % 11)),
        ({"workflow_type": "ScanDrift"}, lambda i: None if 100 <= i < 110 else 1100.0 + 120 * (i * 5 % 13)),
    ],
    MetricsViewName.GNMI_DURATION: [
        ({"device": "spine-01", "operation": "collect"}, lambda i: 40.0 + 6 * (i * 3 % 7)),
        ({"device": "leaf-01", "operation": "configure"}, lambda i: 120.0 + 15 * (i * 5 % 9)),
    ],
    MetricsViewName.GNMI_ERRORS: [({"device": "leaf-01"}, lambda i: 0.05 if 60 <= i < 80 else 0.0)],
    MetricsViewName.WORKER_LAG: [({}, lambda i: 20.0 + i * 3 % 17)],
    MetricsViewName.WORKER_UP: [({}, lambda i: 0.0 if 150 <= i < 165 else 1.0)],
    MetricsViewName.SOURCE_HEALTH: [
        ({"source": source}, lambda i, source=source: _source_up(source, i))
        for source in ("audit", "sot", "temporal", "ci", "lab_runs")
    ],
    # Spec 015, with the label sets of its research R2.
    MetricsViewName.WORKFLOW_MEAN: [
        ({"workflow_type": "DeployIntent"}, lambda i: 700.0 + 60 * (i * 7 % 11)),
        ({"workflow_type": "ScanDrift"}, lambda i: None if 100 <= i < 110 else 1500.0 + 40 * (i * 5 % 13)),
    ],
    MetricsViewName.GNMI_MEAN: [
        ({"device": "spine-01", "operation": "collect"}, lambda i: 30.0 + 3 * (i * 3 % 7)),
        ({"device": "leaf-01", "operation": "configure"}, lambda i: 90.0 + 8 * (i * 5 % 9)),
    ],
    MetricsViewName.WORKFLOWS_COMPLETED: [
        ({"workflow_type": "ScanDrift"}, lambda i: 1.0 if i % 20 < 4 else 0.0),
        ({"workflow_type": "DriftResponse"}, lambda i: 1.0 if 2 <= i % 20 < 6 else 0.0),
        ({"workflow_type": "DeployIntent"}, lambda i: 1.0 if 60 <= i < 64 else 0.0),
    ],
    MetricsViewName.ACTIVITY_TIME: [
        ({"activity_type": activity}, lambda i, base=base: base + base / 10 * (i * 3 % 7))
        for activity, base in (
            ("collect_drift_state", 220.0),
            ("detect_drift", 12.0),
            ("fetch_desired_state", 80.0),
            ("fetch_devices_for_use_case", 60.0),
            ("apply_config", 400.0),
            ("record_audit_event", 4.0),
        )
    ],
    MetricsViewName.WORKER_SLOTS: [
        ({"worker_type": "ActivityWorker"}, lambda i: 6.0 if i % 20 < 2 else 0.0),
        ({"worker_type": "LocalActivityWorker"}, lambda _: 0.0),
        ({"worker_type": "WorkflowWorker"}, lambda i: 1.0 if i % 20 < 2 else 0.0),
    ],
    MetricsViewName.SERVER_REQUEST_TIME: [({}, lambda i: 15.0 + i * 5 % 23)],
    MetricsViewName.SOURCE_FETCH_TIME: [
        ({"source": source}, lambda i, base=base: base + base / 5 * (i * 7 % 5))
        for source, base in (("audit", 4.0), ("sot", 90.0), ("temporal", 30.0), ("ci", 600.0), ("lab_runs", 15.0))
    ],
    # Spec 015 PR E: the worker's counters, with the label sets of contracts/metrics-views.md.
    # A scan every 20 points finds leaf-01 drifted for a stretch, and a repair deploys it back.
    MetricsViewName.SCAN_RESULTS: [
        ({"device": "spine-01", "state": "in_sync"}, lambda i: 1.0 if i % 20 < 4 else 0.0),
        ({"device": "leaf-01", "state": "in_sync"}, lambda i: 1.0 if i % 20 < 4 and not 60 <= i < 100 else 0.0),
        ({"device": "leaf-01", "state": "missing"}, lambda i: 1.0 if i % 20 < 4 and 60 <= i < 80 else 0.0),
        ({"device": "leaf-02", "state": "undesired"}, lambda i: 1.0 if i % 20 < 4 and 80 <= i < 100 else 0.0),
        ({"device": "leaf-02", "state": "error"}, lambda i: None if 100 <= i < 110 else 0.0),
    ],
    MetricsViewName.DRIFT_PATHS: [
        ({"device": "leaf-01", "kind": "missing"}, lambda i: 2.0 if i % 20 < 4 and 60 <= i < 80 else 0.0),
        ({"device": "leaf-02", "kind": "undesired"}, lambda i: 1.0 if i % 20 < 4 and 80 <= i < 100 else 0.0),
    ],
    MetricsViewName.DEPLOY_OUTCOMES: [
        ({"outcome": "succeeded", "reason": "none"}, lambda i: 1.0 if 80 <= i < 84 else 0.0),
        ({"outcome": "failed", "reason": "apply_failed"}, lambda i: 1.0 if 64 <= i < 68 else 0.0),
    ],
}


# Spec 016: the network, as today's lab reads (research R1). Traffic on every
# link, errors on leaf-02's first uplink, the spines' sessions down, and CPU and
# memory as the containers report the host VM: 94 % memory everywhere, CPU 3 to 5.
_DEVICES = ("spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04")
_ERRING = ("leaf-02", "ethernet-1/49")


def _cpu(k: int, i: int = 0) -> float:
    return 3.0 + (k + i) % 3


_SERIES[MetricsViewName.INTERFACE_TRAFFIC] = [
    ({"device": device}, lambda i, k=k: 8090.0 + 300 * k + 80 * (i % 12)) for k, device in enumerate(_DEVICES)
]
# Every link end intent names has an error and a discard series, as the worker exports them.
_LINKS = [(st.device.name, i.name) for st in arc_states() for i in st.interfaces if i.name != "lo0"]
_SERIES[MetricsViewName.INTERFACE_ERRORS] = [
    (
        {"device": device, "interface": name},
        lambda i, erring=(device, name) == _ERRING: 0.2 if erring and i >= 120 else 0.0,
    )
    for device, name in _LINKS
]
_SERIES[MetricsViewName.INTERFACE_DISCARDS] = [
    ({"device": device, "interface": name}, lambda _: 0.0) for device, name in _LINKS
]
# The spines' sessions are down; each leaf has its two.
_ESTABLISHED = {device: 0.0 if device.startswith("spine") else 2.0 for device in _DEVICES}
_SERIES[MetricsViewName.BGP_ESTABLISHED] = [
    ({"device": device}, lambda _, n=established: n) for device, established in _ESTABLISHED.items()
]
_SERIES[MetricsViewName.DEVICE_CPU] = [
    ({"device": device}, lambda i, k=k: _cpu(k, i)) for k, device in enumerate(_DEVICES)
]
_SERIES[MetricsViewName.DEVICE_MEMORY] = [({"device": device}, lambda _: 94.0) for device in _DEVICES]


def _network_up() -> NetworkSample:
    """The same lab at NOW, for /network, over intent's own interfaces and sessions."""
    sample = NetworkSample(at=NOW)
    for k, state in enumerate(arc_states()):
        device = state.device.name
        spine = state.device.role == "spine"
        for n, interface in enumerate(state.interfaces):
            sample.interface_up[(device, interface.name)] = 1.0
            if interface.name == "lo0":
                continue  # a loopback has no counters
            for d, direction in enumerate(("in", "out")):
                key = (device, interface.name, direction)
                sample.octets[key] = 1000.0 + 60 * k + 15 * n + 7 * d
                sample.errors[key] = 0.2 if (device, interface.name) == _ERRING and direction == "in" else 0.0
                sample.discards[key] = 0.0
        for session in state.bgp_sessions:
            sample.established[(device, session.peer_address)] = 0.0 if spine else 1.0
            sample.prefixes[(device, session.peer_address)] = 0.0 if spine else 2.0
        sample.cpu[(device,)] = _cpu(k)
        sample.memory[(device,)] = 94.0
    return sample


def _source_up(source: str, i: int) -> float | None:
    if source == "temporal" and 30 <= i < 45:
        return 0.0
    if source == "ci" and 200 <= i < 210:
        return None
    return 1.0


class _PrometheusUp:
    """Prometheus answering every named view over any range with synthetic series."""

    async def refresh(self) -> View[Any]:
        return View(source=SourceName.PROMETHEUS, available=True, fetched_at=NOW)

    async def query(self, view: MetricsViewName, plan: RangePlan) -> MetricsData:
        count = int((plan.end - plan.start).total_seconds()) // plan.step + 1
        times = [plan.start + timedelta(seconds=plan.step * i) for i in range(count)]
        series = [
            MetricSeries(labels=labels, points=[MetricPoint(t=t, value=value(i)) for i, t in enumerate(times)])
            for labels, value in _SERIES[view]
        ]
        return MetricsData(
            view=view,
            unit=VIEWS[view].unit,
            start=plan.start,
            end=plan.end,
            step=plan.step,
            series=series,
            note="; ".join(note for note in (plan.note, VIEWS[view].note) if note) or None,
        )

    async def network(self) -> NetworkSample:
        return _network_up()


def build_app(ui_dir: Path | None = None, *, up: bool = False, audit_path: Path | None = None):
    """The app over the fixtures. ``audit_path`` defaults to the arc with today's health added."""
    if audit_path is None:
        audit_path = asyncio.run(write_arc_with_health(Path(tempfile.mkdtemp()) / "arc-with-health.sqlite"))
    sources = Sources(
        audit=AuditSource(str(audit_path), clock=lambda: NOW),
        sot=_Intent(),
        temporal=_Up(SourceName.TEMPORAL, _temporal_up()) if up else _Down(SourceName.TEMPORAL),
        ci=_Up(SourceName.CI, _ci_up()) if up else _Down(SourceName.CI),
        lab_runs=_Up(SourceName.LAB_RUNS, _lab_up()) if up else _Down(SourceName.LAB_RUNS),
        prometheus=_PrometheusUp() if up else _Down(SourceName.PROMETHEUS),
    )
    extra = {"ui_dir": ui_dir} if ui_dir is not None else {}
    return create_app(sources, CliSettings(), "dcfabric", clock=lambda: NOW, repo="anton-tvrz/snapl", **extra)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=18931)
    parser.add_argument("--sources", choices=["down", "up"], default="down")
    parser.add_argument("--scan-steps", action="store_true", help="serve the arc with a scan's step events (spec 015)")
    args = parser.parse_args()
    audit_path = None
    if args.scan_steps:
        audit_path = asyncio.run(write_arc_with_scan_steps(Path(tempfile.mkdtemp()) / "arc-with-scan-steps.sqlite"))
    app = build_app(up=args.sources == "up", audit_path=audit_path)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
