"""Fixtures for the end-to-end demo-arc suite (issue #100).

This suite is opt-in. It provisions a schema, seeds the Source of Truth, and
pushes configuration to real devices — destructive enough that it must never
run by accident:

    SNAPL_E2E=1 uv run pytest tests/e2e -m e2e

Everything else skips it, including the default `invoke test-unit`.

Why it exists when `tests/integration/test_orchestrator/` already drives the
live loop: that suite starts from an environment that is *already* seeded and
patched. Every failure that has actually cost time lived in the prefix it skips
— the async schema-registration race (#87), the unrenderable `loopback0` seed
(#78), the mgmt-network collision (#90), the unresolvable `lab_node_name`
(#96). Each was found by a human running the demo, not by CI.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import httpx
import pytest

from snapl_intent.infrahub.client import DEFAULT_ADDRESS
from snapl_intent.infrahub.client import build_client as build_infrahub_client
from snapl_intent.infrahub.store import InfrahubIntentStore

# Dev-compose defaults, matching the integration conftest.
DEFAULT_TOKEN = "06438eb2-8019-4776-878c-0941b1f1d1ec"  # pragma: allowlist secret  # noqa: S105
DEFAULT_SRLINUX_PASSWORD = "NokiaSrl1!"  # pragma: allowlist secret  # noqa: S105

USE_CASE = "dcfabric"
EXPECTED_DEVICE_COUNT = 6


def can_reach(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session", autouse=True)
def require_opt_in() -> None:
    """The gate. This suite seeds a SoT and configures devices."""
    if os.environ.get("SNAPL_E2E") != "1":
        pytest.skip("end-to-end suite is opt-in — set SNAPL_E2E=1 (it seeds the SoT and writes to devices)")


@pytest.fixture(scope="session")
def infrahub_address() -> str:
    return os.environ.get("INFRAHUB_ADDRESS") or DEFAULT_ADDRESS


@pytest.fixture(scope="session")
def infrahub_token() -> str:
    return os.environ.get("INFRAHUB_API_TOKEN") or DEFAULT_TOKEN


@pytest.fixture(scope="session")
def temporal_endpoint() -> str:
    return os.environ.get("TEMPORAL_HOST", "localhost:7233")


@pytest.fixture(scope="session")
def temporal_namespace() -> str:
    return os.environ.get("TEMPORAL_NAMESPACE", "default")


@pytest.fixture(scope="session")
def srlinux_credentials() -> tuple[str, str, int]:
    return (
        os.environ.get("SRLINUX_USERNAME", "admin"),
        os.environ.get("SRLINUX_PASSWORD") or DEFAULT_SRLINUX_PASSWORD,
        int(os.environ.get("SRLINUX_PORT", "57400")),
    )


def _looks_like_a_foreign_sot(address: str) -> str | None:
    """Detect an Infrahub that belongs to someone else (#107).

    A port answering is not evidence the instance is ours. snapl's committed
    default (8000) is also the default for every other Infrahub deployment,
    and on a machine running a neighbouring project it is *their* server that
    answers. This suite provisions a schema and seeds — pointed at the wrong
    instance it would write six devices into a foreign Source of Truth.

    The signature is "populated, but without snapl's markers". A fresh snapl
    instance has neither devices nor markers and passes; an established snapl
    instance has both and passes; someone else's fabric has devices and no
    ``use_case`` attribute, and is refused.
    """
    try:
        with httpx.Client(timeout=10.0) as probe:
            schema = probe.get(f"{address}/api/schema", params={"branch": "main"}).json()
    except (httpx.HTTPError, OSError, ValueError):
        return None  # unreadable schema is not proof of foreignness

    nodes = {node["kind"]: node for node in schema.get("nodes", [])}
    device = nodes.get("DcimDevice")
    if device is None:
        return None  # nothing provisioned yet — a fresh instance, fine

    if any(attribute["name"] == "use_case" for attribute in device.get("attributes", [])):
        return None  # snapl's own marker is present

    try:
        with httpx.Client(timeout=10.0) as probe:
            payload = probe.post(
                f"{address}/graphql",
                json={"query": "{ DcimDevice { count } }"},
            ).json()
        count = payload["data"]["DcimDevice"]["count"]
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
        return None

    if count:
        return (
            f"{address} has {count} devices but no snapl 'use_case' attribute — "
            "this looks like another project's Infrahub, not snapl's"
        )
    return None


@pytest.fixture(scope="session")
def require_stack(infrahub_address: str, temporal_endpoint: str) -> None:
    """Fail-fast preflight. Unlike the integration suite this *reports* what is
    missing rather than skipping silently: an opt-in run that quietly skips
    would defeat the purpose of having asked for it."""
    host, _, port = temporal_endpoint.partition(":")
    if not can_reach(host or "localhost", int(port or 7233)):
        pytest.fail(f"Temporal not reachable at {temporal_endpoint} — run: uv run invoke dev.deps")
    try:
        with httpx.Client(timeout=5.0) as probe:
            if probe.get(f"{infrahub_address}/api/config").status_code != 200:
                raise httpx.HTTPError("bad status")
    except (httpx.HTTPError, OSError) as exc:
        pytest.fail(f"Infrahub not reachable at {infrahub_address} ({exc}) — run: uv run invoke dev.deps")

    foreign = _looks_like_a_foreign_sot(infrahub_address)
    if foreign:
        pytest.fail(
            f"refusing to seed: {foreign}.\n"
            "Set INFRAHUB_ADDRESS to snapl's own instance, or start it: uv run invoke dev.deps.\n"
            "See #107 — snapl's default ports collide with other projects' stacks."
        )


@pytest.fixture
def intent_store(require_stack, infrahub_address: str, infrahub_token: str) -> InfrahubIntentStore:
    # Function-scoped so each test's SDK client sits on its own event loop.
    client = build_infrahub_client(address=infrahub_address, api_token=infrahub_token)
    return InfrahubIntentStore(client=client)


@pytest.fixture(scope="session")
def audit_db(tmp_path_factory) -> str:
    return str(tmp_path_factory.mktemp("e2e") / "demo-arc-audit.sqlite")


@pytest.fixture
async def activities(intent_store: InfrahubIntentStore, srlinux_credentials, audit_db: str):
    """A fully real Activities container, mirroring the worker bootstrap."""
    from snapl_collector.gnmi.collector import GnmiCollector
    from snapl_executor.gnmi.executor import GnmiExecutor
    from snapl_orchestrator import activities as activities_module
    from snapl_orchestrator.activities import Activities, set_activities
    from snapl_orchestrator.audit.sqlite import SqliteAuditLog
    from snapl_orchestrator.worker.run import _build_observer

    username, password, port = srlinux_credentials
    conn = {"username": username, "password": password, "port": port, "insecure": True}

    audit_log = SqliteAuditLog(database_url=audit_db)
    await audit_log.initialize()

    set_activities(
        Activities(
            intent_store=intent_store,
            executor=GnmiExecutor(**conn),
            collector=GnmiCollector(**conn),
            observer=_build_observer(),
            audit_log=audit_log,
        )
    )
    yield
    await audit_log.close()
    activities_module._activities = None


@pytest.fixture
def worker_factory(temporal_endpoint: str, temporal_namespace: str):
    """Build a client plus an in-process worker on a private task queue."""
    from temporalio.worker import Worker

    from snapl_orchestrator.activities.audit import record_audit_event
    from snapl_orchestrator.activities.collector import collect_running_state
    from snapl_orchestrator.activities.executor import apply_config
    from snapl_orchestrator.activities.intent import fetch_desired_state, fetch_devices_for_use_case
    from snapl_orchestrator.activities.observability import detect_drift
    from snapl_orchestrator.worker.client import build_client
    from snapl_orchestrator.worker.sandbox import build_workflow_runner
    from snapl_orchestrator.workflows.deploy_intent import DeployIntentWorkflow
    from snapl_orchestrator.workflows.reconcile_devices import ReconcileDevicesWorkflow
    from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow

    async def _build(task_queue: str):
        client = await build_client(target=temporal_endpoint, namespace=temporal_namespace)
        worker = Worker(
            client,
            task_queue=task_queue,
            workflows=[DeployIntentWorkflow, ScanDriftWorkflow, ReconcileDevicesWorkflow],
            workflow_runner=build_workflow_runner(),
            activities=[
                fetch_desired_state,
                fetch_devices_for_use_case,
                apply_config,
                collect_running_state,
                detect_drift,
                record_audit_event,
            ],
        )
        return client, worker

    return _build


@pytest.fixture(scope="session")
def lab_topology_pins() -> dict[str, str]:
    """The static mgmt addresses from containerlab/dcfabric.yml (#90/#96).

    Read from the topology rather than hardcoded so this suite fails loudly if
    the lab is re-addressed without the seed following.
    """
    import yaml

    path = Path(__file__).parents[2] / "containerlab" / "dcfabric.yml"
    nodes = yaml.safe_load(path.read_text())["topology"]["nodes"]
    return {name: node["mgmt-ipv4"] for name, node in nodes.items()}
