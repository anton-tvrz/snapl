"""`snapl dashboard` (spec 010 T018, contracts/cli-dashboard.md).

The server itself is patched out: what is pinned here is how the command picks
its address, what it refuses before serving, and what it tells the operator.
"""

from __future__ import annotations

import json
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


# --- the designed UI (spec 011, contracts/cli-and-build.md) --------------------------


def test_says_when_the_designed_ui_is_not_built(serve, tmp_path):
    with patch.object(cli, "_port_in_use", return_value=False), patch.object(cli, "_built_ui", return_value=None):
        result = runner.invoke(cli.app, ["dashboard"])

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].startswith("Dashboard for dcfabric at http://127.0.0.1:18030")
    assert lines[1] == (
        "The designed UI is not built: serving the plain page. Build it with: uv run invoke dashboard.build"
    )
    serve.assert_called_once()


def test_says_nothing_more_when_the_designed_ui_is_built(serve, tmp_path):
    with patch.object(cli, "_port_in_use", return_value=False), patch.object(cli, "_built_ui", return_value=tmp_path):
        result = runner.invoke(cli.app, ["dashboard"])

    assert result.exit_code == 0, result.output
    assert "not built" not in result.output
    assert len(result.output.splitlines()) == 1


def test_built_ui_looks_in_the_packaged_location():
    from snapl_presentation.dashboard import pages

    with patch.object(pages, "find_ui", return_value=None) as find:
        assert cli._built_ui() is None

    find.assert_called_once_with(pages.BUILT_UI)


# --- --archive (spec 011 T071, contracts/cli-and-build.md) ----------------------------


@pytest.fixture
def serve_app():
    """The uvicorn server for an archive, patched: each call asks the app what is given in `asks`."""
    answers: dict[str, dict] = {}
    asks: list[str] = []

    def fake(app, host, port):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        for path in asks:
            answers[path] = client.get(path).json()

    with patch.object(cli, "_serve_app", side_effect=fake) as patched:
        patched.asks, patched.answers = asks, answers
        yield patched


def _archive(tmp_path, **kwargs):
    from tests.unit.test_presentation.test_dashboard_archive import build_archive

    return build_archive(tmp_path, **kwargs)


def test_archive_start_line_names_the_file_its_time_commit_and_use_case(serve_app, tmp_path):
    parts = [{"name": "metrics_snapshot", "path": "prometheus", "present": False, "source": "x", "reason": "down"}]
    archive = _archive(tmp_path, manifest={"parts": parts}, prometheus=False)
    port = _free_port()

    result = runner.invoke(cli.app, ["dashboard", "--archive", str(archive), "--port", str(port)])

    assert result.exit_code == 0, result.output
    assert (
        f"Archive {archive.name} (made 2026-11-07 21:20 UTC, commit 6baff39) for dcfabric "
        f"at http://127.0.0.1:{port} (read-only, Ctrl-C to stop)"
    ) in result.output
    assert "  missing: metrics_snapshot (down)" in result.output
    serve_app.assert_called_once()


def test_archive_takes_the_use_case_from_its_manifest(serve_app, tmp_path):
    archive = _archive(tmp_path, manifest={"use_case": "edge"})
    serve_app.asks.append("/api/v1/mode")

    result = runner.invoke(cli.app, ["dashboard", "--archive", str(archive), "--port", str(_free_port())])

    assert "for edge at" in result.output
    assert serve_app.answers["/api/v1/mode"]["use_case"] == "edge"
    assert serve_app.answers["/api/v1/mode"]["mode"] == "archive"


def test_archive_refuses_a_different_use_case(serve_app, tmp_path):
    archive = _archive(tmp_path)

    result = runner.invoke(cli.app, ["dashboard", "--archive", str(archive), "--use-case", "edge"])

    assert result.exit_code == int(ExitCode.ERROR)
    assert "is for use case 'dcfabric', not 'edge'" in result.output
    serve_app.assert_not_called()


@pytest.mark.parametrize("name", ["missing.tar.gz", "notes.txt"])
def test_archive_refusals_exit_before_the_port_check(serve_app, tmp_path, name):
    path = tmp_path / name
    if name == "notes.txt":
        path.write_text("hello")

    with patch.object(cli, "_port_in_use", side_effect=AssertionError("port checked")):
        result = runner.invoke(cli.app, ["dashboard", "--archive", str(path)])

    assert result.exit_code == int(ExitCode.ERROR)
    assert f"error: {path} " in result.output
    serve_app.assert_not_called()


def test_archive_reads_no_connection_settings_or_tokens(serve_app, tmp_path, monkeypatch):
    for name, value in {
        "TEMPORAL_HOST": "temporal.invalid:7233",
        "INFRAHUB_ADDRESS": "http://infrahub.invalid",
        "GITHUB_TOKEN": "ghp_not_a_real_token",
        "SNAPL_AUDIT_DB": str(tmp_path / "other.sqlite"),
        "PROMETHEUS_ADDRESS": "http://prometheus.invalid:9090",
        "TEMPORAL_UI_ADDRESS": "http://temporal-ui.invalid",
    }.items():
        monkeypatch.setenv(name, value)
    serve_app.asks.extend(["/api/v1/devices", "/api/v1/links", "/api/v1/sources", "/api/v1/runs"])

    result = runner.invoke(cli.app, ["dashboard", "--archive", str(_archive(tmp_path)), "--port", str(_free_port())])

    assert result.exit_code == 0, result.output
    devices = serve_app.answers["/api/v1/devices"]["devices"]["data"]
    assert {d["name"]: d["drift_state"] for d in devices}["leaf-01"] == "undesired"
    everything = json.dumps(serve_app.answers)
    assert ".invalid" not in everything
    assert "ghp_" not in everything
