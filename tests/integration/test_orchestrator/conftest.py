"""Fixtures for orchestrator integration tests against a live Temporal cluster + SR Linux."""

from __future__ import annotations

import os
import socket
from pathlib import Path  # noqa: TC003

import pytest


def _can_reach(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def temporal_endpoint() -> str:
    return os.environ.get("TEMPORAL_HOST", "localhost:7233")


@pytest.fixture(scope="session")
def temporal_namespace() -> str:
    return os.environ.get("TEMPORAL_NAMESPACE", "default")


@pytest.fixture(scope="session")
def temporal_task_queue() -> str:
    return os.environ.get("TEMPORAL_TASK_QUEUE", "snapl-orchestrator-test")


@pytest.fixture(scope="session")
def skip_if_temporal_unreachable(temporal_endpoint: str) -> None:
    host, _, port_str = temporal_endpoint.partition(":")
    port = int(port_str or "7233")
    if not _can_reach(host, port):
        pytest.skip(f"Temporal cluster not reachable at {temporal_endpoint}")


@pytest.fixture(scope="session")
def srlinux_endpoint() -> tuple[str, int, str, str]:
    host = os.environ.get("SRLINUX_HOST", "clab-dcfabric-spine-01")
    port = int(os.environ.get("SRLINUX_PORT", "57400"))
    username = os.environ.get("SRLINUX_USERNAME", "admin")
    password = os.environ.get("SRLINUX_PASSWORD", "")  # pragma: allowlist secret
    return host, port, username, password


@pytest.fixture(scope="session")
def skip_if_srlinux_unreachable(srlinux_endpoint: tuple[str, int, str, str]) -> None:
    host, port, _, password = srlinux_endpoint
    if not password or not _can_reach(host, port):
        pytest.skip(f"SR Linux node {host}:{port} not reachable or password not set")


@pytest.fixture
def audit_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "integration-audit.sqlite")
