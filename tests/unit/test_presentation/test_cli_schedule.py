"""Command-level tests for `snapl schedule` (spec 009, contracts/cli-schedule.md).

The schedule functions are mocked: their Temporal behaviour is tested in
test_orchestrator/test_schedules.py. What is asserted here is the CLI's
contract: what it refuses before touching Temporal, which function it calls
with what, its exit code, and that stdout carries only JSON under --json.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from snapl_intent.infrahub.identity import IdentityCheck, SotIdentity
from snapl_orchestrator.exceptions import ScheduleNotFoundError
from snapl_orchestrator.models import DriftResponseMode, DriftScheduleView
from snapl_presentation import cli
from snapl_presentation.exit_codes import ExitCode

pytestmark = pytest.mark.unit

runner = CliRunner()

_SCHEDULES = "snapl_orchestrator.schedules"


def _view(*, mode: DriftResponseMode = DriftResponseMode.REPORT, paused: bool = False, note: str | None = None):
    return DriftScheduleView(
        schedule_id="drift-response-dcfabric",
        use_case_id="dcfabric",
        interval=timedelta(minutes=5),
        mode=mode,
        paused=paused,
        note=note,
        next_runs=[datetime(2026, 10, 28, 3, 5, tzinfo=UTC)],
    )


@pytest.fixture
def audit_log():
    log = MagicMock()
    log.close = AsyncMock()
    with patch.object(cli, "_open_audit_log", new=AsyncMock(return_value=log)):
        yield log


@pytest.fixture
def temporal():
    client = MagicMock()
    with (
        patch.object(cli, "_connect", new=AsyncMock(return_value=client)) as connect,
        patch.object(cli, "count_pollers", new=AsyncMock(return_value=1)),
    ):
        yield connect


@pytest.fixture
def sot_identity():
    """The SoT identifies as snapl's unless a test says otherwise."""
    with (
        patch.object(cli, "build_store", return_value=MagicMock()),
        patch.object(
            cli,
            "identify_sot",
            new=AsyncMock(
                return_value=IdentityCheck(SotIdentity.OURS, "http://localhost:18000 carries snapl's schema")
            ),
        ) as identify,
    ):
        yield identify


@pytest.fixture
def ensure():
    with patch(f"{_SCHEDULES}.ensure_drift_schedule", new=AsyncMock(return_value=_view())) as mock:
        yield mock


@pytest.fixture
def describe():
    with patch(f"{_SCHEDULES}.describe_drift_schedule", new=AsyncMock(return_value=_view())) as mock:
        yield mock


def _invoke(args: list[str]):
    return runner.invoke(cli.app, ["schedule", *args])


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


