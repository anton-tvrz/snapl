"""Write a snapshot of the fixture server's app, for the UI's browser check (spec 011 T083).

The same code `snapl dashboard --snapshot` runs (snapshot.py), on the app
`serve_dashboard_fixtures.py --sources up` serves: the captured demo arc, every
other source answering with canned data, the clock fixed after the last scan.
It needs the built UI's template (`uv run invoke dashboard.build`).

Usage:
    uv run python dev/scripts/write_fixture_snapshot.py <out.html> [--days 7]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import serve_dashboard_fixtures  # noqa: E402 — after the path is set

from snapl_presentation.dashboard import pages  # noqa: E402
from snapl_presentation.dashboard.snapshot import NOT_BUILT, collect_bundle, render, summary, write  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    template = pages.BUILT_UI / "snapshot.html"
    if not template.is_file():
        sys.exit(f"error: {NOT_BUILT}")
    bundle = collect_bundle(
        serve_dashboard_fixtures.build_app(up=True), days=args.days, commit="fixture", source="live"
    )
    write(args.out, render(template.read_text(encoding="utf-8"), bundle), force=True)
    print("\n".join(summary(args.out, bundle)))


if __name__ == "__main__":
    main()
