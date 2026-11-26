"""Reading recorded lab runs (spec 010 T042, contracts/lab-runs.md, research R10).

The records are the T039 fixtures, copied into a temporary folder with their
placeholder commits filled from a temporary git repository. The commit counts
are therefore computed by real git against real history, including the
squash-merge case that a plain ancestor test gets wrong.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from snapl_presentation.dashboard.lab_runs import LabRunSource, read_lab_evidence
from snapl_presentation.dashboard.models import LabRunStatus, SourceName

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent / "fixtures" / "lab_runs"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


class Repo:
    """A throwaway git repository with a `main` branch."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir()
        self.git("init", "-q", "-b", "main")
        self.commit("first")

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=self.path,
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, **GIT_ENV},
        )
        return result.stdout.strip()

    def commit(self, name: str, content: str | None = None) -> str:
        (self.path / name).write_text(content or name)
        self.git("add", name)
        self.git("commit", "-q", "-m", name)
        return self.git("rev-parse", "HEAD")

    def tree(self, commit: str) -> str:
        return self.git("rev-parse", f"{commit}^{{tree}}")


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path / "repo")


@pytest.fixture
def runs_dir(tmp_path: Path, repo: Repo) -> Path:
    """The T039 fixtures, with placeholders filled from the repo's first commit."""
    target = tmp_path / "test-runs"
    shutil.copytree(FIXTURES, target, ignore=shutil.ignore_patterns("README.md"))
    head = repo.git("rev-parse", "HEAD")
    for record in target.glob("*/run.json"):
        text = record.read_text()
        record.write_text(text.replace("COMMIT_PLACEHOLDER", head).replace("TREE_PLACEHOLDER", repo.tree(head)))
    # Truncated mid-write. Written here: the repository's check-json hook
    # refuses to commit a corrupt JSON file.
    corrupt = target / "20261106T110000Z-e2e"
    corrupt.mkdir()
    (corrupt / "run.json").write_text('{"format": 1, "suite": "e2e", "status": "pass')
    return target


def _write_run(runs_dir: Path, name: str, **fields) -> None:
    folder = runs_dir / name
    folder.mkdir(parents=True)
    record = {"format": 1, "suite": name.rsplit("-", 1)[1], "status": "passed", "started_at": "2026-11-07T10:00:00Z"}
    record |= fields
    (folder / "run.json").write_text(json.dumps({k: v for k, v in record.items() if v is not None}))


# --- statuses ------------------------------------------------------------------


def test_reads_every_record_newest_first(runs_dir: Path, repo: Repo):
    evidence = read_lab_evidence(runs_dir, repo.path)

    assert [run.id for run in evidence.runs] == [
        "20261106T120000Z-integration",
        "20261106T110000Z-e2e",
        "20261106T100000Z-e2e",
        "20261106T090000Z-integration",
        "20261105T090000Z-e2e",
    ]


def test_a_running_record_whose_process_is_gone_is_incomplete(runs_dir: Path, repo: Repo):
    runs = {run.id: run for run in read_lab_evidence(runs_dir, repo.path).runs}

    assert runs["20261106T100000Z-e2e"].status is LabRunStatus.INCOMPLETE


def test_a_running_record_whose_process_is_alive_is_running(runs_dir: Path, repo: Repo):
    _write_run(runs_dir, "20261107T000000Z-e2e", status="running", pid=os.getpid())

    runs = {run.id: run for run in read_lab_evidence(runs_dir, repo.path).runs}

    assert runs["20261107T000000Z-e2e"].status is LabRunStatus.RUNNING


def test_corrupt_and_future_records_are_unreadable_and_the_rest_still_listed(runs_dir: Path, repo: Repo):
    runs = {run.id: run for run in read_lab_evidence(runs_dir, repo.path).runs}

    corrupt = runs["20261106T110000Z-e2e"]
    future = runs["20261106T120000Z-integration"]
    assert corrupt.status is LabRunStatus.UNREADABLE
    assert corrupt.problem is not None
    assert future.status is LabRunStatus.UNREADABLE
    assert future.problem is not None
    assert "format 2" in future.problem
    assert runs["20261105T090000Z-e2e"].status is LabRunStatus.PASSED
    assert (runs["20261105T090000Z-e2e"].passed, runs["20261105T090000Z-e2e"].skipped) == (14, 1)
    assert runs["20261106T090000Z-integration"].dirty is True


def test_latest_is_the_newest_readable_run_per_suite(runs_dir: Path, repo: Repo):
    latest = read_lab_evidence(runs_dir, repo.path).latest

    assert latest["e2e"].id == "20261106T100000Z-e2e"
    assert latest["integration"].id == "20261106T090000Z-integration"


def test_folders_that_are_not_runs_are_ignored(runs_dir: Path, repo: Repo):
    (runs_dir / "notes").mkdir()
    (runs_dir / "20261107T000000Z-unit").mkdir()

    ids = [run.id for run in read_lab_evidence(runs_dir, repo.path).runs]

    assert "notes" not in ids
    assert "20261107T000000Z-unit" not in ids