class TestCreate:
    def test_creates_at_the_default_interval_as_the_os_user(self, audit_log, temporal, sot_identity, ensure) -> None:
        with patch.object(cli.getpass, "getuser", return_value="anton"):
            result = _invoke(["create"])

        assert result.exit_code == ExitCode.OK, result.output
        kwargs = ensure.call_args.kwargs
        assert kwargs["use_case_id"] == "dcfabric"
        assert kwargs["interval"] == timedelta(minutes=5)
        assert kwargs["actor"] == "anton"
        assert kwargs["audit_log"] is audit_log
        assert kwargs["task_queue"] == "snapl-orchestrator"
        assert "drift-response-dcfabric" in result.output
        audit_log.close.assert_awaited()

    @pytest.mark.parametrize(
        ("every", "expected"),
        [("90s", timedelta(seconds=90)), ("5m", timedelta(minutes=5)), ("1h", timedelta(hours=1))],
    )
    def test_parses_every(self, audit_log, temporal, sot_identity, ensure, every, expected) -> None:
        result = _invoke(["create", "--every", every])
        assert result.exit_code == ExitCode.OK, result.output
        assert ensure.call_args.kwargs["interval"] == expected

    @pytest.mark.parametrize("every", ["abc", "5", "5d", "30s"])
    def test_rejects_a_bad_or_too_short_interval(self, audit_log, temporal, sot_identity, ensure, every) -> None:
        result = _invoke(["create", "--every", every])
        assert result.exit_code == ExitCode.ERROR
        assert "error:" in result.output
        ensure.assert_not_called()
        temporal.assert_not_called()

    def test_refuses_a_foreign_source_of_truth_before_temporal(self, audit_log, temporal, sot_identity, ensure) -> None:
        sot_identity.return_value = IdentityCheck(SotIdentity.FOREIGN, "this looks like another project's Infrahub")

        result = _invoke(["create"])

        assert result.exit_code == ExitCode.ERROR
        assert "another project's Infrahub" in " ".join(result.output.split())
        temporal.assert_not_called()
        ensure.assert_not_called()

    def test_an_unidentifiable_source_of_truth_does_not_refuse(self, audit_log, temporal, sot_identity, ensure) -> None:
        sot_identity.return_value = IdentityCheck(SotIdentity.UNKNOWN, "schema could not be read")
        assert _invoke(["create"]).exit_code == ExitCode.OK
        ensure.assert_awaited()

    def test_refuses_a_missing_audit_db_before_temporal(
        self, tmp_path, monkeypatch, temporal, sot_identity, ensure
    ) -> None:
        monkeypatch.setenv("SNAPL_AUDIT_DB", str(tmp_path / "absent.sqlite"))

        result = _invoke(["create"])

        assert result.exit_code == ExitCode.ERROR
        assert "no audit log at" in result.output
        assert "SNAPL_AUDIT_DB" in result.output
        temporal.assert_not_called()
        ensure.assert_not_called()

    def test_warns_when_no_worker_is_polling_but_still_creates(self, audit_log, temporal, sot_identity, ensure) -> None:
        with patch.object(cli, "count_pollers", new=AsyncMock(return_value=0)):
            result = _invoke(["create"])
        assert result.exit_code == ExitCode.OK
        assert "no worker is polling" in result.output
        ensure.assert_awaited()

    def test_json_emits_only_the_view(self, audit_log, temporal, sot_identity, ensure) -> None:
        result = runner.invoke(cli.app, ["schedule", "create", "--json"])
        assert result.exit_code == ExitCode.OK
        assert json.loads(result.stdout)["interval"] == 300


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


class TestShow:
    def test_shows_the_schedule(self, temporal, describe) -> None:
        result = _invoke(["show"])
        assert result.exit_code == ExitCode.OK, result.output
        assert describe.call_args.kwargs["use_case_id"] == "dcfabric"
        assert "report" in result.output

    def test_json_is_valid_json_on_stdout(self, temporal, describe) -> None:
        result = _invoke(["show", "--json"])
        assert result.exit_code == ExitCode.OK
        payload = json.loads(result.stdout)
        assert payload["mode"] == "report"
        assert payload["use_case_id"] == "dcfabric"

    def test_a_missing_schedule_exits_1_with_one_line(self, temporal, describe) -> None:
        describe.side_effect = ScheduleNotFoundError("no drift schedule for use case 'dcfabric'")
        result = _invoke(["show"])
        assert result.exit_code == ExitCode.ERROR
        assert "no drift schedule" in result.output
        assert "snapl schedule create" in result.output
        assert "Traceback" not in result.output

    def test_never_requires_a_worker(self, temporal, describe) -> None:
        with (
            patch.object(cli, "count_pollers", new=AsyncMock(return_value=0)),
            patch.object(cli, "_require_worker", new=AsyncMock(side_effect=AssertionError("must not be called"))),
        ):
            result = _invoke(["show"])
        assert result.exit_code == ExitCode.OK


# ---------------------------------------------------------------------------
# pause / resume / trigger / delete (US2)
# ---------------------------------------------------------------------------


@pytest.fixture
def control():
    """Mock every mutating schedule function."""
    with (
        patch(f"{_SCHEDULES}.pause_drift_schedule", new=AsyncMock()) as pause,
        patch(f"{_SCHEDULES}.resume_drift_schedule", new=AsyncMock()) as resume,
        patch(f"{_SCHEDULES}.trigger_drift_schedule", new=AsyncMock(return_value=DriftResponseMode.REPORT)) as trig,
        patch(f"{_SCHEDULES}.delete_drift_schedule", new=AsyncMock()) as delete,
    ):
        yield SimpleNamespace(pause=pause, resume=resume, trigger=trig, delete=delete)


