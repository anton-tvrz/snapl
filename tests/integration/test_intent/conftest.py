"""Fixtures for Intent integration tests against a live Infrahub.

Bring up the stack with::

    docker compose -f development/docker-compose.yml up -d

Then run::

    INFRAHUB_ADDRESS=http://localhost:8001 uv run pytest tests/integration -m integration

If no Infrahub is reachable, the ``live_infrahub_client`` fixture skips the
test rather than failing — so ``pytest`` is always green on a plain dev box.

Env overrides (defaults match ``development/.env``):

- ``INFRAHUB_ADDRESS``    — default ``http://localhost:8001``
- ``INFRAHUB_API_TOKEN``  — default dev admin token from docker-compose.yml
"""

from __future__ import annotations

import os

import httpx
import pytest

from snapl_intent.infrahub.client import build_client
from snapl_intent.infrahub.store import InfrahubIntentStore

DEFAULT_ADDRESS = "http://localhost:8001"
DEFAULT_TOKEN = "06438eb2-8019-4776-878c-0941b1f1d1ec"  # pragma: allowlist secret


def _resolved_address() -> str:
    return os.environ.get("INFRAHUB_ADDRESS") or DEFAULT_ADDRESS


def _resolved_token() -> str:
    return os.environ.get("INFRAHUB_API_TOKEN") or DEFAULT_TOKEN


@pytest.fixture(scope="session")
def infrahub_address() -> str:
    return _resolved_address()


@pytest.fixture(scope="session")
def infrahub_reachable(infrahub_address: str) -> bool:
    """Probe Infrahub's ``/api/config`` endpoint; return True iff it responds 200."""
    try:
        with httpx.Client(timeout=5.0) as probe:
            response = probe.get(f"{infrahub_address}/api/config")
            return response.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


@pytest.fixture
async def live_infrahub_client(infrahub_address: str, infrahub_reachable: bool):
    """Yield a live :class:`InfrahubClient`; skip if Infrahub is unreachable."""
    if not infrahub_reachable:
        pytest.skip(f"Infrahub not reachable at {infrahub_address}")
    os.environ["INFRAHUB_ADDRESS"] = infrahub_address
    os.environ["INFRAHUB_API_TOKEN"] = _resolved_token()
    return build_client(address=infrahub_address, api_token=_resolved_token())


@pytest.fixture
async def live_store(live_infrahub_client) -> InfrahubIntentStore:
    return InfrahubIntentStore(client=live_infrahub_client)


@pytest.fixture(scope="session")
async def _provisioned_once(infrahub_address: str, infrahub_reachable: bool) -> bool:
    """Provision the dcfabric schema exactly once per test session.

    Returning ``True`` signals schema is in place; dependent fixtures block on
    this to avoid racing on ``schema.load``.
    """
    if not infrahub_reachable:
        return False
    client = build_client(address=infrahub_address, api_token=_resolved_token())
    store = InfrahubIntentStore(client=client)
    await store.provision_schema("dcfabric")
    return True


@pytest.fixture
async def provisioned_store(
    live_infrahub_client, _provisioned_once: bool
) -> InfrahubIntentStore:
    if not _provisioned_once:
        pytest.skip("Schema provisioning unavailable (Infrahub not reachable)")
    return InfrahubIntentStore(client=live_infrahub_client)


@pytest.fixture(scope="session")
async def _seeded_once(
    infrahub_address: str, infrahub_reachable: bool, _provisioned_once: bool
) -> bool:
    if not (infrahub_reachable and _provisioned_once):
        return False
    client = build_client(address=infrahub_address, api_token=_resolved_token())
    store = InfrahubIntentStore(client=client)
    await store.seed("dcfabric")
    return True


@pytest.fixture
async def seeded_store(live_infrahub_client, _seeded_once: bool) -> InfrahubIntentStore:
    if not _seeded_once:
        pytest.skip("Seed data unavailable (Infrahub not reachable or schema missing)")
    return InfrahubIntentStore(client=live_infrahub_client)


# ---------------------------------------------------------------------------
# Query-test devices
# ---------------------------------------------------------------------------
# Until the seed ingester's relationship-resolution layer lands
# (``SEED_DEFERRED`` in ``snapl_intent.infrahub.seed``), devices are created
# inline here so T029 has something to query. The fixture is idempotent: it
# looks up each device by name before creating.
_QUERY_TEST_DEVICES: list[dict[str, str]] = [
    {"name": "query-spine-01", "role": "spine", "use_case": "dcfabric"},
    {"name": "query-leaf-01", "role": "leaf", "use_case": "dcfabric"},
    {"name": "query-edge-01", "role": "core", "use_case": "test_edge"},
]


@pytest.fixture(scope="session")
async def _query_devices_seeded(
    infrahub_address: str, infrahub_reachable: bool, _seeded_once: bool
) -> bool:
    if not (infrahub_reachable and _seeded_once):
        return False
    client = build_client(address=infrahub_address, api_token=_resolved_token())
    locations = await client.filters(kind="LocationSite", shortname__value="local-lab")
    if not locations:
        return False
    location = locations[0]
    for device_data in _QUERY_TEST_DEVICES:
        existing = await client.filters(kind="DcimDevice", name__value=device_data["name"])
        if existing:
            continue
        node = await client.create(
            kind="DcimDevice",
            data={
                "name": device_data["name"],
                "status": "active",
                "role": device_data["role"],
                "use_case": device_data["use_case"],
                "location": location.id,
            },
        )
        await node.save()
    return True


@pytest.fixture
async def query_store(
    live_infrahub_client, _query_devices_seeded: bool
) -> InfrahubIntentStore:
    if not _query_devices_seeded:
        pytest.skip(
            "Query-test devices unavailable (Infrahub not reachable or schema/seed missing)"
        )
    return InfrahubIntentStore(client=live_infrahub_client)
