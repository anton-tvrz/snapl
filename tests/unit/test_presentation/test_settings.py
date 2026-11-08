"""CliSettings: the dashboard's settings resolve from the environment (spec 010)."""

from __future__ import annotations

import pytest

from snapl_presentation.settings import CliSettings

pytestmark = pytest.mark.unit

_DASHBOARD_VARS = (
    "DASHBOARD_PORT",
    "DASHBOARD_HOST",
    "TEMPORAL_UI_ADDRESS",
    "TEMPORAL_UI_PORT",
    "SNAPL_GITHUB_REPO",
    "GITHUB_TOKEN",
    "SNAPL_TEST_RUNS_DIR",
    "SNAPL_PROJECT_BOARD_URL",
    "PROMETHEUS_ADDRESS",
    "PROMETHEUS_PORT",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _DASHBOARD_VARS:
        monkeypatch.delenv(var, raising=False)


def test_dashboard_defaults_match_the_host_resource_registry() -> None:
    settings = CliSettings.from_env()

    assert settings.dashboard_port == 18030
    assert settings.dashboard_host == "127.0.0.1"
    assert settings.temporal_ui_address == "http://localhost:18034"
    assert settings.github_repo is None
    assert settings.github_token is None
    assert settings.test_runs_dir == ".snapl/test-runs"
    assert settings.project_board_url == "https://github.com/users/anton-tvrz/projects/4"


def test_dashboard_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_PORT", "18999")
    monkeypatch.setenv("DASHBOARD_HOST", "0.0.0.0")  # noqa: S104 — a value under test, not a bind
    monkeypatch.setenv("TEMPORAL_UI_ADDRESS", "http://temporal.example:8233")
    monkeypatch.setenv("SNAPL_GITHUB_REPO", "someone/fork")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("SNAPL_TEST_RUNS_DIR", "/tmp/runs")  # noqa: S108 — a value under test
    monkeypatch.setenv("SNAPL_PROJECT_BOARD_URL", "https://github.com/users/someone/projects/9")

    settings = CliSettings.from_env()

    assert settings.dashboard_port == 18999
    assert settings.dashboard_host == "0.0.0.0"  # noqa: S104
    assert settings.temporal_ui_address == "http://temporal.example:8233"
    assert settings.github_repo == "someone/fork"
    assert settings.github_token == "ghp_test"  # noqa: S105 — a fake token under test
    assert settings.test_runs_dir == "/tmp/runs"  # noqa: S108
    assert settings.project_board_url == "https://github.com/users/someone/projects/9"


def test_temporal_ui_address_follows_the_ui_port_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """The registry's override variable moves the UI, so the links must follow it."""
    monkeypatch.setenv("TEMPORAL_UI_PORT", "28034")

    assert CliSettings.from_env().temporal_ui_address == "http://localhost:28034"


def test_repr_never_shows_the_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret_value")

    assert "ghp_secret_value" not in repr(CliSettings.from_env())


def test_prometheus_address_defaults_to_the_registry_port() -> None:
    """Spec 012: the metrics store's host port is 18090 (host-resource-registry)."""
    assert CliSettings.from_env().prometheus_address == "http://localhost:18090"


def test_prometheus_address_follows_the_port_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMETHEUS_PORT", "28090")

    assert CliSettings.from_env().prometheus_address == "http://localhost:28090"


def test_prometheus_address_wins_over_the_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMETHEUS_ADDRESS", "http://prom.example:9090")
    monkeypatch.setenv("PROMETHEUS_PORT", "28090")

    assert CliSettings.from_env().prometheus_address == "http://prom.example:9090"