class TestPause:
    def test_pauses_with_the_reason(self, audit_log, temporal, control) -> None:
        with patch.object(cli.getpass, "getuser", return_value="anton"):
            result = _invoke(["pause", "--reason", "maintenance: spine swap"])
        assert result.exit_code == ExitCode.OK, result.output
        kwargs = control.pause.call_args.kwargs
        assert kwargs["reason"] == "maintenance: spine swap"
        assert kwargs["actor"] == "anton"
        assert kwargs["audit_log"] is audit_log
        assert "paused" in result.output

    @pytest.mark.parametrize("args", [[], ["--reason", "  "]])
    def test_requires_a_reason(self, audit_log, temporal, control, args) -> None:
        result = _invoke(["pause", *args])
        assert result.exit_code == ExitCode.ERROR
        assert "--reason" in result.output
        control.pause.assert_not_called()
        temporal.assert_not_called()

    def test_a_missing_schedule_exits_1(self, audit_log, temporal, control) -> None:
        control.pause.side_effect = ScheduleNotFoundError("none")
        result = _invoke(["pause", "--reason", "r"])
        assert result.exit_code == ExitCode.ERROR
        assert "no drift schedule for dcfabric" in result.output


class TestResume:
    def test_resumes_with_an_optional_note(self, audit_log, temporal, control) -> None:
        result = _invoke(["resume", "--note", "swap done"])
        assert result.exit_code == ExitCode.OK, result.output
        assert control.resume.call_args.kwargs["note"] == "swap done"

    def test_note_defaults_to_none(self, audit_log, temporal, control) -> None:
        assert _invoke(["resume"]).exit_code == ExitCode.OK
        assert control.resume.call_args.kwargs["note"] is None

    def test_a_missing_schedule_exits_1(self, audit_log, temporal, control) -> None:
        control.resume.side_effect = ScheduleNotFoundError("none")
        assert _invoke(["resume"]).exit_code == ExitCode.ERROR


class TestTrigger:
    def test_triggers_a_report_schedule_without_asking(self, audit_log, temporal, describe, control) -> None:
        result = _invoke(["trigger"])
        assert result.exit_code == ExitCode.OK, result.output
        control.trigger.assert_awaited_once()
        assert "drift-response-dcfabric-" in result.output

    def test_a_missing_schedule_exits_1(self, audit_log, temporal, describe, control) -> None:
        describe.side_effect = ScheduleNotFoundError("none")
        result = _invoke(["trigger"])
        assert result.exit_code == ExitCode.ERROR
        control.trigger.assert_not_called()


class TestDelete:
    def test_prompts_and_deletes_on_yes(self, audit_log, temporal, control) -> None:
        result = runner.invoke(cli.app, ["schedule", "delete"], input="y\n")
        assert result.exit_code == ExitCode.OK, result.output
        control.delete.assert_awaited_once()

    def test_an_aborted_prompt_deletes_nothing(self, audit_log, temporal, control) -> None:
        result = runner.invoke(cli.app, ["schedule", "delete"], input="n\n")
        assert result.exit_code == ExitCode.ERROR
        control.delete.assert_not_called()

    def test_yes_skips_the_prompt(self, audit_log, temporal, control) -> None:
        assert _invoke(["delete", "--yes"]).exit_code == ExitCode.OK
        control.delete.assert_awaited_once()

    def test_a_missing_schedule_exits_1(self, audit_log, temporal, control) -> None:
        control.delete.side_effect = ScheduleNotFoundError("none")
        assert _invoke(["delete", "--yes"]).exit_code == ExitCode.ERROR


@pytest.mark.parametrize(
    "args",
    [["pause", "--reason", "r"], ["resume"], ["trigger"], ["delete", "--yes"], ["create"]],
)
def test_no_schedule_command_requires_a_worker(args, audit_log, temporal, sot_identity, ensure, describe, control):
    with patch.object(cli, "_require_worker", new=AsyncMock(side_effect=AssertionError("must not be called"))):
        result = _invoke(args)
    assert result.exit_code == ExitCode.OK, result.output
