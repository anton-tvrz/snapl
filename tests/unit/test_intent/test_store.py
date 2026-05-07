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
    IntentDeletionError,
    IntentNotFoundError,
    IntentSchemaError,
    IntentValidationError,
)
from snapl_intent.infrahub.store import InfrahubIntentStore
from snapl_intent.models import DeleteResult, DesiredState, ProvisionResult, Schema, SeedResult

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
        # Infrahub's GraphQL expects ``ids: [String]`` rather than a single
        # ``id`` argument, so the store wraps the UUID in a one-element list.
        assert kwargs.get("ids") == [str(dev_uuid)]


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
        mock_infrahub_client.schema.load.return_value = {"errors": [{"message": "unknown kind"}]}
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
            "location": {"name": "Lab", "shortname": "lab"},
            "device_types": [{"name": "IXR-D2", "manufacturer": "Nokia"}],
            "devices": [
                {
                    "name": "spine-01",
                    "role": "spine",
                    "use_case": "dcfabric",
                    "device_type": "IXR-D2",
                    "location": "lab",
                }
            ],
        }
        path = tmp_path / "topology.yml"
        path.write_text(yaml.safe_dump(dataset))
        return path

    @staticmethod
    def _wire_peer_resolver(client: MagicMock) -> None:
        """Return stub peers for relationship-resolution filter calls.

        The same stub shape doubles as an "existing" node when the ingester's
        upsert lookup hits the same kind — the stub has an awaitable ``save``
        so the update path completes without blowing up. Semantic idempotency
        is covered by ``test_seed.py``; here we only need the end-to-end
        call chain to succeed so the :class:`SeedResult` assertions fire.
        """
        peer_kinds = {
            "OrganizationManufacturer",
            "DcimDeviceType",
            "LocationSite",
        }

        async def fake_filters(*, kind: str, **_kwargs):
            if kind in peer_kinds:
                stub = MagicMock()
                stub.id = f"{kind}-peer-id"
                stub.save = AsyncMock()
                return [stub]
            return []

        client.filters = AsyncMock(side_effect=fake_filters)

    async def test_seed_returns_seed_result(self, mock_infrahub_client, tmp_path: Path):
        self._wire_peer_resolver(mock_infrahub_client)
        mock_infrahub_client.create.side_effect = lambda **_: MagicMock(save=AsyncMock())
        store = InfrahubIntentStore(client=mock_infrahub_client)
        data_path = self._write_dataset(tmp_path)

        result = await store.seed("dcfabric", data_path=data_path, branch="feature-branch")

        assert isinstance(result, SeedResult)
        assert result.use_case == "dcfabric"
        assert result.branch == "feature-branch"
        assert result.total_records > 0
        assert result.devices_created == 1

    async def test_seed_default_branch_is_main(self, mock_infrahub_client, tmp_path: Path):
        self._wire_peer_resolver(mock_infrahub_client)
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


# ---------------------------------------------------------------------------
# US3 — get_schema
# ---------------------------------------------------------------------------


def _schema_kind(kind: str) -> SimpleNamespace:
    """Return a minimal stand-in for ``NodeSchemaAPI`` — only ``kind`` matters here."""
    return SimpleNamespace(kind=kind)