def test_at_most_20_runs_are_listed(tmp_path: Path, repo: Repo):
    runs_dir = tmp_path / "many"
    for day in range(1, 26):
        _write_run(runs_dir, f"202611{day:02d}T000000Z-e2e")

    evidence = read_lab_evidence(runs_dir, repo.path)

    assert len(evidence.runs) == 20
    assert evidence.runs[0].id == "20261125T000000Z-e2e"


# --- commits on main since (research R10) --------------------------------------


def _evidence_for(tmp_path: Path, repo: Repo, **fields):
    runs_dir = tmp_path / "runs"
    _write_run(runs_dir, "20261107T100000Z-e2e", **fields)
    return read_lab_evidence(runs_dir, repo.path)


def test_a_run_on_an_ancestor_of_main_counts_the_commits_since(tmp_path: Path, repo: Repo):
    tested = repo.git("rev-parse", "HEAD")
    repo.commit("second")
    repo.commit("third")

    evidence = _evidence_for(tmp_path, repo, commit=tested, tree=repo.tree(tested))

    assert evidence.commits_on_main_since["e2e"] == 2
    assert evidence.matched_by["e2e"] == "ancestor"
    assert "e2e" not in evidence.commits_unknown_reason


def test_a_run_on_a_squash_merged_branch_is_matched_by_tree(tmp_path: Path, repo: Repo):
    repo.git("checkout", "-q", "-b", "feature")
    tested = repo.commit("feature-file")
    repo.git("checkout", "-q", "main")
    # Squash-merge: one commit on main with the branch head's tree.
    repo.git("checkout", "feature", "--", "feature-file")
    repo.git("commit", "-q", "-m", "squash")
    repo.commit("after-1")
    repo.commit("after-2")

    evidence = _evidence_for(tmp_path, repo, commit=tested, tree=repo.tree(tested))

    assert evidence.commits_on_main_since["e2e"] == 2
    assert evidence.matched_by["e2e"] == "tree"


def test_a_branch_commit_not_merged_yet_has_no_count(tmp_path: Path, repo: Repo):
    repo.git("checkout", "-q", "-b", "open-pr")
    tested = repo.commit("unmerged", "not on main")
    repo.git("checkout", "-q", "main")

    evidence = _evidence_for(tmp_path, repo, commit=tested, tree=repo.tree(tested))

    assert evidence.commits_on_main_since["e2e"] is None
    assert evidence.commits_unknown_reason["e2e"] == "commit not on main yet"
    assert "e2e" not in evidence.matched_by


def test_a_commit_missing_from_local_history_has_no_count(tmp_path: Path, repo: Repo):
    evidence = _evidence_for(tmp_path, repo, commit="0" * 40, tree="1" * 40)

    assert evidence.commits_on_main_since["e2e"] is None
    assert evidence.commits_unknown_reason["e2e"] == "commit not in local history"


def test_a_record_without_a_tree_and_no_ancestor_has_no_count(tmp_path: Path, repo: Repo):
    repo.git("checkout", "-q", "-b", "old")
    tested = repo.commit("old-record")
    repo.git("checkout", "-q", "main")

    evidence = _evidence_for(tmp_path, repo, commit=tested, tree=None)

    assert evidence.commits_on_main_since["e2e"] is None
    assert evidence.commits_unknown_reason["e2e"] == "run recorded without a tree"


# --- the source ----------------------------------------------------------------


async def test_missing_folder_is_available_with_no_runs_and_a_remedy(tmp_path: Path, repo: Repo):
    source = LabRunSource(tmp_path / "nowhere", repo.path, clock=lambda: datetime(2026, 11, 7, tzinfo=UTC))

    view = await source.refresh()

    assert view.source is SourceName.LAB_RUNS
    assert view.available is True
    assert view.remedy == "uv run invoke test-e2e"
    assert view.data is not None
    assert view.data.runs == []
    assert view.data.latest == {}
    assert not (tmp_path / "nowhere").exists()


async def test_source_reads_the_folder_on_every_refresh(runs_dir: Path, repo: Repo):
    source = LabRunSource(runs_dir, repo.path, clock=lambda: datetime(2026, 11, 7, tzinfo=UTC))

    first = await source.refresh()
    _write_run(runs_dir, "20261107T000000Z-e2e")
    second = await source.refresh()

    assert first.data is not None
    assert second.data is not None
    assert len(second.data.runs) == len(first.data.runs) + 1
    assert second.fetched_at == datetime(2026, 11, 7, tzinfo=UTC)


# --- without a git history (spec 011 archive mode, research R13) -------------------


def test_without_a_repo_every_count_is_unknown_with_the_reason_and_git_is_not_run(
    runs_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    def no_git(*_args, **_kwargs):
        raise AssertionError("git must not run without a repo")

    monkeypatch.setattr(subprocess, "run", no_git)

    evidence = read_lab_evidence(runs_dir, None, no_history="an archive has no git history")

    assert evidence.latest
    assert set(evidence.commits_on_main_since.values()) == {None}
    assert set(evidence.commits_unknown_reason.values()) == {"an archive has no git history"}
    assert evidence.matched_by == {}


async def test_the_source_can_read_runs_with_no_repo(runs_dir: Path):
    view = await LabRunSource(runs_dir, None, no_history="an archive has no git history").refresh()

    assert view.available is True
    assert set(view.data.commits_unknown_reason.values()) == {"an archive has no git history"}
