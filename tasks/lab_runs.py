"""Record each live lab test run (spec 010 FR-026, contracts/lab-runs.md).

`invoke test-e2e` and `invoke test-integration` run pytest against the live
stack. Unit tests say nothing about whether the loop still works on real
devices; only these runs do. So each one leaves a record under
`.snapl/test-runs/`: what was tested (commit, tree, device image) and how it
went. The dashboard reads the records to say how current the live evidence is.

Recording never changes the outcome: if the record cannot be written, the run
still happens and the task still exits with pytest's code.
"""

from __future__ import annotations

import json
import os

# Bandit B404: subprocess runs fixed git commands, no shell.
import subprocess  # nosec B404

# Bandit B405: parses the JUnit file our own pytest just wrote.
import xml.etree.ElementTree as ET  # nosec B405
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .shared import PROJECT_ROOT

FORMAT = 1
TOPOLOGY = PROJECT_ROOT / "containerlab" / "dcfabric.yml"
DEFAULT_RUNS_DIR = PROJECT_ROOT / ".snapl" / "test-runs"
# pytest's exit codes: 0 all passed, 1 some failed, 2 interrupted; anything
# else (usage error, internal error, no tests) is a failed run.
_STATUS = {0: "passed", 1: "failed", 2: "interrupted"}
# The conventional exit code of a process stopped by Ctrl-C (128 + SIGINT).
_CTRL_C = 130


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(*args: str) -> str | None:
    try:
        # Bandit: fixed git arguments, no shell.
        result = subprocess.run(  # nosec B603 B607
            ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _device_image() -> str | None:
    """The image the lab is configured with: what the topology says, not what
    is running (research R10)."""
    import yaml  # noqa: PLC0415 — keeps `invoke --list` import-light

    try:
        topology = yaml.safe_load(TOPOLOGY.read_text())
    except (OSError, yaml.YAMLError):
        return None
    kinds = (topology or {}).get("topology", {}).get("kinds", {})
    for kind in kinds.values():
        if isinstance(kind, dict) and kind.get("image"):
            return str(kind["image"])
    return None


def _counts(junit: Path) -> dict[str, int] | None:
    try:
        root = ET.parse(junit).getroot()  # noqa: S314  # nosec B314
    except (OSError, ET.ParseError):
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key, 0))
    return {
        "passed": totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"],
        "failed": totals["failures"],
        "skipped": totals["skipped"],
        "errors": totals["errors"],
    }


def _write(folder: Path, record: dict[str, Any]) -> None:
    """Atomically: a reader never sees half a record."""
    tmp = folder / "run.json.tmp"
    tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    tmp.replace(folder / "run.json")


def _shown(folder: Path) -> Path:
    """The folder relative to where the task was run from, when it is under it."""
    try:
        return folder.relative_to(Path.cwd())
    except ValueError:
        return folder


def _runs_dir() -> Path:
    configured = os.environ.get("SNAPL_TEST_RUNS_DIR")
    return Path(configured) if configured else DEFAULT_RUNS_DIR


def record_run(ctx, suite: str, command: str) -> int:
    """Run ``command`` (a pytest invocation) and record the run.

    Returns pytest's exit code. Ctrl-C is recorded as ``interrupted`` and
    re-raised.
    """
    started = _now()
    folder = _runs_dir() / f"{started:%Y%m%dT%H%M%SZ}-{suite}"
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    record: dict[str, Any] = {
        "format": FORMAT,
        "suite": suite,
        "status": "running",
        "started_at": _iso(started),
        "commit": _git("rev-parse", "HEAD"),
        "tree": _git("rev-parse", "HEAD^{tree}"),
        # A detached HEAD has no branch to name.
        "branch": None if branch in (None, "HEAD") else branch,
        "dirty": bool(_git("status", "--porcelain")),
        "device_image": _device_image(),
        "pid": os.getpid(),
    }
    try:
        folder.mkdir(parents=True, exist_ok=True)
        _write(folder, record)
    except OSError as exc:
        print(f"warning: could not record this lab run in {folder}: {exc}")
        return int(ctx.run(command, pty=True, warn=True).exited)

    junit = folder / "junit.xml"
    exit_code = _CTRL_C
    try:
        exit_code = int(ctx.run(f"{command} --junitxml={junit}", pty=True, warn=True).exited)
    finally:
        record |= {
            "status": _STATUS.get(exit_code, "interrupted" if exit_code == _CTRL_C else "failed"),
            "ended_at": _iso(_now()),
            "exit_code": exit_code,
        }
        counts = _counts(junit)
        if counts is not None:
            record["counts"] = counts
        try:
            _write(folder, record)
        except OSError as exc:
            print(f"warning: could not finish the lab run record in {folder}: {exc}")
        summary = (
            f"passed: {counts['passed']}, failed: {counts['failed']}, skipped: {counts['skipped']}"
            if counts
            else "no test counts"
        )
        print(f"Lab run recorded: {_shown(folder)} ({summary})")
    return exit_code
