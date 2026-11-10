"""Serve the dashboard on captured fixtures, for the UI's browser check (spec 011 research R16).

The real app, with:
- the audit trail captured from a live demo arc (tests/unit/test_presentation/fixtures/README.md),
  opened read-only through the normal audit source;
- intent built from dcfabric's seed file, with the device ids that arc recorded;
- every other source unavailable, each with a reason and a remedy, or with
  `--sources up` the other sources answering with canned data (an active
  schedule, one worker, a scan running, CI on main and two PRs, lab runs of
  both suites), for the panels that read them;
- the clock fixed just after the arc's last scan, so ages and default ranges are stable.

Nothing here reaches a lab, the stack or the network.

Usage:
    uv run python dev/scripts/serve_dashboard_fixtures.py [--port 18931] [--sources down|up]
"""

from __future__ import annotations

import argparse
import sys
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
    SourceName,
    View,
)
from snapl_presentation.dashboard.sources import AuditSource, Sources, TemporalSnapshot  # noqa: E402
from snapl_presentation.settings import CliSettings  # noqa: E402
from tests.unit.test_presentation.dashboard_fakes import ARC_FIXTURE, SCAN_3_END, arc_states  # noqa: E402

NOW = SCAN_3_END + timedelta(seconds=30)
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


def build_app(ui_dir: Path | None = None, *, up: bool = False):
    sources = Sources(
        audit=AuditSource(str(ARC_FIXTURE), clock=lambda: NOW),
        sot=_Intent(),
        temporal=_Up(SourceName.TEMPORAL, _temporal_up()) if up else _Down(SourceName.TEMPORAL),
        ci=_Up(SourceName.CI, _ci_up()) if up else _Down(SourceName.CI),
        lab_runs=_Up(SourceName.LAB_RUNS, _lab_up()) if up else _Down(SourceName.LAB_RUNS),
        prometheus=_Down(SourceName.PROMETHEUS),
    )
    extra = {"ui_dir": ui_dir} if ui_dir is not None else {}
    return create_app(sources, CliSettings(), "dcfabric", clock=lambda: NOW, repo="anton-tvrz/snapl", **extra)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=18931)
    parser.add_argument("--sources", choices=["down", "up"], default="down")
    args = parser.parse_args()
    app = build_app(up=args.sources == "up")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
