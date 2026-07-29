"""Command-level tests for the snapl CLI (spec 006).

Every test runs against mocked clients — no Temporal cluster, no SoT, no
devices (FR-017). What is asserted is the CLI's contract with the world: which
workflow it starts with which id, what exit code it returns, and that an
anticipated failure produces a message instead of a traceback (SC-003).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from snapl_orchestrator.models import WorkflowReason
from snapl_presentation import cli
from snapl_presentation.exit_codes import ExitCode

pytestmark = pytest.mark.unit

runner = CliRunner()


@pytest.fixture(autouse=True)
def _worker_present():
    """Assume a polling worker unless a test says otherwise.

    Without this the poller check would run against a MagicMock client in every
    test; the tests that care about the no-worker path override it explicitly.
    """
    with patch.object(cli, "count_pollers", new=AsyncMock(return_value=1)):
        yield


@pytest.fixture
def sot(states):
    """Patch SoT access so no command reaches a real Infrahub."""
    with (
        patch.object(cli, "build_store", return_value=MagicMock()),
        patch.object(cli, "load_states", new=AsyncMock(return_value=states)),
    ):
        yield states


def _invoke(args: list[str]):
    return runner.invoke(cli.app, args)


# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------


class TestDeploy:
    def test_starts_the_workflow_with_the_documented_id(
        self, sot, device_ids, workflow_result, temporal_client
    ) -> None:
        """FR-015: the CLI must not route around per-device serialization by
        minting a unique id — it uses the same `deploy-intent-<id>` family the
        Orchestrator and the operator entry point use."""
        client = temporal_client(workflow_result())
        with patch.object(cli, "_connect", new=AsyncMock(return_value=client)):
            result = _invoke(["deploy", "spine-01"])

        assert result.exit_code == ExitCode.OK
        kwargs = client.execute_workflow.call_args.kwargs
        assert kwargs["id"] == f"deploy-intent-{device_ids['spine-01']}"
        assert client.execute_workflow.call_args.args[1] == device_ids["spine-01"]

    def test_resolves_the_name_to_an_id(self, sot, device_ids, workflow_result, temporal_client) -> None:
        """FR-007: operators name devices; UUIDs are the CLI's problem."""
        client = temporal_client(workflow_result())
        with patch.object(cli, "_connect", new=AsyncMock(return_value=client)):
            _invoke(["deploy", "leaf-01"])
        assert client.execute_workflow.call_args.args[1] == device_ids["leaf-01"]

    def test_failed_deploy_exits_non_zero(self, sot, workflow_result, temporal_client) -> None:
        client = temporal_client(workflow_result(success=False, reason=WorkflowReason.APPLY_FAILED))
        with patch.object(cli, "_connect", new=AsyncMock(return_value=client)):
            result = _invoke(["deploy", "spine-01"])
        assert result.exit_code == ExitCode.ERROR

    def test_unknown_device_never_starts_a_workflow(self, sot, temporal_client) -> None:
        """US1 scenario 3 — refuse before touching the cluster."""
        client = temporal_client()
        with patch.object(cli, "_connect", new=AsyncMock(return_value=client)):
            result = _invoke(["deploy", "ghost"])

        assert result.exit_code == ExitCode.ERROR
        assert "ghost" in result.output
        client.execute_workflow.assert_not_called()

    def test_unknown_device_lists_the_known_ones(self, sot) -> None:
        result = _invoke(["deploy", "ghost"])
        assert "spine-01" in result.output


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


