"""`snapl dashboard` (spec 010 T018, contracts/cli-dashboard.md).

The server itself is patched out: what is pinned here is how the command picks
its address, what it refuses before serving, and what it tells the operator.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from snapl_presentation import cli
from snapl_presentation.exit_codes import ExitCode

pytestmark = pytest.mark.unit

runner = CliRunner()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def serve():
    """The uvicorn server, patched: records (use_case, host, port)."""
    with patch.object(cli, "_serve_dashboard") as fake:
        yield fake


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DASHBOARD_PORT", raising=False)
    monkeypatch.delenv("DASHBOARD_HOST", raising=False)


def _served(serve) -> tuple[str, str, int]:
    serve.assert_called_once()
    _settings, use_case, host, port = serve.call_args.args
    return use_case, host, port


def test_defaults_to_loopback_and_the_registry_port(serve):
    with patch.object(cli, "_port_in_use", return_value=False):
        result = runner.invoke(cli.app, ["dashboard"])

    assert result.exit_code == 0, result.output
    assert _served(serve) == ("dcfabric", "127.0.0.1", 18030)


def test_env_overrides_the_defaults(serve, monkeypatch: pytest.MonkeyPatch):
    port = _free_port()
    monkeypatch.setenv("DASHBOARD_PORT", str(port))
    monkeypatch.setenv("DASHBOARD_HOST", "127.0.0.1")

    result = runner.invoke(cli.app, ["dashboard"])

    assert result.exit_code == 0, result.output
    assert _served(serve) == ("dcfabric", "127.0.0.1", port)


def test_options_override_the_env(serve, monkeypatch: pytest.MonkeyPatch):
    port = _free_port()
    monkeypatch.setenv("DASHBOARD_PORT", "1")
    monkeypatch.setenv("DASHBOARD_HOST", "192.0.2.1")

    result = runner.invoke(cli.app, ["dashboard", "--port", str(port), "--host", "127.0.0.1", "--use-case", "lab"])

    assert result.exit_code == 0, result.output
    assert _served(serve) == ("lab", "127.0.0.1", port)


def test_port_in_use_refuses_before_serving(serve):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]

        result = runner.invoke(cli.app, ["dashboard", "--port", str(port)])

    assert result.exit_code == ExitCode.ERROR
    assert f"error: port {port} is in use. Set DASHBOARD_PORT or pass --port." in result.output
    serve.assert_not_called()


def test_non_loopback_host_warns_there_is_no_authentication(serve):
    port = _free_port()
    with patch.object(cli, "_port_in_use", return_value=False):
        result = runner.invoke(cli.app, ["dashboard", "--host", "0.0.0.0", "--port", str(port)])  # noqa: S104

    assert result.exit_code == 0, result.output
    assert (
        f"warning: the dashboard has no authentication; anyone who can reach 0.0.0.0:{port} can read it."
        in result.output
    )


def test_loopback_host_does_not_warn(serve):
    result = runner.invoke(cli.app, ["dashboard", "--port", str(_free_port())])

    assert "warning" not in result.output


def test_start_line_names_the_use_case_and_url(serve):
    port = _free_port()

    result = runner.invoke(cli.app, ["dashboard", "--port", str(port)])

    assert f"Dashboard for dcfabric at http://127.0.0.1:{port} (read-only, Ctrl-C to stop)" in result.output


def test_ctrl_c_exits_zero(serve):
    serve.side_effect = KeyboardInterrupt

    result = runner.invoke(cli.app, ["dashboard", "--port", str(_free_port())])

    assert result.exit_code == 0
    assert "workflow" not in result.output
