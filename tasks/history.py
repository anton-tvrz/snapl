"""Long-lived history: archive it, or purge the metrics store (spec 012, research R9, R10).

`invoke history.archive` packs everything snapl has recorded into one tarball
that outlives the stack: the audit DB, a Prometheus snapshot, the recorded
live test runs, and a 7-day export of the dashboard's JSON. A part that can't
be read is written down as missing, with the reason, and the task exits 1;
the rest of the archive is still written (FR-018).

`invoke history.purge` deletes the metrics store, and nothing else. It is the
one way metrics are deleted: `dev.down --volumes` and `demo.reset` keep them.

Contract: specs/012-measured-metrics/contracts/history-tasks.md.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sqlite3

# Bandit B404: subprocess runs one fixed git command, no shell.
import subprocess  # nosec B404
import sys
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from invoke import Exit, task

from snapl_presentation.dashboard.models import MetricsViewName
from snapl_presentation.settings import CliSettings

from .dev import PROMETHEUS_DATA
from .shared import PROJECT_ROOT

FORMAT = "snapl-history/1"
DEFAULT_ARCHIVE_DIR = PROJECT_ROOT / ".snapl" / "archives"
# The image Compose runs (development/docker-compose.yml); purge reuses it to
# delete files owned by its `nobody` user.
PROMETHEUS_IMAGE = "prom/prometheus:v3.10.0"
EXPORT_DAYS = 7
# Spec 011 adds the views archive mode reads (its research R13); older archives
# lack them and still open.
_EXPORTS = ("overview", "history", "links", "sources", "topology", "devices", "runs", "drift-timeline")
_RANGED = ("history", "drift-timeline")


@dataclass
class Part:
    name: str
    path: str
    present: bool
    source: str
    reason: str | None = None
    bytes: int | None = None


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"  # pragma: no cover — the loop always returns


def _from_root(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def _commit() -> str | None:
    try:
        # Bandit: a fixed git command, no shell.
        result = subprocess.run(  # nosec B603 B607
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


# --- the parts ---------------------------------------------------------------------


def _audit_copy(source: Path, staging: Path) -> Part:
    """SQLite's online backup from a read-only connection: consistent while the worker writes."""
    target = staging / "audit" / "snapl-audit.sqlite"
    part = Part("audit_db", "audit/snapl-audit.sqlite", False, str(source))
    if not source.is_file():
        part.reason = f"no audit DB at {source} (set SNAPL_AUDIT_DB to the path the worker writes)"
        return part
    target.parent.mkdir(parents=True)
    try:
        with (
            sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as src,
            sqlite3.connect(target) as dst,
        ):
            src.backup(dst)
    except sqlite3.Error as exc:
        part.reason = f"the audit DB at {source} could not be read: {exc}"
        return part
    part.present, part.bytes = True, target.stat().st_size
    return part


def _metrics_snapshot(prometheus: str, data: Path) -> tuple[Part, Path | None]:
    """Ask Prometheus for a snapshot; it appears under ``<data>/snapshots/<name>/`` (R9)."""
    url = f"{prometheus.rstrip('/')}/api/v1/admin/tsdb/snapshot"
    part = Part("metrics_snapshot", "prometheus", False, url)
    try:
        response = httpx.post(url, timeout=120)
    except httpx.HTTPError as exc:
        part.reason = f"Prometheus not reachable at {prometheus} ({exc}); start it with: uv run invoke dev.deps"
        return part, None
    try:
        body = response.json()
    except ValueError:
        body = {}
    name = (body.get("data") or {}).get("name") if body.get("status") == "success" else None
    if not name:
        error = body.get("error") or f"HTTP {response.status_code}"
        part.reason = f"Prometheus refused the snapshot: {error}"
        return part, None
    snapshot = data / "snapshots" / name
    if not snapshot.is_dir():
        part.reason = f"Prometheus made snapshot {name}, but it isn't under {data}"
        return part, None
    part.path, part.present, part.bytes = f"prometheus/{name}", True, _size(snapshot)
    return part, snapshot


def _test_runs(runs: Path) -> Part:
    part = Part("test_runs", "test-runs", False, str(runs))
    if not runs.is_dir() or not any(runs.iterdir()):
        part.reason = f"no recorded test runs at {runs} (record one with: uv run invoke test-e2e)"
        return part
    part.present, part.bytes = True, _size(runs)
    return part


def _dashboard_export(dashboard: str, staging: Path, now: datetime) -> Part:
    """The dashboard's JSON for the last 7 days, as the API serves it (R9)."""
    part = Part("dashboard_export", "export", False, dashboard)
    start = (now - timedelta(days=EXPORT_DAYS)).isoformat()
    wanted: list[tuple[str, str, dict[str, str]]] = [
        (f"{name}.json", f"/api/v1/{name}", {"start": start} if name in _RANGED else {}) for name in _EXPORTS
    ]
    wanted += [(f"metrics/{v.value}.json", f"/api/v1/metrics/{v.value}", {"start": start}) for v in MetricsViewName]
    out = staging / "export"
    failed: list[str] = []
    with httpx.Client(base_url=dashboard, timeout=30) as client:
        for filename, path, params in wanted:
            try:
                response = client.get(path, params=params)
            except httpx.TransportError as exc:
                if not out.exists():
                    part.reason = (
                        f"the dashboard is not answering at {dashboard} ({exc}); start it with: uv run snapl dashboard"
                    )
                    return part
                failed.append(f"{path} ({exc})")
                continue
            if response.status_code != httpx.codes.OK:
                failed.append(f"{path} (HTTP {response.status_code})")
                continue
            target = out / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(response.content)
    if failed:
        part.reason = "the dashboard did not answer: " + ", ".join(failed)
        part.bytes = _size(out) if out.exists() else None
        return part
    part.present, part.bytes = True, _size(out)
    return part


