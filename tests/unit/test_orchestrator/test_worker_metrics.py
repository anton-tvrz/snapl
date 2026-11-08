"""The worker's metrics endpoint settings and runtime (spec 012, T005, research R3).

Every test binds its own free port. 18091 is the maintainer's worker's port and
is never touched here.
"""

from __future__ import annotations

import logging
import socket

import pytest
from temporalio.runtime import Runtime

from snapl_orchestrator.exceptions import OrchestratorConfigError
from snapl_orchestrator.worker.metrics import (
    DEFAULT_WORKER_METRICS_BIND,
    DEFAULT_WORKER_METRICS_PORT,
    MetricsSettings,
    build_runtime,
    metrics_settings_from_env,
)

pytestmark = pytest.mark.unit


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestSettingsFromEnv:
    def test_defaults_match_the_host_resource_registry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WORKER_METRICS_PORT", raising=False)
        monkeypatch.delenv("WORKER_METRICS_BIND", raising=False)

        settings = metrics_settings_from_env()

        assert settings == MetricsSettings(port=18091, bind="127.0.0.1")
        assert (DEFAULT_WORKER_METRICS_PORT, DEFAULT_WORKER_METRICS_BIND) == (18091, "127.0.0.1")

    def test_reads_port_and_bind(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WORKER_METRICS_PORT", "28091")
        monkeypatch.setenv("WORKER_METRICS_BIND", "0.0.0.0")  # noqa: S104 — a value under test, not a bind

        assert metrics_settings_from_env() == MetricsSettings(port=28091, bind="0.0.0.0")  # noqa: S104

    @pytest.mark.parametrize("raw", ["off", "OFF", " off "])
    def test_off_disables_metrics(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv("WORKER_METRICS_PORT", raw)

        assert metrics_settings_from_env().port == "off"

    @pytest.mark.parametrize("raw", ["nine", "18091x", "0", "65536", "-1"])
    def test_rejects_a_port_that_is_neither_a_port_nor_off(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv("WORKER_METRICS_PORT", raw)

        with pytest.raises(OrchestratorConfigError, match="WORKER_METRICS_PORT"):
            metrics_settings_from_env()

    def test_empty_values_fall_back_to_the_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WORKER_METRICS_PORT", "")
        monkeypatch.setenv("WORKER_METRICS_BIND", "")

        assert metrics_settings_from_env() == MetricsSettings(port=18091, bind="127.0.0.1")


class TestBuildRuntime:
    def test_returns_a_runtime_on_a_free_port(self) -> None:
        port = _free_port()

        runtime = build_runtime(port=port, bind="127.0.0.1")

        assert isinstance(runtime, Runtime)
        # The exporter is listening: a second bind on the same port fails.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe, pytest.raises(OSError, match="in use"):
            probe.bind(("127.0.0.1", port))

    def test_a_taken_port_returns_none_and_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
            holder.bind(("127.0.0.1", 0))
            holder.listen()
            port = holder.getsockname()[1]

            with caplog.at_level(logging.WARNING, logger="snapl_orchestrator.worker.metrics"):
                runtime = build_runtime(port=port, bind="127.0.0.1")

        assert runtime is None
        [record] = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert record.getMessage() == (f"metrics off: port {port} in use (set WORKER_METRICS_PORT to move it, or off)")

    def test_off_returns_none_without_binding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import snapl_orchestrator.worker.metrics as metrics_mod

        def _no_runtime(*_args: object, **_kwargs: object) -> Runtime:
            raise AssertionError("off must not build a runtime")

        monkeypatch.setattr(metrics_mod, "Runtime", _no_runtime)

        assert build_runtime(port="off", bind="127.0.0.1") is None

    def test_an_unusable_bind_address_returns_none_and_says_why(self, caplog: pytest.LogCaptureFixture) -> None:
        """Any exporter failure, not just a taken port, leaves the worker running (FR-002)."""
        with caplog.at_level(logging.WARNING, logger="snapl_orchestrator.worker.metrics"):
            runtime = build_runtime(port=_free_port(), bind="not-an-ip")

        assert runtime is None
        [record] = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert record.getMessage().startswith("metrics off: could not serve on not-an-ip:")
