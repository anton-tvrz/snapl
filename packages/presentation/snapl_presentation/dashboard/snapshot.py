"""The snapshot: one HTML file that shows the loop with no server (spec 011 research R14).

``snapl dashboard --snapshot <file>`` builds the app as serving would, calls its
own routes in-process (no port is bound), puts the answers into the built
template's ``snapl-bundle`` element, and writes one file. The bundle is made of
API responses only, so it carries no credential or environment value.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

import httpx

from snapl_presentation.dashboard.models import MetricsViewName

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI

FORMAT = "snapl-snapshot/1"
NOT_BUILT = "the designed UI is not built. Build it with: uv run invoke dashboard.build"
# The newest runs of any type also get their steps, so the loop strip has the
# latest run; every deploy gets them because a deploy records each step.
LATEST_WITH_STEPS = 20
CONTAINS = "devices, links, drift history, runs and their steps, schedule, CI status, lab runs, metrics"
NOTE = "error text and paths recorded from devices are included as recorded. Read it before publishing."

_ELEMENT = re.compile(r'(<script id="snapl-bundle" type="application/json">)(.*?)(</script>)', re.S)
# Inside a script element, none of these may appear raw: `</script` or `<!--`
# would end or change it, and U+2028/2029 end a line in older parsers.
_ESCAPES: dict[str, str | int | None] = {
    "<": "\\u003c",
    ">": "\\u003e",
    "&": "\\u0026",
    chr(0x2028): "\\u2028",
    chr(0x2029): "\\u2029",
}


class SnapshotError(Exception):
    """A snapshot that cannot be written, with the message to print."""


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _bucket(days: int) -> str:
    # The UI's history table buckets (lib/history.ts bucketFor).
    if days <= 2:
        return "1h"
    return "1d" if days <= 60 else "1w"


async def _collect(app: FastAPI, days: int, commit: str | None, source: str) -> dict[str, Any]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://snapshot.invalid") as client:

        async def get(path: str, **params: Any) -> Any:
            response = await client.get(f"/api/v1/{path}", params=params)
            response.raise_for_status()
            return response.json()

        mode = await get("mode")
        end = datetime.fromisoformat(mode["now"])
        start = end - timedelta(days=days)
        window = {"start": _iso(start), "end": _iso(end)}
        # Read every source once first, so no view is cut short by a source
        # still answering its first request.
        sources = await get("sources")
        runs = await get("runs", limit=500)
        listed = runs["runs"]["data"] or []
        wanted = [run for run in listed if run["workflow_type"] == "DeployIntent"] + listed[:LATEST_WITH_STEPS]
        steps = {}
        for run in wanted:
            key = f"{run['workflow_id']}@{run['started_at']}"
            if key not in steps:
                steps[key] = await get("runs/steps", workflow_id=run["workflow_id"], started_at=run["started_at"])
        return {
            "manifest": {
                "format": FORMAT,
                "created_at": mode["now"],
                "snapl_commit": commit,
                "use_case": mode["use_case"],
                "start": window["start"],
                "end": window["end"],
                "source": source,
            },
            "overview": await get("overview", runs_limit=500),
            "devices": await get("devices"),
            "runs": runs,
            "topology": await get("topology"),
            "timeline": await get("drift-timeline", **window),
            "history": await get("history", bucket=_bucket(days), **window),
            "steps": steps,
            "links": await get("links"),
            "sources": sources,
            "metrics": {view.value: await get(f"metrics/{view.value}", **window) for view in MetricsViewName},
        }


def collect_bundle(
    app: FastAPI, *, days: int, commit: str | None, source: Literal["live", "archive"]
) -> dict[str, Any]:
    """Every view the page reads, over the ``days`` before the app's own now."""
    return asyncio.run(_collect(app, days, commit, source))


def render(template: str, bundle: dict[str, Any]) -> str:
    """The template with the bundle in its one ``snapl-bundle`` element."""
    if len(_ELEMENT.findall(template)) != 1:
        raise SnapshotError("the snapshot template has no snapl-bundle element, or more than one")
    payload = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    payload = payload.translate(str.maketrans(_ESCAPES))
    return _ELEMENT.sub(lambda match: match.group(1) + payload + match.group(3), template)


def write(target: Path, html: str, *, force: bool = False) -> None:
    """Write through ``<name>.partial`` and rename: a failure never leaves a finished-looking file."""
    if target.exists() and not force:
        raise SnapshotError(f"{target} already exists. Pass --force to replace it.")
    partial = target.with_name(target.name + ".partial")
    try:
        partial.write_text(html, encoding="utf-8")
        partial.rename(target)
    finally:
        partial.unlink(missing_ok=True)


def summary(target: Path, bundle: dict[str, Any], *, archive: str | None = None) -> list[str]:
    """What the command prints once the file is written (contracts/cli-and-build.md)."""
    manifest = bundle["manifest"]
    start, end = datetime.fromisoformat(manifest["start"]), datetime.fromisoformat(manifest["end"])
    days = round((end - start) / timedelta(days=1))
    commit = (manifest["snapl_commit"] or "unknown")[:7]
    origin = f"archive {archive}" if archive else "live dashboard sources"
    lines = [
        f"Snapshot written: {target.name} ({round(target.stat().st_size / 1000)} kB)",
        f"  range: {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC ({days} {'day' if days == 1 else 'days'})",
        f"  from: {origin}, commit {commit}",
        f"  contains: {CONTAINS}",
        f"  note: {NOTE}",
    ]
    down = [view for view in bundle["sources"]["sources"] if not view["available"]]
    if down:
        named = ", ".join(f"{view['source']} ({view.get('reason') or 'no reason given'})" for view in down)
        lines.append(f"  unavailable when written: {named}")
    return lines
