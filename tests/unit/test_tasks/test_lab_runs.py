"""Recording live lab runs (spec 010 T040, contracts/lab-runs.md).

`record_run` wraps the pytest command of `invoke test-e2e` and `invoke
test-integration`. The command is a stub here: it inspects what was written
before it ran, writes a JUnit file where pytest would, and exits with a chosen
code. Nothing touches the lab.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from invoke import Context
from invoke.exceptions import Exit

from tasks import lab_runs, main, ns

pytestmark = pytest.mark.unit

JUNIT = (
    '<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">'
    '<testsuite name="pytest" errors="1" failures="2" skipped="3" tests="20" time="1.0"></testsuite></testsuites>'
)
HEX40 = re.compile(r"^[0-9a-f]{40}$")


@pytest.fixture
def runs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "test-runs"
    monkeypatch.setenv("SNAPL_TEST_RUNS_DIR", str(path))
    return path


def _ctx(*, exit_code: int = 0, junit: str | None = JUNIT, check=None, raises: BaseException | None = None):
    """An invoke context whose `run` stands in for pytest."""
    ctx = MagicMock(spec=Context)

    def run(command: str, **kwargs):
        ctx.command = command
        if check is not None:
            check(command)
        if raises is not None:
            raise raises
        junit_path = re.search(r"--junitxml=(\S+)", command)
        if junit is not None and junit_path:
            Path(junit_path.group(1)).write_text(junit, encoding="utf-8")
        return SimpleNamespace(exited=exit_code)

    ctx.run.side_effect = run
    return ctx


def _record(runs_dir: Path) -> dict:
    [folder] = list(runs_dir.iterdir())
    return json.loads((folder / "run.json").read_text())


def test_run_json_is_written_before_pytest_starts(runs_dir: Path):
    seen: dict = {}

    def check(command: str) -> None:
        seen.update(_record(runs_dir))

    lab_runs.record_run(_ctx(check=check), "e2e", "pytest tests/e2e -m e2e -v")

    assert seen["format"] == 1
    assert seen["suite"] == "e2e"
    assert seen["status"] == "running"
    assert HEX40.match(seen["commit"])
    assert HEX40.match(seen["tree"])
    assert seen["branch"] is None or isinstance(seen["branch"], str)
    assert isinstance(seen["dirty"], bool)
    assert seen["device_image"] == "ghcr.io/nokia/srlinux:26.3.1"
    assert isinstance(seen["pid"], int)
    assert seen["started_at"].endswith("Z")
    assert "ended_at" not in seen
    assert "exit_code" not in seen


def test_the_folder_is_named_for_the_time_and_suite(runs_dir: Path):
    lab_runs.record_run(_ctx(), "integration", "pytest tests/integration")

    [folder] = list(runs_dir.iterdir())
    assert re.match(r"^\d{8}T\d{6}Z-integration$", folder.name)


def test_pytest_writes_junit_into_the_run_folder(runs_dir: Path):
    ctx = _ctx()

    lab_runs.record_run(ctx, "e2e", "pytest tests/e2e -m e2e -v")

    [folder] = list(runs_dir.iterdir())
    assert ctx.command == f"pytest tests/e2e -m e2e -v --junitxml={folder / 'junit.xml'}"


def test_the_end_rewrites_the_record_with_status_and_counts(runs_dir: Path):
    code = lab_runs.record_run(_ctx(exit_code=0), "e2e", "pytest tests/e2e")

    record = _record(runs_dir)
    assert code == 0
    assert record["status"] == "passed"
    assert record["exit_code"] == 0
    assert record["ended_at"].endswith("Z")
    assert record["counts"] == {"passed": 14, "failed": 2, "skipped": 3, "errors": 1}
    assert not list(runs_dir.glob("*/run.json.tmp"))


@pytest.mark.parametrize(
    ("exit_code", "status"),
    [(0, "passed"), (1, "failed"), (2, "interrupted"), (3, "failed"), (5, "failed")],
)
def test_status_follows_pytests_exit_code(runs_dir: Path, exit_code: int, status: str):
    assert lab_runs.record_run(_ctx(exit_code=exit_code), "e2e", "pytest") == exit_code

    assert _record(runs_dir)["status"] == status


def test_ctrl_c_is_recorded_as_interrupted_and_still_raised(runs_dir: Path):
    with pytest.raises(KeyboardInterrupt):
        lab_runs.record_run(_ctx(raises=KeyboardInterrupt()), "e2e", "pytest")

    record = _record(runs_dir)
    assert record["status"] == "interrupted"
    assert "ended_at" in record


def test_missing_junit_leaves_counts_out(runs_dir: Path):
    lab_runs.record_run(_ctx(junit=None), "e2e", "pytest")

    assert "counts" not in _record(runs_dir)


def test_unparseable_junit_leaves_counts_out(runs_dir: Path):
    lab_runs.record_run(_ctx(junit="<not xml"), "e2e", "pytest")

    assert "counts" not in _record(runs_dir)


def test_an_unwritable_folder_warns_and_keeps_pytests_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("a file where the folder should be")
    monkeypatch.setenv("SNAPL_TEST_RUNS_DIR", str(blocker / "test-runs"))
    ctx = _ctx(exit_code=1)

    code = lab_runs.record_run(ctx, "e2e", "pytest tests/e2e")

    assert code == 1
    assert ctx.command == "pytest tests/e2e"
    assert "warning: could not record this lab run" in capsys.readouterr().out


def test_the_last_line_names_the_record(runs_dir: Path, capsys: pytest.CaptureFixture[str]):
    lab_runs.record_run(_ctx(), "e2e", "pytest")

    [folder] = list(runs_dir.iterdir())
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last == f"Lab run recorded: {folder} (passed: 14, failed: 2, skipped: 3)"


def test_the_last_line_without_counts(runs_dir: Path, capsys: pytest.CaptureFixture[str]):
    lab_runs.record_run(_ctx(junit=None), "e2e", "pytest")

    [folder] = list(runs_dir.iterdir())
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last == f"Lab run recorded: {folder} (no test counts)"


# --- the invoke tasks ----------------------------------------------------------


def test_test_e2e_records_its_run_with_the_unchanged_command():
    with patch.object(main, "record_run", return_value=0) as record:
        main.test_e2e(MagicMock(spec=Context))

    record.assert_called_once()
    _ctx_arg, suite, command = record.call_args.args
    assert (suite, command) == ("e2e", "SNAPL_E2E=1 pytest tests/e2e -m e2e -v")


def test_test_integration_runs_the_integration_suite():
    with patch.object(main, "record_run", return_value=0) as record:
        main.test_integration(MagicMock(spec=Context))

    _ctx_arg, suite, command = record.call_args.args
    assert (suite, command) == ("integration", "pytest tests/integration -m integration -v")
    assert "test-integration" in ns.task_names


def test_a_failed_run_fails_the_task_with_pytests_code():
    with patch.object(main, "record_run", return_value=1), pytest.raises(Exit) as exited:
        main.test_e2e(MagicMock(spec=Context))

    assert exited.value.code == 1


def test_the_last_line_is_relative_to_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SNAPL_TEST_RUNS_DIR", str(tmp_path / ".snapl" / "test-runs"))

    lab_runs.record_run(_ctx(), "e2e", "pytest")

    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert re.match(r"^Lab run recorded: \.snapl/test-runs/\d{8}T\d{6}Z-e2e \(passed: 14", last), last