class TestScan:
    def test_clean_fabric_exits_zero(self, scan_result, temporal_client) -> None:
        client = temporal_client(scan_result())
        with patch.object(cli, "_connect", new=AsyncMock(return_value=client)):
            result = _invoke(["scan"])
        assert result.exit_code == ExitCode.OK

    def test_drift_exits_two(self, scan_result, temporal_client) -> None:
        client = temporal_client(scan_result(drifted=1))
        with patch.object(cli, "_connect", new=AsyncMock(return_value=client)):
            result = _invoke(["scan"])
        assert result.exit_code == ExitCode.DRIFT

    def test_errored_device_exits_one(self, scan_result, temporal_client) -> None:
        client = temporal_client(scan_result(errored=1))
        with patch.object(cli, "_connect", new=AsyncMock(return_value=client)):
            result = _invoke(["scan"])
        assert result.exit_code == ExitCode.ERROR

    def test_json_mode_emits_parseable_stdout(self, scan_result, temporal_client) -> None:
        import json

        client = temporal_client(scan_result(drifted=1))
        with patch.object(cli, "_connect", new=AsyncMock(return_value=client)):
            result = _invoke(["scan", "--json"])
        assert json.loads(result.stdout)["drifted"] == 1

    def test_passes_the_use_case_through(self, scan_result, temporal_client) -> None:
        client = temporal_client(scan_result())
        with patch.object(cli, "_connect", new=AsyncMock(return_value=client)):
            _invoke(["scan", "--use-case", "test_edge"])
        assert client.execute_workflow.call_args.args[1] == "test_edge"


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------


class TestReconcile:
    def test_named_devices_reconcile_after_confirmation(
        self, sot, device_ids, reconcile_result, temporal_client
    ) -> None:
        client = temporal_client(reconcile_result(succeeded=1))
        with patch.object(cli, "_connect", new=AsyncMock(return_value=client)):
            result = _invoke(["reconcile", "spine-01", "--yes"])

        assert result.exit_code == ExitCode.OK
        assert client.execute_workflow.call_args.args[1] == [device_ids["spine-01"]]

    def test_drifted_flag_scans_first_then_reconciles_only_the_drifted(
        self, scan_result, reconcile_result, device_ids, temporal_client
    ) -> None:
        """US3 scenario 2 — the scan feeds the reconcile target list."""
        client = temporal_client(scan_result(drifted=1), reconcile_result(succeeded=1))
        with patch.object(cli, "_connect", new=AsyncMock(return_value=client)):
            result = _invoke(["reconcile", "--drifted", "--yes"])

        assert result.exit_code == ExitCode.OK
        assert client.execute_workflow.call_count == 2
        assert client.execute_workflow.call_args.args[1] == [device_ids["spine-01"]]

    def test_drifted_flag_with_a_clean_fabric_does_nothing(self, scan_result, temporal_client) -> None:
        client = temporal_client(scan_result())
        with patch.object(cli, "_connect", new=AsyncMock(return_value=client)):
            result = _invoke(["reconcile", "--drifted", "--yes"])

        assert result.exit_code == ExitCode.OK
        assert client.execute_workflow.call_count == 1, "scanned, then stopped — no reconcile workflow"

    def test_naming_devices_and_drifted_together_is_refused(self, sot) -> None:
        result = _invoke(["reconcile", "spine-01", "--drifted", "--yes"])
        assert result.exit_code == ExitCode.ERROR

    def test_neither_devices_nor_drifted_is_refused(self, sot) -> None:
        result = _invoke(["reconcile", "--yes"])
        assert result.exit_code == ExitCode.ERROR

    def test_json_without_yes_refuses_rather_than_blocking(self, sot, temporal_client) -> None:
        """FR-012: a JSON consumer cannot answer a prompt, so refuse loudly
        instead of waiting on stdin nobody is attached to."""
        client = temporal_client()
        with patch.object(cli, "_connect", new=AsyncMock(return_value=client)):
            result = _invoke(["reconcile", "spine-01", "--json"])

        assert result.exit_code == ExitCode.ERROR
        client.execute_workflow.assert_not_called()

    def test_declining_the_prompt_aborts(self, sot, temporal_client) -> None:
        client = temporal_client()
        with patch.object(cli, "_connect", new=AsyncMock(return_value=client)):
            result = runner.invoke(cli.app, ["reconcile", "spine-01"], input="n\n")

        assert result.exit_code == ExitCode.ERROR
        client.execute_workflow.assert_not_called()

    def test_failed_device_exits_non_zero(self, sot, reconcile_result, temporal_client) -> None:
        client = temporal_client(reconcile_result(succeeded=1, failed=1))
        with patch.object(cli, "_connect", new=AsyncMock(return_value=client)):
            result = _invoke(["reconcile", "spine-01", "--yes"])
        assert result.exit_code == ExitCode.ERROR

    def test_skips_alone_still_exit_zero(self, sot, reconcile_result, temporal_client) -> None:
        client = temporal_client(reconcile_result(succeeded=1, skipped=1))
        with patch.object(cli, "_connect", new=AsyncMock(return_value=client)):
            result = _invoke(["reconcile", "spine-01", "--yes"])
        assert result.exit_code == ExitCode.OK


