"""Fixtures for Executor integration tests against a live SR Linux node (T017).

Bring up the Containerlab dcfabric lab::

    cd containerlab && sudo containerlab deploy -t dcfabric.yml

Then run::

    SRLINUX_HOST=clab-dcfabric-spine-01 \\
    SRLINUX_PORT=57400 \\
    SRLINUX_USERNAME=admin \\
    SRLINUX_PASSWORD=<lab-password> \\
    uv run pytest tests/integration/test_executor/ -m integration

If no SR Linux node is reachable the fixture skips the test automatically.

Env overrides (defaults match Containerlab dcfabric topology):

- ``SRLINUX_HOST``      — default ``clab-dcfabric-spine-01``
- ``SRLINUX_PORT``      — default ``57400``
- ``SRLINUX_USERNAME``  — default ``admin``
- ``SRLINUX_PASSWORD``  — required (no default)
"""

from __future__ import annotations

import os

import pytest

DEFAULT_HOST = "clab-dcfabric-spine-01"
DEFAULT_PORT = 57400
DEFAULT_USERNAME = "admin"


def _host() -> str:
    return os.environ.get("SRLINUX_HOST", DEFAULT_HOST)


def _port() -> int:
    return int(os.environ.get("SRLINUX_PORT", str(DEFAULT_PORT)))


def _username() -> str:
    return os.environ.get("SRLINUX_USERNAME", DEFAULT_USERNAME)


def _password() -> str:
    return os.environ.get("SRLINUX_PASSWORD", "")


def _is_reachable(host: str, port: int) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except (OSError, TimeoutError):
        return False


@pytest.fixture(scope="session")
def srlinux_reachable() -> bool:
    return _is_reachable(_host(), _port())


@pytest.fixture
def srlinux_executor(srlinux_reachable):
    """Yield a GnmiExecutor pointed at a live SR Linux node; skip if unreachable."""
    if not srlinux_reachable:
        pytest.skip(f"SR Linux not reachable at {_host()}:{_port()}")
    if not _password():
        pytest.skip("SRLINUX_PASSWORD not set")
    from snapl_executor.gnmi.executor import GnmiExecutor

    return GnmiExecutor(
        host=_host(),
        port=_port(),
        username=_username(),
        password=_password(),  # pragma: allowlist secret
        insecure=True,
        timeout=30,
    )