class TestGetSchema:
    async def test_returns_schema_for_known_use_case(self, mock_infrahub_client):
        # schema.all() returns a mixture of Infrahub built-ins and our kinds —
        # the store should filter down to project namespaces and sort.
        mock_infrahub_client.schema.all.return_value = {
            "BuiltinTag": _schema_kind("BuiltinTag"),
            "CoreAccount": _schema_kind("CoreAccount"),
            "DcimDevice": _schema_kind("DcimDevice"),
            "InterfacePhysical": _schema_kind("InterfacePhysical"),
            "IpamPrefix": _schema_kind("IpamPrefix"),
            "RoutingBGPSession": _schema_kind("RoutingBGPSession"),
        }
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_schema("dcfabric")

        assert isinstance(result, Schema)
        assert result.use_case == "dcfabric"
        assert "DcimDevice" in result.entities
        assert "InterfacePhysical" in result.entities
        assert "RoutingBGPSession" in result.entities
        # Infrahub built-ins must not leak through.
        assert "BuiltinTag" not in result.entities
        assert "CoreAccount" not in result.entities
        # Source files are discovered from the packaged schemas tree.
        assert result.source_files, "expected non-empty source_files"
        assert all(f.endswith(".yml") for f in result.source_files)

    async def test_unknown_use_case_raises_schema_error(self, mock_infrahub_client):
        store = InfrahubIntentStore(client=mock_infrahub_client)

        with pytest.raises(IntentSchemaError):
            await store.get_schema("definitely-not-a-real-use-case")

    async def test_no_provisioned_kinds_raises_schema_error(self, mock_infrahub_client):
        # Infrahub is reachable but only built-ins are present — schema was
        # never provisioned for this project.
        mock_infrahub_client.schema.all.return_value = {
            "BuiltinTag": _schema_kind("BuiltinTag"),
            "CoreAccount": _schema_kind("CoreAccount"),
        }
        store = InfrahubIntentStore(client=mock_infrahub_client)

        with pytest.raises(IntentSchemaError):
            await store.get_schema("dcfabric")

    async def test_connection_error_translated(self, mock_infrahub_client):
        mock_infrahub_client.schema.all.side_effect = OSError("connection refused")
        store = InfrahubIntentStore(client=mock_infrahub_client)

        with pytest.raises(IntentConnectionError):
            await store.get_schema("dcfabric")


# ---------------------------------------------------------------------------
# US4 — use-case isolation
# ---------------------------------------------------------------------------


class TestUseCaseIsolation:
    async def test_get_desired_state_passes_use_case_filter_to_sdk(self, mock_infrahub_client):
        mock_infrahub_client.filters.return_value = []
        store = InfrahubIntentStore(client=mock_infrahub_client)

        await store.get_desired_state(use_case="dcfabric")

        kwargs = mock_infrahub_client.filters.await_args.kwargs
        assert kwargs.get("use_case__value") == "dcfabric"

    async def test_get_desired_state_without_use_case_sends_no_filter(self, mock_infrahub_client):
        mock_infrahub_client.filters.return_value = []
        store = InfrahubIntentStore(client=mock_infrahub_client)

        await store.get_desired_state()

        kwargs = mock_infrahub_client.filters.await_args.kwargs
        assert "use_case__value" not in kwargs
        assert "use_case" not in kwargs

    async def test_get_desired_state_returns_only_sdk_result_for_use_case(self, mock_infrahub_client):
        dev_uuid = uuid4()
        dcfabric_node = _make_device_node(name="spine-01", use_case="dcfabric", device_id=dev_uuid)
        mock_infrahub_client.filters.return_value = [dcfabric_node]
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(use_case="dcfabric")

        assert len(result) == 1
        assert result[0].device.use_case == "dcfabric"
        assert result[0].device.name == "spine-01"

    async def test_different_use_cases_produce_independent_queries(self, mock_infrahub_client):
        mock_infrahub_client.filters.return_value = []
        store = InfrahubIntentStore(client=mock_infrahub_client)

        await store.get_desired_state(use_case="dcfabric")
        await store.get_desired_state(use_case="test_edge")

        calls = mock_infrahub_client.filters.await_args_list
        assert calls[0].kwargs.get("use_case__value") == "dcfabric"
        assert calls[1].kwargs.get("use_case__value") == "test_edge"


# ---------------------------------------------------------------------------
# Phase 7 — T036: delete_device
# ---------------------------------------------------------------------------


