"""Unit tests for the worker bootstrap builders (#30).

The worker builds one executor/collector for all devices, so neither may be
pinned to a fixed host — the dial target resolves per device at call time.
"""

from __future__ import annotations

import pytest

from snapl_orchestrator.exceptions import OrchestratorConfigError
from snapl_orchestrator.worker.run import _build_collector, _build_executor

pytestmark = pytest.mark.unit


class TestBuildExecutor:
    def test_has_no_fixed_host(self, monkeypatch):
        monkeypatch.setenv("SRLINUX_PASSWORD", "pw")
        executor = _build_executor()
        assert executor._host is None

    def test_missing_password_raises(self, monkeypatch):
        monkeypatch.delenv("SRLINUX_PASSWORD", raising=False)
        with pytest.raises(OrchestratorConfigError, match="SRLINUX_PASSWORD"):
            _build_executor()


class TestBuildCollector:
    def test_has_no_fixed_host(self, monkeypatch):
        monkeypatch.setenv("SRLINUX_PASSWORD", "pw")
        collector = _build_collector()
        assert collector._host is None

    def test_missing_password_raises(self, monkeypatch):
        monkeypatch.delenv("SRLINUX_PASSWORD", raising=False)
        with pytest.raises(OrchestratorConfigError, match="SRLINUX_PASSWORD"):
            _build_collector()
