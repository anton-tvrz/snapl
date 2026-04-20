"""Unit tests for :class:`InfrahubIntentStore` using a mock SDK client.

These tests exercise the store's query paths without a running Infrahub —
the mock_infrahub_client fixture from ``tests/conftest.py`` stands in for
the SDK client. Tests grow across phases (US1 -> US2 -> US3 -> US4 -> Polish).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
import yaml

if TYPE_CHECKING:
    from pathlib import Path

from snapl_intent.exceptions import (
    IntentConnectionError,
    IntentSchemaError,
    IntentValidationError,
)
from snapl_intent.infrahub.store import InfrahubIntentStore
from snapl_intent.models import DesiredState, ProvisionResult, SeedResult

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers — build SDK-shaped mock nodes the store can translate
# ---------------------------------------------------------------------------


def _attr(value):
    """SDK nodes expose scalar attributes via a ``.value`` property."""
    return SimpleNamespace(value=value)


def _rel_one(node):
    """An SDK cardinality-one relation exposes its peer via ``.peer``."""
    return SimpleNamespace(peer=node)


def _rel_many(nodes):
    """An SDK cardinality-many relation exposes ``.peers``."""
    return SimpleNamespace(peers=list(nodes))


def _make_interface_node(*, device_uuid: UUID, name: str, ip: str | None = None, prefix: int | None = None):
    return SimpleNamespace(
        id=str(uuid4()),
        device=_rel_one(SimpleNamespace(id=str(device_uuid))),
        name=_attr(name),
        description=_attr(None),
        ip_address=_attr(ip),
        prefix_length=_attr(prefix),
        enabled=_attr(True),
        speed=_attr(None),
        mtu=_attr(9214),
        peer_device=_attr(None),
        peer_interface=_attr(None),
    )


def _make_bgp_session_node(*, device_uuid: UUID, local_asn: int, peer_address: str, peer_asn: int):
    return SimpleNamespace(
        id=str(uuid4()),
        device=_rel_one(SimpleNamespace(id=str(device_uuid))),
        local_asn=_attr(local_asn),
        peer_address=_attr(peer_address),
        peer_asn=_attr(peer_asn),
        peer_group=_attr(None),
        address_family=_attr("ipv4_unicast"),
        export_policy=_attr(None),
        import_policy=_attr(None),
        enabled=_attr(True),
    )


def _make_device_node(
    *,
    name: str,
    role: str = "spine",
    use_case: str = "dcfabric",
    management: str = "10.0.0.1",
    interfaces: list | None = None,
    bgp_sessions: list | None = None,
    device_id: UUID | None = None,
):
    dev_uuid = device_id or uuid4()
    return SimpleNamespace(
        id=str(dev_uuid),
        name=_attr(name),
        management_address=_attr(management),
        role=_attr(role),
        use_case=_attr(use_case),
        platform=_attr("nokia-srlinux"),
        description=_attr(None),
        interfaces=_rel_many(interfaces or []),
        bgp_sessions=_rel_many(bgp_sessions or []),
    )


# ---------------------------------------------------------------------------
# US1 — get_desired_state
# ---------------------------------------------------------------------------


class TestGetDesiredState:
    async def test_returns_empty_list_when_no_matches(self, mock_infrahub_client):
        mock_infrahub_client.filters.return_value = []
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(use_case="dcfabric")

        assert result == []

    async def test_returns_single_device(self, mock_infrahub_client):
        dev_uuid = uuid4()
        iface = _make_interface_node(device_uuid=dev_uuid, name="ethernet-1/1", ip="10.1.1.0", prefix=31)
        bgp = _make_bgp_session_node(device_uuid=dev_uuid, local_asn=65000, peer_address="10.1.1.1", peer_asn=65001)
        node = _make_device_node(
            name="spine-01",
            device_id=dev_uuid,
            interfaces=[iface],
            bgp_sessions=[bgp],
        )
        mock_infrahub_client.filters.return_value = [node]
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(device_id=dev_uuid)

        assert len(result) == 1
        state = result[0]
        assert isinstance(state, DesiredState)
        assert state.device.name == "spine-01"
        assert state.device.id == dev_uuid
        assert len(state.interfaces) == 1
        assert state.interfaces[0].device_id == dev_uuid
        assert len(state.bgp_sessions) == 1

    async def test_filters_by_role(self, mock_infrahub_client):
        spine_nodes = [_make_device_node(name=f"spine-{i:02d}", role="spine") for i in range(1, 3)]
        mock_infrahub_client.filters.return_value = spine_nodes
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(role="spine")

        assert len(result) == 2
        assert all(state.device.role == "spine" for state in result)
        # The store must pass role to the SDK's filter call
        kwargs = mock_infrahub_client.filters.await_args.kwargs
        assert kwargs.get("role__value") == "spine" or kwargs.get("role") == "spine"

    async def test_filters_by_use_case(self, mock_infrahub_client):
        mock_infrahub_client.filters.return_value = [_make_device_node(name="spine-01", use_case="dcfabric")]
        store = InfrahubIntentStore(client=mock_infrahub_client)

        await store.get_desired_state(use_case="dcfabric")

        kwargs = mock_infrahub_client.filters.await_args.kwargs
        assert kwargs.get("use_case__value") == "dcfabric" or kwargs.get("use_case") == "dcfabric"

    async def test_filters_combine(self, mock_infrahub_client):
        mock_infrahub_client.filters.return_value = []
        store = InfrahubIntentStore(client=mock_infrahub_client)

        await store.get_desired_state(use_case="dcfabric", role="leaf", name="leaf-01")

        kwargs = mock_infrahub_client.filters.await_args.kwargs
        # All three filter keys must be present; keys may use __value suffix for the SDK
        assert any(k.startswith("use_case") for k in kwargs)
        assert any(k.startswith("role") for k in kwargs)
        assert any(k.startswith("name") for k in kwargs)

    async def test_connection_failure_raises_domain_exception(self, mock_infrahub_client):
        mock_infrahub_client.filters = AsyncMock(side_effect=OSError("connection refused"))
        store = InfrahubIntentStore(client=mock_infrahub_client)

        with pytest.raises(IntentConnectionError):
            await store.get_desired_state(use_case="dcfabric")

    async def test_device_id_filter_translated_to_uuid_string(self, mock_infrahub_client):
        mock_infrahub_client.filters.return_value = []
        store = InfrahubIntentStore(client=mock_infrahub_client)
        dev_uuid = uuid4()

        await store.get_desired_state(device_id=dev_uuid)

        kwargs = mock_infrahub_client.filters.await_args.kwargs
        # The SDK expects string UUIDs on the wire
        id_values = [v for k, v in kwargs.items() if k.startswith("id")]
        assert str(dev_uuid) in id_values


# ---------------------------------------------------------------------------
# US2 — provision_schema / seed
# ---------------------------------------------------------------------------


class TestProvisionSchema:
    async def test_returns_provision_result(self, mock_infrahub_client):
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.provision_schema("dcfabric")

        assert isinstance(result, ProvisionResult)
        assert result.use_case == "dcfabric"
        # Three batches = three await calls on schema.load
        assert mock_infrahub_client.schema.load.await_count == 3
        assert result.schemas_loaded >= 7

    async def test_translates_schema_validation_failure(self, mock_infrahub_client):
        mock_infrahub_client.schema.load.return_value = {
            "errors": [{"message": "unknown kind"}]
        }
        store = InfrahubIntentStore(client=mock_infrahub_client)

        with pytest.raises(IntentSchemaError) as exc_info:
            await store.provision_schema("dcfabric")

        assert "unknown kind" in str(exc_info.value)


class TestSeed:
    def _write_dataset(self, tmp_path: Path) -> Path:
        dataset = {
            "organization": {"name": "Test Org"},
            "manufacturer": {"name": "Nokia"},
            "platform": {"name": "SR Linux"},
            "location": {"name": "Lab"},
            "device_types": [{"name": "IXR-D2"}],
            "devices": [
                {
                    "name": "spine-01",
                    "role": "spine",
                    "use_case": "dcfabric",
                    "device_type": "IXR-D2",
                }
            ],
        }
        path = tmp_path / "topology.yml"
        path.write_text(yaml.safe_dump(dataset))
        return path

    async def test_seed_returns_seed_result(self, mock_infrahub_client, tmp_path: Path):
        # New records — nothing already exists.
        mock_infrahub_client.filters.return_value = []
        mock_infrahub_client.create.side_effect = lambda **_: MagicMock(save=AsyncMock())
        store = InfrahubIntentStore(client=mock_infrahub_client)
        data_path = self._write_dataset(tmp_path)

        result = await store.seed(
            "dcfabric", data_path=data_path, branch="feature-branch"
        )

        assert isinstance(result, SeedResult)
        assert result.use_case == "dcfabric"
        assert result.branch == "feature-branch"
        assert result.devices_created == 1

    async def test_seed_default_branch_is_main(self, mock_infrahub_client, tmp_path: Path):
        mock_infrahub_client.filters.return_value = []
        mock_infrahub_client.create.side_effect = lambda **_: MagicMock(save=AsyncMock())
        store = InfrahubIntentStore(client=mock_infrahub_client)
        data_path = self._write_dataset(tmp_path)

        result = await store.seed("dcfabric", data_path=data_path)

        assert result.branch == "main"

    async def test_seed_validation_error(self, mock_infrahub_client, tmp_path: Path):
        dataset = {"devices": [{"name": "incomplete"}]}
        path = tmp_path / "bad.yml"
        path.write_text(yaml.safe_dump(dataset))
        store = InfrahubIntentStore(client=mock_infrahub_client)

        with pytest.raises(IntentValidationError):
            await store.seed("dcfabric", data_path=path)