# ---------------------------------------------------------------------------
# Failure presentation — SC-003: no tracebacks for anticipated failures
# ---------------------------------------------------------------------------


class TestFailurePresentation:
    def test_temporal_unreachable_names_the_address_and_env_var(self, sot, scan_result) -> None:
        from snapl_presentation.exceptions import ConnectionCliError

        error = ConnectionCliError(
            subsystem="Temporal",
            address="localhost:7233",
            env_var="TEMPORAL_HOST",
            cause="connection refused",
        )
        with patch.object(cli, "_connect", new=AsyncMock(side_effect=error)):
            result = _invoke(["scan"])

        assert result.exit_code == ExitCode.ERROR
        assert "localhost:7233" in result.output
        assert "TEMPORAL_HOST" in result.output
        assert "Traceback" not in result.output

    def test_a_dead_cluster_fails_fast_instead_of_hanging(self, monkeypatch) -> None:
        """Temporal's client retries its initial handshake forever, so without
        our own cap `snapl scan` against a stopped cluster hangs with no output
        — the opposite of FR-014. Verified by pointing at a black-hole address
        with a short timeout and requiring a prompt, useful failure."""
        import time

        monkeypatch.setenv("TEMPORAL_HOST", "203.0.113.1:7233")  # TEST-NET-3, never routes
        monkeypatch.setenv("SNAPL_CONNECT_TIMEOUT", "1")

        started = time.monotonic()
        result = _invoke(["scan"])
        elapsed = time.monotonic() - started

        assert result.exit_code == ExitCode.ERROR
        assert elapsed < 15, f"took {elapsed:.1f}s — the connect timeout is not bounding this"
        assert "203.0.113.1:7233" in result.output
        assert "TEMPORAL_HOST" in result.output

    def test_no_worker_polling_refuses_instead_of_hanging(self, scan_result, temporal_client) -> None:
        """US1 scenario 4. Temporal accepts a workflow with no worker polling —
        it sits in the queue and execute_workflow blocks forever, which to an
        operator is indistinguishable from a hang. It is also the likeliest way
        to be stuck mid-demo: the stack is up, the worker terminal is not."""
        client = temporal_client(scan_result())
        with (
            patch.object(cli, "_connect", new=AsyncMock(return_value=client)),
            patch.object(cli, "count_pollers", new=AsyncMock(return_value=0)),
        ):
            result = _invoke(["scan"])

        assert result.exit_code == ExitCode.ERROR
        assert "no worker" in result.output
        assert "orchestrator.start" in result.output
        client.execute_workflow.assert_not_called()

    def test_a_polled_queue_proceeds(self, scan_result, temporal_client) -> None:
        client = temporal_client(scan_result())
        with (
            patch.object(cli, "_connect", new=AsyncMock(return_value=client)),
            patch.object(cli, "count_pollers", new=AsyncMock(return_value=1)),
        ):
            result = _invoke(["scan"])

        assert result.exit_code == ExitCode.OK
        client.execute_workflow.assert_called_once()

    def test_poller_introspection_failure_does_not_block_real_work(self, scan_result, temporal_client) -> None:
        """The check is a courtesy. If DescribeTaskQueue is unavailable (older
        server, restricted permissions) the command must still run."""
        client = temporal_client(scan_result())
        with (
            patch.object(cli, "_connect", new=AsyncMock(return_value=client)),
            patch.object(cli, "count_pollers", new=AsyncMock(side_effect=RuntimeError("unimplemented"))),
        ):
            result = _invoke(["scan"])

        assert result.exit_code == ExitCode.OK
        client.execute_workflow.assert_called_once()

    def test_audit_requires_exactly_one_selector(self) -> None:
        assert _invoke(["audit"]).exit_code == ExitCode.ERROR
        assert _invoke(["audit", "--workflow", "w", "--device", "spine-01"]).exit_code == ExitCode.ERROR

    def test_missing_audit_db_is_reported_not_invented(self, tmp_path, monkeypatch) -> None:
        """An absent database is not an empty one — reporting "0 events" for a
        path never written would be indistinguishable from a real empty log."""
        monkeypatch.setenv("SNAPL_AUDIT_DB", str(tmp_path / "nope.sqlite"))
        result = _invoke(["audit", "--workflow", "deploy-intent-x"])

        assert result.exit_code == ExitCode.ERROR
        assert "no audit log" in result.output


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


