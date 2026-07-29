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


def _make_interface_node(
    *,
    device_uuid: UUID,
    name: str,
    ip: str | None = None,
    status: str = "active",
    mtu: int | None = 9214,
    description: str | None = None,
    peer_device: str | None = None,
    peer_interface: str | None = None,
):
    # Mirrors the live InterfacePhysical shape (#33): the IP lives on an
    # ``ip_addresses`` relation whose peer carries a CIDR ``address``; there
    # are no ``ip_address``/``prefix_length``/``enabled`` attributes.
    ip_peers = [_rel_one(SimpleNamespace(address=_attr(ip)))] if ip else []
    return SimpleNamespace(
        id=str(uuid4()),
        device=_rel_one(SimpleNamespace(id=str(device_uuid))),
        name=_attr(name),
        description=_attr(description),
        status=_attr(status),
        speed=_attr(None),
        mtu=_attr(mtu),
        peer_device=_attr(peer_device),
        peer_interface=_attr(peer_interface),
        ip_addresses=_rel_many(ip_peers),
    )


def _make_bgp_session_node(
    *,
    device_uuid: UUID,
    local_asn: int,
    peer_address: str,
    peer_asn: int,
    peer_group: str | None = None,
    status: str = "active",
    export_policies: str | None = None,
    import_policies: str | None = None,
):
    # Mirrors the live RoutingBGPSession shape (#33): ASNs and IPs are
    # relations (local_as/remote_as → RoutingAutonomousSystem, remote_ip →
    # IpamIPAddress with CIDR address), not scalar attributes.
    return SimpleNamespace(
        id=str(uuid4()),
        device=_rel_one(SimpleNamespace(id=str(device_uuid))),
        local_as=_rel_one(SimpleNamespace(asn=_attr(local_asn))),
        remote_as=_rel_one(SimpleNamespace(asn=_attr(peer_asn))),
        local_ip=_rel_one(SimpleNamespace(address=_attr(None))),
        remote_ip=_rel_one(SimpleNamespace(address=_attr(peer_address))),
        peer_group=_rel_one(SimpleNamespace(name=_attr(peer_group))) if peer_group else _rel_one(None),
        status=_attr(status),
        description=_attr(None),
        export_policies=_attr(export_policies),
        import_policies=_attr(import_policies),
    )


def _dispatch_filters(mock, *, devices, interfaces=None, sessions=None):
    """Route mock ``client.filters`` calls by kind, like the live SDK."""

    async def _filters(kind=None, **kwargs):
        if kind == "DcimDevice":
            return list(devices)
        if kind == "InterfacePhysical":
            return list(interfaces or [])
        if kind == "RoutingBGPSession":
            return list(sessions or [])
        return []

    mock.filters = AsyncMock(name="client.filters", side_effect=_filters)


