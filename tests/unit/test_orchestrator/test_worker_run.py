"""Unit tests for the worker bootstrap builders (#30).

The worker builds one executor/collector for all devices, so neither may be
pinned to a fixed host — the dial target resolves per device at call time.
"""

from __future__ import annotations

import pytest

from snapl_orchestrator.exceptions import OrchestratorConfigError
from snapl_orchestrator.worker.run import _build_collector, _build_executor, _build_intent_store

pytestmark = pytest.mark.unit


class TestBuildExecutor:
    def test_has_no_fixed_host(self, monkeypatch):
        monkeypatch.setenv("SRLINUX_PASSWORD", "pw")
        executor = _build_executor()
        assert executor._host is None

    def test_default_port_and_insecure(self, monkeypatch):
        monkeypatch.setenv("SRLINUX_PASSWORD", "pw")
        monkeypatch.delenv("SRLINUX_PORT", raising=False)
        monkeypatch.delenv("SRLINUX_INSECURE", raising=False)
        executor = _build_executor()
        assert executor._port == 57400
        assert executor._insecure is True

    def test_srlinux_port_env_is_respected(self, monkeypatch):
        monkeypatch.setenv("SRLINUX_PASSWORD", "pw")
        monkeypatch.setenv("SRLINUX_PORT", "50051")
        executor = _build_executor()
        assert executor._port == 50051

    def test_srlinux_insecure_env_disables_plaintext(self, monkeypatch):
        monkeypatch.setenv("SRLINUX_PASSWORD", "pw")
        monkeypatch.setenv("SRLINUX_INSECURE", "false")
        executor = _build_executor()
        assert executor._insecure is False

    def test_bad_srlinux_port_raises_config_error(self, monkeypatch):
        monkeypatch.setenv("SRLINUX_PASSWORD", "pw")
        monkeypatch.setenv("SRLINUX_PORT", "not-a-port")
        with pytest.raises(OrchestratorConfigError, match="SRLINUX_PORT"):
            _build_executor()

    def test_missing_password_raises(self, monkeypatch):
        monkeypatch.delenv("SRLINUX_PASSWORD", raising=False)
        with pytest.raises(OrchestratorConfigError, match="SRLINUX_PASSWORD"):
            _build_executor()


class TestBuildCollector:
    def test_has_no_fixed_host(self, monkeypatch):
        monkeypatch.setenv("SRLINUX_PASSWORD", "pw")
        collector = _build_collector()
        assert collector._host is None

    def test_srlinux_port_and_insecure_env_are_respected(self, monkeypatch):
        monkeypatch.setenv("SRLINUX_PASSWORD", "pw")
        monkeypatch.setenv("SRLINUX_PORT", "50051")
        monkeypatch.setenv("SRLINUX_INSECURE", "no")
        collector = _build_collector()
        assert collector._port == 50051
        assert collector._insecure is False

    def test_missing_password_raises(self, monkeypatch):
        monkeypatch.delenv("SRLINUX_PASSWORD", raising=False)
        with pytest.raises(OrchestratorConfigError, match="SRLINUX_PASSWORD"):
            _build_collector()


class TestBuildIntentStore:
    def test_default_address_matches_intent_client_default(self, monkeypatch):
        """The worker must not define its own Infrahub default (#61) — an unset
        INFRAHUB_ADDRESS resolves through the intent client's single source of
        truth (http://localhost:8000, matching the committed compose default)."""
        from snapl_intent.infrahub.client import DEFAULT_ADDRESS

        monkeypatch.setenv("INFRAHUB_API_TOKEN", "tok")
        monkeypatch.delenv("INFRAHUB_ADDRESS", raising=False)
        store = _build_intent_store()
        assert store._client.config.address == DEFAULT_ADDRESS

    def test_infrahub_address_env_is_respected(self, monkeypatch):
        monkeypatch.setenv("INFRAHUB_API_TOKEN", "tok")
        monkeypatch.setenv("INFRAHUB_ADDRESS", "http://sot.example:9000")
        store = _build_intent_store()
        assert store._client.config.address == "http://sot.example:9000"

    def test_missing_token_raises(self, monkeypatch):
        monkeypatch.delenv("INFRAHUB_API_TOKEN", raising=False)
        with pytest.raises(OrchestratorConfigError, match="INFRAHUB_API_TOKEN"):
            _build_intent_store()