class TestWiring:
    def test_help_lists_every_command(self) -> None:
        output = _invoke(["--help"]).output
        for command in ("deploy", "scan", "reconcile", "audit", "status"):
            assert command in output

    def test_console_entry_point_is_declared(self) -> None:
        import tomllib
        from pathlib import Path

        pyproject = Path(cli.__file__).parents[1] / "pyproject.toml"
        scripts = tomllib.loads(pyproject.read_text())["project"]["scripts"]
        assert scripts["snapl"] == "snapl_presentation.cli:main"

    def test_settings_defaults_match_the_worker(self, monkeypatch) -> None:
        """FR-009: a CLI pointed at a different cluster than the worker
        succeeds at everything except doing anything."""
        from snapl_intent.infrahub.client import DEFAULT_ADDRESS
        from snapl_orchestrator.worker.run import DEFAULT_TASK_QUEUE, DEFAULT_TEMPORAL_HOST
        from snapl_presentation.settings import CliSettings

        for key in ("TEMPORAL_HOST", "TEMPORAL_TASK_QUEUE", "INFRAHUB_ADDRESS"):
            monkeypatch.delenv(key, raising=False)

        settings = CliSettings.from_env()
        assert settings.temporal_host == DEFAULT_TEMPORAL_HOST
        assert settings.task_queue == DEFAULT_TASK_QUEUE
        assert settings.infrahub_address == DEFAULT_ADDRESS


class TestStatus:
    def test_reports_poller_count_not_queue_existence(self, sot, temporal_client) -> None:
        """US5 scenario 3. A task queue nothing polls looks identical to a
        healthy one until a workflow sits in it forever, so status must probe
        for actual pollers."""
        client = temporal_client()
        with (
            patch.object(cli, "_connect", new=AsyncMock(return_value=client)),
            patch.object(cli, "count_pollers", new=AsyncMock(return_value=0)),
            patch.object(cli, "list_running_workflows", new=AsyncMock(return_value=[]), create=True),
        ):
            result = _invoke(["status"])

        assert result.exit_code == ExitCode.ERROR
        assert "nothing polling" in result.output
        assert "snapl-orchestrator" in result.output

    def test_healthy_environment_exits_zero(self, sot, temporal_client) -> None:
        client = temporal_client()
        with (
            patch.object(cli, "_connect", new=AsyncMock(return_value=client)),
            patch.object(cli, "count_pollers", new=AsyncMock(return_value=1)),
            patch.object(cli, "list_running_workflows", new=AsyncMock(return_value=[]), create=True),
        ):
            result = _invoke(["status"])

        assert result.exit_code == ExitCode.OK


class TestErrorMessageBrevity:
    def test_multiline_sdk_errors_are_reduced_to_one_line(self) -> None:
        """SDK errors embed whole GraphQL documents; pasting that into a
        terminal buries the one fact the operator needs (FR-014)."""
        from snapl_presentation.exceptions import ConnectionCliError, first_line

        graphql_blob = "Infrahub error: query failed\nquery {\n  DcimDevice(...) {\n    count\n  }\n}"
        assert first_line(graphql_blob) == "Infrahub error: query failed"

        error = ConnectionCliError(
            subsystem="Source of Truth",
            address="http://localhost:8000",
            env_var="INFRAHUB_ADDRESS",
            cause=graphql_blob,
        )
        assert "\n" not in error.message
        assert "DcimDevice" not in error.message

    def test_very_long_single_lines_are_clipped(self) -> None:
        from snapl_presentation.exceptions import first_line

        assert len(first_line("x" * 500)) <= 161