def _make_device_node(
    *,
    name: str,
    role: str = "spine",
    use_case: str = "dcfabric",
    management: str | None = "10.0.0.1/24",
    lab_node_name: str | None = None,
    device_id: UUID | None = None,
):
    # Mirrors the real Infrahub schema (schemas/network_device.yml): the
    # attribute is ``management_ip`` (IPHost, so CIDR-suffixed), not
    # ``management_address`` — that mismatch is what issue #31 fixed. Live
    # device nodes carry no usable interface/session peers (#33): those are
    # queried from the child side (InterfacePhysical / RoutingBGPSession).
    dev_uuid = device_id or uuid4()
    return SimpleNamespace(
        id=str(dev_uuid),
        name=_attr(name),
        management_ip=_attr(management),
        lab_node_name=_attr(lab_node_name),
        role=_attr(role),
        use_case=_attr(use_case),
        platform=_attr("nokia-srlinux"),
        description=_attr(None),
        interfaces=_rel_many([]),
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
        iface = _make_interface_node(device_uuid=dev_uuid, name="ethernet-1/1", ip="10.1.1.0/31")
        bgp = _make_bgp_session_node(device_uuid=dev_uuid, local_asn=65000, peer_address="10.1.1.1/31", peer_asn=65001)
        node = _make_device_node(name="spine-01", device_id=dev_uuid)
        _dispatch_filters(mock_infrahub_client, devices=[node], interfaces=[iface], sessions=[bgp])
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

    async def test_management_address_reads_management_ip_and_strips_prefix(self, mock_infrahub_client):
        """Regression for #31: the schema attribute is management_ip (IPHost,
        CIDR-suffixed); the Device contract carries a prefix-free address."""
        node = _make_device_node(name="spine-01", management="10.0.0.1/24")
        mock_infrahub_client.filters.return_value = [node]
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(use_case="dcfabric")

        assert result[0].device.management_address == "10.0.0.1"

    async def test_management_ip_as_ip_interface_object(self, mock_infrahub_client):
        """The live SDK materialises IPHost attributes as ipaddress objects,
        not strings — the mapping must cope with both."""
        from ipaddress import IPv4Interface

        node = _make_device_node(name="spine-01", management=IPv4Interface("10.0.0.1/24"))
        mock_infrahub_client.filters.return_value = [node]
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(use_case="dcfabric")

        assert result[0].device.management_address == "10.0.0.1"

    async def test_management_ip_missing_yields_empty_address(self, mock_infrahub_client):
        node = _make_device_node(name="spine-01", management=None)
        mock_infrahub_client.filters.return_value = [node]
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(use_case="dcfabric")

        assert result[0].device.management_address == ""

    async def test_lab_node_name_mapped(self, mock_infrahub_client):
        node = _make_device_node(name="spine-01", lab_node_name="clab-dcfabric-spine-01")
        mock_infrahub_client.filters.return_value = [node]
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(use_case="dcfabric")

        assert result[0].device.lab_node_name == "clab-dcfabric-spine-01"

    async def test_lab_node_name_absent_defaults_to_none(self, mock_infrahub_client):
        node = _make_device_node(name="spine-01")
        mock_infrahub_client.filters.return_value = [node]
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(use_case="dcfabric")

        assert result[0].device.lab_node_name is None

    async def test_filters_by_role(self, mock_infrahub_client):
        spine_nodes = [_make_device_node(name=f"spine-{i:02d}", role="spine") for i in range(1, 3)]
        mock_infrahub_client.filters.return_value = spine_nodes
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(role="spine")

        assert len(result) == 2
        assert all(state.device.role == "spine" for state in result)
        # The store must pass role to the SDK's filter call
        kwargs = mock_infrahub_client.filters.await_args_list[0].kwargs
        assert kwargs.get("role__value") == "spine" or kwargs.get("role") == "spine"

    async def test_filters_by_use_case(self, mock_infrahub_client):
        mock_infrahub_client.filters.return_value = [_make_device_node(name="spine-01", use_case="dcfabric")]
        store = InfrahubIntentStore(client=mock_infrahub_client)

        await store.get_desired_state(use_case="dcfabric")

        kwargs = mock_infrahub_client.filters.await_args_list[0].kwargs
        assert kwargs.get("use_case__value") == "dcfabric" or kwargs.get("use_case") == "dcfabric"

    async def test_filters_combine(self, mock_infrahub_client):
        mock_infrahub_client.filters.return_value = []
        store = InfrahubIntentStore(client=mock_infrahub_client)

        await store.get_desired_state(use_case="dcfabric", role="leaf", name="leaf-01")

        kwargs = mock_infrahub_client.filters.await_args_list[0].kwargs
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

        kwargs = mock_infrahub_client.filters.await_args_list[0].kwargs
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


class TestRelationshipQueries:
    """Regression for #33: live device nodes carry no usable interface/session
    peers, so get_desired_state must query InterfacePhysical and
    RoutingBGPSession from the child side with prefetched relations."""

    async def test_relation_queries_pin_call_signature(self, mock_infrahub_client):
        dev_uuid = uuid4()
        node = _make_device_node(name="spine-01", device_id=dev_uuid)
        _dispatch_filters(mock_infrahub_client, devices=[node])
        store = InfrahubIntentStore(client=mock_infrahub_client)

        await store.get_desired_state(use_case="dcfabric")

        calls = {c.kwargs.get("kind"): c.kwargs for c in mock_infrahub_client.filters.await_args_list}
        assert set(calls) == {"DcimDevice", "InterfacePhysical", "RoutingBGPSession"}
        for kind in ("InterfacePhysical", "RoutingBGPSession"):
            kwargs = calls[kind]
            assert kwargs.get("device__ids") == [str(dev_uuid)]
            assert kwargs.get("prefetch_relationships") is True
            assert kwargs.get("populate_store") is True

    async def test_no_relation_queries_without_devices(self, mock_infrahub_client):
        _dispatch_filters(mock_infrahub_client, devices=[])
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(use_case="dcfabric")

        assert result == []
        assert mock_infrahub_client.filters.await_count == 1

    async def test_interface_mapped_from_live_shape(self, mock_infrahub_client):
        dev_uuid = uuid4()
        node = _make_device_node(name="spine-01", device_id=dev_uuid)
        iface = _make_interface_node(
            device_uuid=dev_uuid,
            name="ethernet-1/1",
            ip="10.10.1.0/31",
            status="active",
            mtu=9214,
            description="to leaf-01:ethernet-1/49",
            peer_device="leaf-01",
            peer_interface="ethernet-1/49",
        )
        _dispatch_filters(mock_infrahub_client, devices=[node], interfaces=[iface])
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(use_case="dcfabric")

        (mapped,) = result[0].interfaces
        assert mapped.name == "ethernet-1/1"
        assert mapped.ip_address == "10.10.1.0"
        assert mapped.prefix_length == 31
        assert mapped.enabled is True
        assert mapped.mtu == 9214
        assert mapped.description == "to leaf-01:ethernet-1/49"
        assert mapped.peer_device == "leaf-01"
        assert mapped.peer_interface == "ethernet-1/49"

    async def test_interface_without_mtu_maps_to_none(self, mock_infrahub_client):
        """A null mtu in the SoT stays None — loopbacks must not inherit a
        fabric-port default the renderer would push to the device (#78)."""
        dev_uuid = uuid4()
        node = _make_device_node(name="spine-01", device_id=dev_uuid)
        iface = _make_interface_node(
            device_uuid=dev_uuid,
            name="lo0",
            ip="10.1.0.1/32",
            status="active",
            mtu=None,
        )
        _dispatch_filters(mock_infrahub_client, devices=[node], interfaces=[iface])
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(use_case="dcfabric")

        (mapped,) = result[0].interfaces
        assert mapped.mtu is None

    async def test_interface_without_ip_and_inactive_status(self, mock_infrahub_client):
        dev_uuid = uuid4()
        node = _make_device_node(name="spine-01", device_id=dev_uuid)
        iface = _make_interface_node(device_uuid=dev_uuid, name="ethernet-1/9", ip=None, status="disabled")
        _dispatch_filters(mock_infrahub_client, devices=[node], interfaces=[iface])
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(use_case="dcfabric")

        (mapped,) = result[0].interfaces
        assert mapped.ip_address is None
        assert mapped.prefix_length is None
        assert mapped.enabled is False

    async def test_bgp_session_mapped_from_live_shape(self, mock_infrahub_client):
        dev_uuid = uuid4()
        node = _make_device_node(name="spine-01", device_id=dev_uuid)
        bgp = _make_bgp_session_node(
            device_uuid=dev_uuid,
            local_asn=65000,
            peer_asn=65011,
            peer_address="10.10.1.1/31",
            peer_group="underlay-ipv4",
            export_policies="export-all",
        )
        _dispatch_filters(mock_infrahub_client, devices=[node], sessions=[bgp])
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(use_case="dcfabric")

        (mapped,) = result[0].bgp_sessions
        assert mapped.local_asn == 65000
        assert mapped.peer_asn == 65011
        assert mapped.peer_address == "10.10.1.1"
        assert mapped.peer_group == "underlay-ipv4"
        assert mapped.export_policy == "export-all"
        assert mapped.enabled is True

    async def test_relations_grouped_by_device(self, mock_infrahub_client):
        spine_id, leaf_id = uuid4(), uuid4()
        spine = _make_device_node(name="spine-01", device_id=spine_id)
        leaf = _make_device_node(name="leaf-01", role="leaf", device_id=leaf_id)
        ifaces = [
            _make_interface_node(device_uuid=spine_id, name="ethernet-1/1"),
            _make_interface_node(device_uuid=leaf_id, name="ethernet-1/49"),
            _make_interface_node(device_uuid=leaf_id, name="ethernet-1/50"),
        ]
        sessions = [
            _make_bgp_session_node(device_uuid=spine_id, local_asn=65000, peer_address="10.10.1.1/31", peer_asn=65011)
        ]
        _dispatch_filters(mock_infrahub_client, devices=[spine, leaf], interfaces=ifaces, sessions=sessions)
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = {s.device.name: s for s in await store.get_desired_state(use_case="dcfabric")}

        assert len(result["spine-01"].interfaces) == 1
        assert len(result["leaf-01"].interfaces) == 2
        assert len(result["spine-01"].bgp_sessions) == 1
        assert len(result["leaf-01"].bgp_sessions) == 0

    async def test_unresolvable_relation_peers_fall_back_to_defaults(self, mock_infrahub_client):
        """Live RelatedNode.peer raises when the peer isn't in the SDK store —
        mapping must degrade to defaults, not crash."""

        class _RaisingRel:
            @property
            def peer(self):
                raise ValueError("Unable to find the node in the store")

        dev_uuid = uuid4()
        node = _make_device_node(name="spine-01", device_id=dev_uuid)
        bgp = _make_bgp_session_node(device_uuid=dev_uuid, local_asn=65000, peer_address="10.10.1.1/31", peer_asn=65011)
        bgp.local_as = _RaisingRel()
        bgp.peer_group = _RaisingRel()
        _dispatch_filters(mock_infrahub_client, devices=[node], sessions=[bgp])
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(use_case="dcfabric")

        (mapped,) = result[0].bgp_sessions
        assert mapped.local_asn == 0
        assert mapped.peer_group is None
        assert mapped.peer_asn == 65011

    async def test_unresolvable_interface_ip_peer_falls_back_to_none(self, mock_infrahub_client):
        """A cardinality-many ip_addresses peer whose live .peer property raises
        (SDK store miss) must degrade to ip_address=None, not crash the whole
        get_desired_state call (#45)."""

        class _RaisingRel:
            @property
            def peer(self):
                raise ValueError("Unable to find the node in the store")

        dev_uuid = uuid4()
        node = _make_device_node(name="spine-01", device_id=dev_uuid)
        iface = _make_interface_node(device_uuid=dev_uuid, name="ethernet-1/1")
        iface.ip_addresses = _rel_many([_RaisingRel()])
        _dispatch_filters(mock_infrahub_client, devices=[node], interfaces=[iface])
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(use_case="dcfabric")

        (mapped,) = result[0].interfaces
        assert mapped.ip_address is None
        assert mapped.prefix_length is None

    async def test_dual_stack_interface_prefers_ipv4_deterministically(self, mock_infrahub_client):
        """A dual-stack interface exposes both a v6 and a v4 ip_addresses peer in
        unstable relation order; mapping must deterministically pick the IPv4
        address so rendered config and drift comparison don't flip (#48)."""
        dev_uuid = uuid4()
        node = _make_device_node(name="spine-01", device_id=dev_uuid)
        iface = _make_interface_node(device_uuid=dev_uuid, name="ethernet-1/1")
        # v6 peer listed *before* the v4 peer — the first-truthy loop would pick v6.
        iface.ip_addresses = _rel_many(
            [
                _rel_one(SimpleNamespace(address=_attr("2001:db8::1/64"))),
                _rel_one(SimpleNamespace(address=_attr("10.10.1.0/31"))),
            ]
        )
        _dispatch_filters(mock_infrahub_client, devices=[node], interfaces=[iface])
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(use_case="dcfabric")

        (mapped,) = result[0].interfaces
        assert mapped.ip_address == "10.10.1.0"
        assert mapped.prefix_length == 31

    async def test_interface_device_peer_id_as_uuid_object(self, mock_infrahub_client):
        """Some SDK nodes expose ``id`` as a ``uuid.UUID`` object rather than a
        string; the peer side must stringify it (as the device side already does)
        or ``UUID(peer_id)`` raises and the interface is dropped (#48)."""
        dev_uuid = uuid4()
        node = _make_device_node(name="spine-01", device_id=dev_uuid)
        iface = _make_interface_node(device_uuid=dev_uuid, name="ethernet-1/1", ip="10.10.1.0/31")
        # Live SDK sometimes hands back a UUID object, not str.
        iface.device = _rel_one(SimpleNamespace(id=dev_uuid))
        _dispatch_filters(mock_infrahub_client, devices=[node], interfaces=[iface])
        store = InfrahubIntentStore(client=mock_infrahub_client)

        result = await store.get_desired_state(use_case="dcfabric")

        (mapped,) = result[0].interfaces
        assert mapped.device_id == dev_uuid


class TestUseCaseIsolation:
    async def test_get_desired_state_passes_use_case_filter_to_sdk(self, mock_infrahub_client):
        mock_infrahub_client.filters.return_value = []
        store = InfrahubIntentStore(client=mock_infrahub_client)

        await store.get_desired_state(use_case="dcfabric")

        kwargs = mock_infrahub_client.filters.await_args_list[0].kwargs
        assert kwargs.get("use_case__value") == "dcfabric"

    async def test_get_desired_state_without_use_case_sends_no_filter(self, mock_infrahub_client):
        mock_infrahub_client.filters.return_value = []
        store = InfrahubIntentStore(client=mock_infrahub_client)

        await store.get_desired_state()

        kwargs = mock_infrahub_client.filters.await_args_list[0].kwargs
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
        node = _make_device_node(name=name, device_id=dev_uuid)
        # delete_device still reads embedded peers off the device node
        # (prefetch path) — out of #33's scope, so keep that shape here.
        node.interfaces = _rel_many(ifaces)
        node.bgp_sessions = _rel_many(sessions)
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

        kwargs = mock_infrahub_client.filters.await_args_list[0].kwargs
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

        kwargs = mock_infrahub_client.filters.await_args_list[0].kwargs
        assert kwargs.get("branch") == "feature-branch"

    async def test_get_desired_state_uses_store_default_branch(self, mock_infrahub_client):
        mock_infrahub_client.filters.return_value = []
        store = InfrahubIntentStore(client=mock_infrahub_client, branch="staging")

        await store.get_desired_state()

        kwargs = mock_infrahub_client.filters.await_args_list[0].kwargs
        assert kwargs.get("branch") == "staging"

    async def test_get_desired_state_per_call_branch_overrides_default(self, mock_infrahub_client):
        mock_infrahub_client.filters.return_value = []
        store = InfrahubIntentStore(client=mock_infrahub_client, branch="main")

        await store.get_desired_state(branch="hotfix-branch")

        kwargs = mock_infrahub_client.filters.await_args_list[0].kwargs
        assert kwargs.get("branch") == "hotfix-branch"
