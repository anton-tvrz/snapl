"""Serve the dashboard on captured fixtures, for the UI's browser check (spec 011 research R16).

The real app, with:
- the audit trail captured from a live demo arc (tests/unit/test_presentation/fixtures/README.md),
  opened read-only through the normal audit source;
- intent built from dcfabric's seed file, with the device ids that arc recorded;
- every other source unavailable, each with a reason and a remedy;
- the clock fixed just after the arc's last scan, so ages and default ranges are stable.

Nothing here reaches a lab, the stack or the network.

Usage:
    uv run python dev/scripts/serve_dashboard_fixtures.py [--port 18931]
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

from snapl_presentation.dashboard.app import create_app  # noqa: E402 — after the path is set
from snapl_presentation.dashboard.models import SourceName, View  # noqa: E402
from snapl_presentation.dashboard.sources import AuditSource, Sources  # noqa: E402
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


def build_app(ui_dir: Path | None = None):
    sources = Sources(
        audit=AuditSource(str(ARC_FIXTURE), clock=lambda: NOW),
        sot=_Intent(),
        temporal=_Down(SourceName.TEMPORAL),
        ci=_Down(SourceName.CI),
        lab_runs=_Down(SourceName.LAB_RUNS),
        prometheus=_Down(SourceName.PROMETHEUS),
    )
    extra = {"ui_dir": ui_dir} if ui_dir is not None else {}
    return create_app(sources, CliSettings(), "dcfabric", clock=lambda: NOW, repo="anton-tvrz/snapl", **extra)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=18931)
    args = parser.parse_args()
    uvicorn.run(build_app(), host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
