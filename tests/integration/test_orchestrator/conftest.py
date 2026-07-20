"""Fixtures for orchestrator integration tests against a live Temporal cluster + SR Linux.

The live tests exercise the real closed loop: Infrahub-backed intent, gNMI
executor/collector against the containerlab dcfabric nodes, and workflows on a
real Temporal cluster. Bring the stack up with::

    uv run invoke dev.deps        # Infrahub + Temporal
    uv run invoke dev.lab-deploy  # containerlab dcfabric nodes

then run (local .env port offsets shown)::

    INFRAHUB_ADDRESS=http://localhost:8001 \
    TEMPORAL_HOST=localhost:7234 \
    uv run pytest tests/integration/test_orchestrator/ -m integration -v

Every fixture skips rather than fails when its dependency is unreachable, so
the suite is safe to run without the stack (e.g. in CI).

The gNMI dial target for each device is its ``lab_node_name`` from the SoT
(the clab management IP on macOS — see dev/guides + #30), so the tests probe
exactly the address production workflows would dial.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path  # noqa: TC003

import httpx
import pytest

from snapl_intent.infrahub.client import DEFAULT_ADDRESS
from snapl_intent.infrahub.client import build_client as build_infrahub_client
from snapl_intent.infrahub.store import InfrahubIntentStore

# Dev-compose admin token (matches development/docker-compose.yml and the
# intent integration conftest).
DEFAULT_TOKEN = "06438eb2-8019-4776-878c-0941b1f1d1ec"  # pragma: allowlist secret  # noqa: S105
# Containerlab dcfabric default credentials (printed by `invoke dev.lab-deploy`).
DEFAULT_SRLINUX_PASSWORD = "NokiaSrl1!"  # pragma: allowlist secret  # noqa: S105


def _can_reach(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Temporal
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Infrahub (Source of Truth)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def infrahub_address() -> str:
    # Same resolution as production (#61): env override, else the intent
    # client's single default.
    return os.environ.get("INFRAHUB_ADDRESS") or DEFAULT_ADDRESS


@pytest.fixture(scope="session")
def infrahub_token() -> str:
    return os.environ.get("INFRAHUB_API_TOKEN") or DEFAULT_TOKEN


@pytest.fixture(scope="session")
def skip_if_infrahub_unreachable(infrahub_address: str) -> None:
    try:
        with httpx.Client(timeout=5.0) as probe:
            if probe.get(f"{infrahub_address}/api/config").status_code == 200:
                return
    except (httpx.HTTPError, OSError):
        pass
    pytest.skip(f"Infrahub not reachable at {infrahub_address}")


@pytest.fixture
def live_intent_store(skip_if_infrahub_unreachable, infrahub_address: str, infrahub_token: str) -> InfrahubIntentStore:
    # Function-scoped: a fresh SDK client per test keeps its httpx transport on
    # the test's own event loop.
    client = build_infrahub_client(address=infrahub_address, api_token=infrahub_token)
    return InfrahubIntentStore(client=client)


@pytest.fixture
async def live_desired_states(live_intent_store: InfrahubIntentStore) -> list:
    """The dcfabric desired states from the live SoT; skip when not seeded."""
    states = await live_intent_store.get_desired_state(use_case="dcfabric")
    if not states:
        pytest.skip("Infrahub reachable but dcfabric is not seeded — run provision_schema + seed first")
    return states


# ---------------------------------------------------------------------------
# SR Linux lab nodes
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def srlinux_credentials() -> tuple[str, str, int]:
    username = os.environ.get("SRLINUX_USERNAME", "admin")
    password = os.environ.get("SRLINUX_PASSWORD") or DEFAULT_SRLINUX_PASSWORD
    port = int(os.environ.get("SRLINUX_PORT", "57400"))
    return username, password, port


@pytest.fixture
def skip_if_lab_unreachable(live_desired_states: list, srlinux_credentials: tuple[str, str, int]) -> None:
    """Probe every device's SoT dial target — the address production would use."""
    _, _, port = srlinux_credentials
    for state in live_desired_states:
        target = state.device.lab_node_name or state.device.management_address
        if not _can_reach(target, port):
            pytest.skip(f"SR Linux node {state.device.name} not reachable at {target}:{port}")


# ---------------------------------------------------------------------------
# Real activity container + audit log
# ---------------------------------------------------------------------------


@pytest.fixture
def audit_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "integration-audit.sqlite")


@pytest.fixture
async def live_activities(
    live_intent_store: InfrahubIntentStore,
    srlinux_credentials: tuple[str, str, int],
    audit_db_path: str,
):
    """A fully real Activities container, mirroring the worker bootstrap
    (`_build_default_activities`) but with a per-test SQLite audit path.

    Installs itself via set_activities and resets the global on teardown.
    """
    from snapl_collector.gnmi.collector import GnmiCollector
    from snapl_executor.gnmi.executor import GnmiExecutor
    from snapl_orchestrator import activities as activities_module
    from snapl_orchestrator.activities import Activities, set_activities
    from snapl_orchestrator.audit.sqlite import SqliteAuditLog
    from snapl_orchestrator.worker.run import _build_observer

    username, password, port = srlinux_credentials
    conn = {"username": username, "password": password, "port": port, "insecure": True}

    audit_log = SqliteAuditLog(database_url=audit_db_path)
    await audit_log.initialize()

    container = Activities(
        intent_store=live_intent_store,
        executor=GnmiExecutor(**conn),
        collector=GnmiCollector(**conn),
        observer=_build_observer(),
        audit_log=audit_log,
    )
    set_activities(container)
    yield container
    await audit_log.close()
    activities_module._activities = None
