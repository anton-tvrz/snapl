"""Read the recorded live lab runs (spec 010 US4, contracts/lab-runs.md).

`tasks/lab_runs.py` writes one folder per `invoke test-e2e` or `invoke
test-integration` run. This module reads them back and says how current the
live evidence is: the latest run of each suite, and how many commits have
landed on `main` since.

`main` is squash-merged, so a run made on a PR branch is never an ancestor of
`main`. Its commit is matched to `main` by tree instead: squashing an
up-to-date branch gives a commit with the branch head's tree (research R10).
"""

from __future__ import annotations

import json
import os
import re

# Bandit B404: subprocess runs fixed git commands, no shell.
import subprocess  # nosec B404
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from snapl_presentation.dashboard.models import LabEvidence, LabRun, LabRunStatus, SourceName, Suite, View

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

FORMAT = 1
MAX_RUNS = 20
# The newest commits on main searched for a tree match (research R10).
TREE_SEARCH_DEPTH = 500
RUN_REMEDY = "uv run invoke test-e2e"
_FOLDER = re.compile(r"^\d{8}T\d{6}Z-(e2e|integration)$")
_GIT_TIMEOUT_S = 5


def _utcnow() -> datetime:
    return datetime.now(UTC)


def pid_alive(pid: int) -> bool:
    """Whether ``pid`` is a live process this user owns.

    A pid owned by someone else is a reused pid, not our test run.
    """
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OverflowError, ValueError):
        return False
    return True


class _Git:
    def __init__(self, repo: Path) -> None:
        self._repo = repo

    def run(self, *args: str) -> subprocess.CompletedProcess[str] | None:
        try:
            # Bandit: fixed git arguments, no shell.
            return subprocess.run(  # nosec B603 B607
                ["git", *args],
                cwd=self._repo,
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    def ok(self, *args: str) -> bool:
        result = self.run(*args)
        return result is not None and result.returncode == 0

    def out(self, *args: str) -> str | None:
        result = self.run(*args)
        return result.stdout.strip() if result is not None and result.returncode == 0 else None

    def count_since(self, commit: str, main: str) -> int | None:
        count = self.out("rev-list", "--count", f"{commit}..{main}")
        return int(count) if count is not None and count.isdigit() else None


def _read_run(folder: Path, suite: str, is_alive: Callable[[int], bool]) -> LabRun:
    def unreadable(problem: str) -> LabRun:
        return LabRun(id=folder.name, suite=suite, status=LabRunStatus.UNREADABLE, problem=problem)  # type: ignore[arg-type]

    try:
        record: Any = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return unreadable("no run.json")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return unreadable(f"run.json could not be read: {exc}")
    if not isinstance(record, dict):
        return unreadable("run.json is not an object")
    if record.get("format") != FORMAT:
        return unreadable(f"run.json is format {record.get('format')}; this dashboard reads format {FORMAT}")

    status: Any = record.get("status")
    if status == LabRunStatus.RUNNING and not (isinstance(record.get("pid"), int) and is_alive(record["pid"])):
        status = LabRunStatus.INCOMPLETE
    counts: dict[str, Any] = record["counts"] if isinstance(record.get("counts"), dict) else {}
    try:
        return LabRun(
            id=folder.name,
            suite=suite,  # type: ignore[arg-type]
            status=status,
            started_at=record.get("started_at"),
            ended_at=record.get("ended_at"),
            commit=record.get("commit"),
            tree=record.get("tree"),
            branch=record.get("branch"),
            dirty=record.get("dirty"),
            device_image=record.get("device_image"),
            passed=counts.get("passed"),
            failed=counts.get("failed"),
            skipped=counts.get("skipped"),
            errors=counts.get("errors"),
        )
    except ValidationError as exc:
        return unreadable(f"run.json has an invalid field: {exc.errors()[0]['loc']}")


def _commits_since(run: LabRun, git: _Git, main: str) -> tuple[int | None, str]:
    """Commits on ``main`` since the run's commit, and how the commit was matched,
    or None and why it could not be (research R10)."""
    if run.commit and git.ok("merge-base", "--is-ancestor", run.commit, main):
        return git.count_since(run.commit, main), "ancestor"
    if not run.tree:
        return None, "run recorded without a tree"
    log = git.out("log", "-n", str(TREE_SEARCH_DEPTH), "--format=%H %T", main) or ""
    for line in log.splitlines():
        commit, _, tree = line.partition(" ")
        if tree == run.tree:
            return git.count_since(commit, main), "tree"
    if run.commit and git.ok("cat-file", "-e", f"{run.commit}^{{commit}}"):
        return None, "commit not on main yet"
    return None, "commit not in local history"


def read_lab_evidence(
    runs_dir: Path,
    repo: Path,
    *,
    main: str = "main",
    is_alive: Callable[[int], bool] = pid_alive,
) -> LabEvidence:
    """Every recorded run, the latest per suite, and the commits since each.

    Counted against the local ``main`` ref, so "as of your last fetch".
    """
    folders = (
        sorted(
            (folder for folder in runs_dir.iterdir() if folder.is_dir() and _FOLDER.match(folder.name)),
            key=lambda folder: folder.name,
            reverse=True,
        )
        if runs_dir.is_dir()
        else []
    )
    runs = [_read_run(folder, _FOLDER.match(folder.name).group(1), is_alive) for folder in folders]  # type: ignore[union-attr]

    latest: dict[Suite, LabRun] = {}
    for run in runs:
        if run.status is not LabRunStatus.UNREADABLE:
            latest.setdefault(run.suite, run)

    git = _Git(repo)
    since: dict[Suite, int | None] = {}
    matched_by: dict[Suite, Any] = {}
    unknown: dict[Suite, str] = {}
    for suite, run in latest.items():
        count, how = _commits_since(run, git, main)
        since[suite] = count
        if count is None:
            unknown[suite] = how if how not in ("ancestor", "tree") else "git could not count the commits"
        else:
            matched_by[suite] = how
    return LabEvidence(
        latest=latest,
        commits_on_main_since=since,
        matched_by=matched_by,
        commits_unknown_reason=unknown,
        runs=runs[:MAX_RUNS],
    )


class LabRunSource:
    """The recorded runs, read from disk on every request: a directory scan is cheap."""

    def __init__(self, runs_dir: Path, repo: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self._runs_dir = runs_dir
        self._repo = repo
        self._clock = clock or _utcnow

    async def refresh(self) -> View[LabEvidence]:
        import asyncio  # noqa: PLC0415

        now = self._clock()
        evidence = await asyncio.to_thread(read_lab_evidence, self._runs_dir, self._repo)
        if not evidence.runs:
            # No runs yet is a fact, not a failure: say how to make one.
            return View(source=SourceName.LAB_RUNS, available=True, fetched_at=now, remedy=RUN_REMEDY, data=evidence)
        return View(source=SourceName.LAB_RUNS, available=True, fetched_at=now, data=evidence)