# --- archive -----------------------------------------------------------------------


def _target(out_dir: Path, now: datetime) -> Path:
    stem = f"snapl-history-{now.strftime('%Y%m%dT%H%M%SZ')}"
    target, n = out_dir / f"{stem}.tar.gz", 1
    while target.exists():
        n += 1
        target = out_dir / f"{stem}-{n}.tar.gz"
    return target


def _write(target: Path, members: list[tuple[Path, str]]) -> None:
    """Write to ``<name>.partial`` and rename when complete: a crash never leaves a finished-looking file."""
    partial = target.with_name(target.name + ".partial")
    try:
        with tarfile.open(partial, "w:gz") as tar:
            for source, arcname in members:
                tar.add(source, arcname=arcname)
        partial.rename(target)
    finally:
        partial.unlink(missing_ok=True)


@task(help={"use_case": "The use case the archive is labelled with (default dcfabric)."})
def archive(ctx, use_case="dcfabric"):
    """Pack the audit DB, a metrics snapshot, the test runs and a dashboard export into one tarball."""
    settings = CliSettings.from_env()
    out_dir = _from_root(os.environ.get("SNAPL_ARCHIVE_DIR") or str(DEFAULT_ARCHIVE_DIR))
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    dashboard = f"http://{settings.dashboard_host}:{settings.dashboard_port}"
    runs = _from_root(settings.test_runs_dir)

    with tempfile.TemporaryDirectory(prefix="snapl-archive-") as tmp:
        staging = Path(tmp)
        audit = _audit_copy(_from_root(settings.audit_db), staging)
        metrics, snapshot = _metrics_snapshot(settings.prometheus_address, PROMETHEUS_DATA)
        try:
            tests = _test_runs(runs)
            export = _dashboard_export(dashboard, staging, now)
            parts = [audit, metrics, tests, export]
            manifest = {
                "format": FORMAT,
                "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "snapl_commit": _commit(),
                "use_case": use_case,
                "parts": [asdict(part) for part in parts],
            }
            (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            members = [(staging / "manifest.json", "manifest.json")]
            members += [(staging / "audit" / "snapl-audit.sqlite", audit.path)] if audit.present else []
            members += [(snapshot, metrics.path)] if snapshot is not None else []
            members += [(runs, tests.path)] if tests.present else []
            members += [(staging / "export", export.path)] if (staging / "export").exists() else []
            target = _target(out_dir, now)
            _write(target, members)
        finally:
            # The snapshot is a copy inside the live store: never let archives grow it.
            if snapshot is not None:
                shutil.rmtree(snapshot, ignore_errors=True)

    print(f"{target} ({_human(target.stat().st_size)})")
    for part in parts:
        state = f"{_human(part.bytes or 0)}" if part.present else f"MISSING: {part.reason}"
        print(f"  {part.name}: {state}")
    missing = [part for part in parts if not part.present]
    for part in missing:
        print(f"missing {part.name}: {part.reason}", file=sys.stderr)
    if missing:
        raise Exit(code=1)


# --- purge -------------------------------------------------------------------------


def _answers(address: str) -> bool:
    try:
        httpx.get(f"{address.rstrip('/')}/-/ready", timeout=2)
    except httpx.HTTPError:
        return False
    return True


@task(help={"yes": "Delete without asking (needed when there is no terminal to ask on)."})
def purge(ctx, yes=False):
    """Delete the metrics store (.snapl/prometheus/) and nothing else. Prometheus must be stopped."""
    settings = CliSettings.from_env()
    if _answers(settings.prometheus_address):
        print(
            f"Prometheus is running at {settings.prometheus_address}; stop it first: uv run invoke dev.stop",
            file=sys.stderr,
        )
        raise Exit(code=1)
    data = PROMETHEUS_DATA
    if not data.exists():
        print(f"Nothing to delete: {data} does not exist.")
        return
    print(f"This deletes every stored metric: {data} ({_human(_size(data))}).")
    print("The audit DB, the test runs and the archives are kept.")
    if not yes:
        if not sys.stdin.isatty():
            print("Refusing: no terminal to confirm on. Re-run with --yes to delete without asking.", file=sys.stderr)
            raise Exit(code=1)
        if input("Type purge to delete: ").strip() != "purge":
            print("Aborted; nothing deleted.")
            return
    try:
        shutil.rmtree(data)
    except PermissionError:
        # On Linux the files belong to the container's `nobody` (R6): delete as root inside one.
        parent = shlex.quote(str(data.parent))
        ctx.run(
            f"docker run --rm --user 0 -v {parent}:/snapl --entrypoint sh {PROMETHEUS_IMAGE}"
            f" -c 'rm -rf /snapl/{data.name}'"
        )
    print(f"Deleted {data}.")