class TestDeleteDevice:
    def _make_deletable_node(
        self,
        *,
        device_id: UUID | None = None,
        name: str = "spine-01",
        num_interfaces: int = 2,
        num_sessions: int = 1,
    ):
        """Build a SimpleNamespace device node with a delete coroutine attached."""
        dev_uuid = device_id or uuid4()
        ifaces = [SimpleNamespace(id=str(uuid4())) for _ in range(num_interfaces)]
        sessions = [SimpleNamespace(id=str(uuid4())) for _ in range(num_sessions)]
        node = _make_device_node(
            name=name,
            device_id=dev_uuid,
            interfaces=ifaces,
            bgp_sessions=sessions,
        )
        node.delete = AsyncMock()
        return node, dev_uuid

    async def test_delete_device_returns_delete_result(self, mock_infrahub_client):
        node, dev_uuid = self._make_deletable_node()
        mock_infrahub_client.filters.return_value = [node]
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.delete_device(dev_uuid)

        assert isinstance(result, DeleteResult)
        assert result.device_id == dev_uuid
        assert result.device_name == "spine-01"

    async def test_delete_device_counts_children_in_records_removed(self, mock_infrahub_client):
        node, dev_uuid = self._make_deletable_node(num_interfaces=3, num_sessions=2)
        mock_infrahub_client.filters.return_value = [node]
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.delete_device(dev_uuid)

        # 1 device + 3 interfaces + 2 bgp_sessions = 6
        assert result.records_removed == 6

    async def test_delete_device_calls_node_delete_once(self, mock_infrahub_client):
        node, dev_uuid = self._make_deletable_node()
        mock_infrahub_client.filters.return_value = [node]
        store = InfrahubIntentStore(client=mock_infrahub_client)

        await store.delete_device(dev_uuid)

        node.delete.assert_awaited_once()

    async def test_delete_device_queries_with_prefetch(self, mock_infrahub_client):
        node, dev_uuid = self._make_deletable_node()
        mock_infrahub_client.filters.return_value = [node]
        store = InfrahubIntentStore(client=mock_infrahub_client)

        await store.delete_device(dev_uuid)

        kwargs = mock_infrahub_client.filters.await_args.kwargs
        assert kwargs.get("prefetch_relationships") is True

    async def test_delete_device_not_found_raises_not_found_error(self, mock_infrahub_client):
        mock_infrahub_client.filters.return_value = []
        store = InfrahubIntentStore(client=mock_infrahub_client)

        with pytest.raises(IntentNotFoundError):
            await store.delete_device(uuid4())

    async def test_delete_device_sdk_failure_raises_deletion_error(self, mock_infrahub_client):
        node, dev_uuid = self._make_deletable_node()
        node.delete = AsyncMock(side_effect=Exception("lock conflict"))
        mock_infrahub_client.filters.return_value = [node]
        store = InfrahubIntentStore(client=mock_infrahub_client)

        with pytest.raises(IntentDeletionError):
            await store.delete_device(dev_uuid)


# ---------------------------------------------------------------------------
# Phase 7 — T038: branch parameter support
# ---------------------------------------------------------------------------


class TestBranchSupport:
    async def test_get_desired_state_passes_explicit_branch_to_sdk(self, mock_infrahub_client):
        mock_infrahub_client.filters.return_value = []
        store = InfrahubIntentStore(client=mock_infrahub_client)

        await store.get_desired_state(branch="feature-branch")

        kwargs = mock_infrahub_client.filters.await_args.kwargs
        assert kwargs.get("branch") == "feature-branch"

    async def test_get_desired_state_uses_store_default_branch(self, mock_infrahub_client):
        mock_infrahub_client.filters.return_value = []
        store = InfrahubIntentStore(client=mock_infrahub_client, branch="staging")

        await store.get_desired_state()

        kwargs = mock_infrahub_client.filters.await_args.kwargs
        assert kwargs.get("branch") == "staging"

    async def test_get_desired_state_per_call_branch_overrides_default(self, mock_infrahub_client):
        mock_infrahub_client.filters.return_value = []
        store = InfrahubIntentStore(client=mock_infrahub_client, branch="main")

        await store.get_desired_state(branch="hotfix-branch")

        kwargs = mock_infrahub_client.filters.await_args.kwargs
        assert kwargs.get("branch") == "hotfix-branch"
