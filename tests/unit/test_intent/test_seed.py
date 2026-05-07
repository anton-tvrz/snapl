"""Unit tests for dependency-ordered data ingestion (seed).

Covers YAML parsing, dependency ordering of ``SEED_ORDER``, upsert-by-default
semantics, validation rejection, and idempotent re-run behaviour. Uses a mock
SDK client so no Infrahub is needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from snapl_intent.exceptions import IntentConnectionError, IntentValidationError
from snapl_intent.infrahub.seed import (
    SEED_DEFERRED,
    SEED_ORDER,
    SeedIngester,
    load_seed_file,
)

# Full dependency order = SEED_ORDER plus remaining deferred sections
# (IP-namespace, interfaces, RoutingProtocol shadow copies — see
# T028-followup). Invariants below cover the whole chain so they remain
# meaningful as deferred sections graduate into SEED_ORDER.
FULL_ORDER: list[str] = SEED_ORDER + SEED_DEFERRED

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# YAML parsing
# ---------------------------------------------------------------------------


class TestLoadSeedFile:
    def test_load_valid_yaml(self, tmp_path: Path):
        path = tmp_path / "topology.yml"
        path.write_text(yaml.safe_dump({"devices": [{"name": "spine-01"}]}))

        data = load_seed_file(path)

        assert data["devices"][0]["name"] == "spine-01"

    def test_load_empty_file_returns_empty_dict(self, tmp_path: Path):
        path = tmp_path / "empty.yml"
        path.write_text("")

        assert load_seed_file(path) == {}

    def test_load_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_seed_file(tmp_path / "nope.yml")

    def test_load_malformed_yaml_raises_validation(self, tmp_path: Path):
        path = tmp_path / "bad.yml"
        path.write_text("devices:\n  - name: spine-01\n   bad-indent")

        with pytest.raises(IntentValidationError):
            load_seed_file(path)


# ---------------------------------------------------------------------------
# Dependency ordering
# ---------------------------------------------------------------------------


class TestSeedOrder:
    def test_supporting_entities_precede_devices(self):
        assert FULL_ORDER.index("organization") < FULL_ORDER.index("devices")
        assert FULL_ORDER.index("manufacturer") < FULL_ORDER.index("devices")
        assert FULL_ORDER.index("platform") < FULL_ORDER.index("devices")
        assert FULL_ORDER.index("device_types") < FULL_ORDER.index("devices")
        assert FULL_ORDER.index("location") < FULL_ORDER.index("devices")
        assert FULL_ORDER.index("autonomous_systems") < FULL_ORDER.index("devices")

    def test_interfaces_come_after_devices(self):
        assert FULL_ORDER.index("devices") < FULL_ORDER.index("interfaces")

    def test_bgp_sessions_come_last(self):
        assert FULL_ORDER.index("bgp_peer_groups") < FULL_ORDER.index("bgp_sessions")
        assert FULL_ORDER.index("interfaces") < FULL_ORDER.index("bgp_sessions")

    def test_active_and_deferred_are_disjoint(self):
        assert set(SEED_ORDER).isdisjoint(SEED_DEFERRED)

    def test_vrfs_precede_devices(self):
        assert SEED_ORDER.index("vrfs") < SEED_ORDER.index("devices")

    def test_ip_prefixes_precede_devices(self):
        assert SEED_ORDER.index("ip_prefixes") < SEED_ORDER.index("devices")

    def test_vrfs_follow_autonomous_systems(self):
        assert SEED_ORDER.index("autonomous_systems") < SEED_ORDER.index("vrfs")

    def test_interfaces_in_seed_order(self):
        assert "interfaces" in SEED_ORDER

    def test_interfaces_not_deferred(self):
        assert "interfaces" not in SEED_DEFERRED

    def test_interfaces_follow_devices_in_seed_order(self):
        assert SEED_ORDER.index("devices") < SEED_ORDER.index("interfaces")


# ---------------------------------------------------------------------------
# Ingester — order, upsert, idempotency
# ---------------------------------------------------------------------------


def _stub_node(name: str = "name") -> MagicMock:
    """Return a fake Infrahub node with a ``save`` coroutine."""
    node = MagicMock(name=name)
    node.save = AsyncMock()
    return node


class TestSeedIngester:
    def _make_client(self) -> MagicMock:
        client = MagicMock()
        # ``create`` returns a node which the ingester then saves.
        client.create = AsyncMock(side_effect=lambda **kwargs: _stub_node(name=kwargs.get("kind", "node")))
        # ``all`` returns existing nodes — empty for a first run.
        client.all = AsyncMock(return_value=[])
        client.filters = AsyncMock(return_value=[])
        return client

    async def test_ingest_upserts_in_declared_order(self, tmp_path: Path):
        # Attribute-only sections — no relationship resolution needed, so the
        # default ``filters=[]`` mock is sufficient. Relationship resolution is
        # covered by ``test_ingest_resolves_relationships_to_peer_ids`` below.
        dataset: dict[str, Any] = {
            "organization": {"name": "Test Org"},
            "location": {"name": "Lab", "shortname": "lab"},
            "manufacturer": {"name": "Nokia"},
            "platform": {"name": "SR Linux"},
        }
        path = tmp_path / "topology.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = self._make_client()
        ingester = SeedIngester(client=client)

        result = await ingester.seed(use_case="dcfabric", data_path=path, branch="main")

        assert client.create.await_count == len(dataset)
        create_kinds = [call.kwargs.get("kind") for call in client.create.await_args_list]
        assert any("Organization" in (k or "") for k in create_kinds)
        assert result.use_case == "dcfabric"
        assert result.branch == "main"
        assert result.devices_created == 0
        assert result.total_records == len(dataset)

    async def test_ingest_resolves_relationships_to_peer_ids(self, tmp_path: Path):
        dataset: dict[str, Any] = {
            "device_types": [
                {"name": "IXR-D2", "manufacturer": "Nokia"},
            ],
        }
        path = tmp_path / "topology.yml"
        path.write_text(yaml.safe_dump(dataset))

        peer = _stub_node()
        peer.id = "mfr-1"

        async def fake_filters(*, kind: str, **kwargs: Any) -> list[Any]:
            if kind == "OrganizationManufacturer" and kwargs.get("name__value") == "Nokia":
                return [peer]
            return []

        client = self._make_client()
        client.filters = AsyncMock(side_effect=fake_filters)
        ingester = SeedIngester(client=client)

        await ingester.seed(use_case="dcfabric", data_path=path)

        device_type_call = next(
            call
            for call in client.create.await_args_list
            if call.kwargs.get("kind") == "DcimDeviceType"
        )
        assert device_type_call.kwargs["data"]["manufacturer"] == "mfr-1"

    async def test_ingest_raises_when_relationship_peer_missing(self, tmp_path: Path):
        dataset: dict[str, Any] = {
            "device_types": [
                {"name": "IXR-D2", "manufacturer": "Unknown"},
            ],
        }
        path = tmp_path / "topology.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = self._make_client()
        ingester = SeedIngester(client=client)

        with pytest.raises(IntentValidationError, match="Unresolved manufacturer"):
            await ingester.seed(use_case="dcfabric", data_path=path)

    async def test_ingest_second_run_upserts_in_place(self, tmp_path: Path):
        dataset: dict[str, Any] = {
            "organization": {"name": "Test Org"},
            "location": {"name": "Lab", "shortname": "lab"},
            "manufacturer": {"name": "Nokia"},
            "platform": {"name": "SR Linux"},
        }
        path = tmp_path / "topology.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = self._make_client()
        ingester = SeedIngester(client=client)

        await ingester.seed(use_case="dcfabric", data_path=path)
        create_calls_before = client.create.await_count

        # Second run: ``filters`` now returns an existing node so the ingester
        # takes the update branch instead of calling ``create``.
        existing = _stub_node(name="existing")
        existing.name = MagicMock(value="Test Org")
        client.filters.return_value = [existing]

        result = await ingester.seed(use_case="dcfabric", data_path=path)

        # No new create calls after the second seed — everything was an upsert.
        assert client.create.await_count == create_calls_before
        assert result.total_records == len(dataset)

    async def test_missing_data_path_raises(self, tmp_path: Path):
        client = self._make_client()
        ingester = SeedIngester(client=client)

        with pytest.raises(FileNotFoundError):
            await ingester.seed(use_case="dcfabric", data_path=tmp_path / "nope.yml")

    async def test_validation_error_for_device_without_required_fields(self, tmp_path: Path):
        # Device missing required 'role' — ingester should refuse before SDK.
        dataset = {"devices": [{"name": "incomplete"}]}
        path = tmp_path / "bad.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = self._make_client()
        ingester = SeedIngester(client=client)

        with pytest.raises(IntentValidationError):
            await ingester.seed(use_case="dcfabric", data_path=path)

    async def test_connection_failure_translated(self, tmp_path: Path):
        dataset = {"organization": {"name": "Test Org"}}
        path = tmp_path / "topology.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = self._make_client()
        client.create.side_effect = OSError("connection refused")
        ingester = SeedIngester(client=client)

        with pytest.raises(IntentConnectionError):
            await ingester.seed(use_case="dcfabric", data_path=path)


# ---------------------------------------------------------------------------
# Namespace bootstrap (Milestone B)
# ---------------------------------------------------------------------------


class TestNamespaceBootstrap:
    """Default IpamNamespace ID is looked up once and injected into vrfs/ip_prefixes."""

    def _make_client_with_namespace(self, ns_id: str = "ns-1") -> MagicMock:
        ns = _stub_node("namespace")
        ns.id = ns_id

        async def fake_filters(*, kind: str, **kwargs: Any) -> list[Any]:
            if kind == "IpamNamespace":
                return [ns]
            return []

        client = MagicMock()
        client.create = AsyncMock(side_effect=lambda **kwargs: _stub_node(kwargs.get("kind", "node")))
        client.filters = AsyncMock(side_effect=fake_filters)
        return client

    async def test_vrfs_inject_namespace_id(self, tmp_path: Path):
        dataset = {"vrfs": [{"name": "default", "description": "Global VRF"}]}
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = self._make_client_with_namespace("ns-1")
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        vrf_call = next(
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "IpamVRF"
        )
        assert vrf_call.kwargs["data"]["namespace"] == "ns-1"

    async def test_ip_prefixes_inject_namespace_id(self, tmp_path: Path):
        dataset = {"ip_prefixes": [{"prefix": "10.0.0.0/8", "status": "active"}]}
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = self._make_client_with_namespace("ns-1")
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        prefix_call = next(
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "IpamPrefix"
        )
        assert prefix_call.kwargs["data"]["ip_namespace"] == "ns-1"

    async def test_missing_namespace_raises_validation_error(self, tmp_path: Path):
        dataset = {"vrfs": [{"name": "default"}]}
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = MagicMock()
        client.filters = AsyncMock(return_value=[])
        client.create = AsyncMock(side_effect=lambda **_: _stub_node())
        ingester = SeedIngester(client=client)

        with pytest.raises(IntentValidationError, match="namespace"):
            await ingester.seed(use_case="dcfabric", data_path=path)

    async def test_namespace_fetched_once_for_multiple_sections(self, tmp_path: Path):
        dataset = {
            "vrfs": [{"name": "default"}, {"name": "mgmt"}],
            "ip_prefixes": [{"prefix": "10.0.0.0/8", "status": "active"}],
        }
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = self._make_client_with_namespace("ns-1")
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        ns_calls = [
            c for c in client.filters.await_args_list
            if c.kwargs.get("kind") == "IpamNamespace"
        ]
        assert len(ns_calls) == 1


# ---------------------------------------------------------------------------
# Milestone C — interfaces + IP materialisation
# ---------------------------------------------------------------------------


def _make_client_with_ns_and_peers(
    ns_id: str = "ns-1",
    device_id: str = "dev-1",
) -> MagicMock:
    """Client stub that resolves IpamNamespace, DcimDevice and common device peers."""
    ns = _stub_node("namespace")
    ns.id = ns_id
    device = _stub_node("device")
    device.id = device_id

    async def fake_filters(*, kind: str, **kwargs: Any) -> list[Any]:
        if kind == "IpamNamespace":
            return [ns]
        if kind == "DcimDevice":
            return [device]
        if kind in ("DcimDeviceType", "DcimPlatform", "LocationSite", "RoutingAutonomousSystem"):
            peer = _stub_node(kind)
            peer.id = f"{kind}-id"
            return [peer]
        # upsert lookups (InterfacePhysical, IpamIPAddress, ...) — nothing exists yet
        return []

    client = MagicMock()
    client.create = AsyncMock(side_effect=lambda **kw: _stub_node(kw.get("kind", "node")))
    client.filters = AsyncMock(side_effect=fake_filters)
    return client


class TestInterfaceSeeding:
    """Milestone C — interfaces promoted from SEED_DEFERRED to SEED_ORDER."""

    async def test_interface_device_relationship_resolved(self, tmp_path: Path):
        dataset = {
            "interfaces": [
                {"device": "spine-01", "name": "ethernet-1/1", "role": "fabric"},
            ]
        }
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = _make_client_with_ns_and_peers(device_id="dev-1")
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        iface_call = next(
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "InterfacePhysical"
        )
        assert iface_call.kwargs["data"]["device"] == "dev-1"

    async def test_interface_ip_address_materialised(self, tmp_path: Path):
        """ip_address on an interface creates an IpamIPAddress node."""
        dataset = {
            "interfaces": [
                {
                    "device": "spine-01",
                    "name": "ethernet-1/1",
                    "role": "fabric",
                    "ip_address": "10.10.1.0/31",
                },
            ]
        }
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = _make_client_with_ns_and_peers()
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        ip_call = next(
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "IpamIPAddress"
        )
        assert ip_call.kwargs["data"]["address"] == "10.10.1.0/31"
        assert ip_call.kwargs["data"]["ip_namespace"] == "ns-1"

    async def test_interface_ip_addresses_rel_populated(self, tmp_path: Path):
        """ip_address materialisation wires IpamIPAddress id into ip_addresses list."""
        ip_node = _stub_node("ip")
        ip_node.id = "ip-1"

        async def fake_create(**kw: Any) -> MagicMock:
            if kw.get("kind") == "IpamIPAddress":
                return ip_node
            return _stub_node(kw.get("kind", "node"))

        dataset = {
            "interfaces": [
                {
                    "device": "spine-01",
                    "name": "ethernet-1/1",
                    "role": "fabric",
                    "ip_address": "10.10.1.0/31",
                },
            ]
        }
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = _make_client_with_ns_and_peers()
        client.create = AsyncMock(side_effect=fake_create)
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        iface_call = next(
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "InterfacePhysical"
        )
        assert iface_call.kwargs["data"].get("ip_addresses") == ["ip-1"]

    async def test_interface_without_ip_skips_materialisation(self, tmp_path: Path):
        """Interface with no ip_address creates no IpamIPAddress."""
        dataset = {
            "interfaces": [
                {"device": "spine-01", "name": "loopback0", "role": "loopback"},
            ]
        }
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = _make_client_with_ns_and_peers()
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        ip_creates = [
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "IpamIPAddress"
        ]
        assert ip_creates == []

    async def test_interface_lookup_uses_device_ids_filter(self, tmp_path: Path):
        """Upsert lookup for existing InterfacePhysical uses device__ids, not device__value."""
        dataset = {
            "interfaces": [
                {"device": "spine-01", "name": "ethernet-1/1", "role": "fabric"},
            ]
        }
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = _make_client_with_ns_and_peers(device_id="dev-42")
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        iface_lookup = next(
            c for c in client.filters.await_args_list
            if c.kwargs.get("kind") == "InterfacePhysical"
        )
        assert iface_lookup.kwargs.get("device__ids") == ["dev-42"]
        assert "device__value" not in iface_lookup.kwargs

    async def test_interface_idempotency_on_second_run(self, tmp_path: Path):
        """Second seed run finds existing InterfacePhysical and updates in place."""
        dataset = {
            "interfaces": [
                {"device": "spine-01", "name": "ethernet-1/1", "role": "fabric"},
            ]
        }
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        existing_iface = _stub_node("existing-iface")

        async def fake_filters_second(*, kind: str, **kwargs: Any) -> list[Any]:
            ns = _stub_node("namespace")
            ns.id = "ns-1"
            device = _stub_node("device")
            device.id = "dev-1"
            if kind == "IpamNamespace":
                return [ns]
            if kind == "DcimDevice":
                return [device]
            if kind == "InterfacePhysical":
                return [existing_iface]
            return []

        client = _make_client_with_ns_and_peers()
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)
        creates_after_first = client.create.await_count

        client.filters = AsyncMock(side_effect=fake_filters_second)
        await ingester.seed(use_case="dcfabric", data_path=path)

        # No new InterfacePhysical created on second run.
        assert client.create.await_count == creates_after_first


# ---------------------------------------------------------------------------
# Milestone C (Broad) — management_ip → IpamIPAddress + primary_address
# ---------------------------------------------------------------------------


class TestDeviceManagementIP:
    """management_ip on a device is materialised as IpamIPAddress and wired to primary_address."""

    def _make_client(self, ns_id: str = "ns-1") -> MagicMock:
        ns = _stub_node("namespace")
        ns.id = ns_id

        async def fake_filters(*, kind: str, **kwargs: Any) -> list[Any]:
            if kind == "IpamNamespace":
                return [ns]
            if kind in ("DcimDeviceType", "DcimPlatform", "LocationSite", "RoutingAutonomousSystem"):
                peer = _stub_node(kind)
                peer.id = f"{kind}-id"
                return [peer]
            return []

        client = MagicMock()
        client.create = AsyncMock(side_effect=lambda **kw: _stub_node(kw.get("kind", "node")))
        client.filters = AsyncMock(side_effect=fake_filters)
        return client

    def _minimal_device(self, *, management_ip: str | None = None) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": "spine-01",
            "role": "spine",
            "use_case": "dcfabric",
            "device_type": "7220 IXR-D3",
            "location": "local-lab",
        }
        if management_ip is not None:
            d["management_ip"] = management_ip
        return d

    async def test_management_ip_creates_ipam_address(self, tmp_path: Path):
        dataset = {"devices": [self._minimal_device(management_ip="10.0.0.1/24")]}
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = self._make_client()
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        ip_call = next(
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "IpamIPAddress"
        )
        assert ip_call.kwargs["data"]["address"] == "10.0.0.1/24"
        assert ip_call.kwargs["data"]["ip_namespace"] == "ns-1"

    async def test_management_ip_wires_primary_address(self, tmp_path: Path):
        """primary_address on the DcimDevice is set to the IpamIPAddress id."""
        ip_node = _stub_node("ip")
        ip_node.id = "mgmt-ip-1"

        async def fake_create(**kw: Any) -> MagicMock:
            if kw.get("kind") == "IpamIPAddress":
                return ip_node
            return _stub_node(kw.get("kind", "node"))

        dataset = {"devices": [self._minimal_device(management_ip="10.0.0.1/24")]}
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = self._make_client()
        client.create = AsyncMock(side_effect=fake_create)
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        device_call = next(
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "DcimDevice"
        )
        assert device_call.kwargs["data"].get("primary_address") == "mgmt-ip-1"

    async def test_device_without_management_ip_skips_materialisation(self, tmp_path: Path):
        """Device without management_ip creates no IpamIPAddress."""
        dataset = {"devices": [self._minimal_device()]}
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = self._make_client()
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        ip_creates = [
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "IpamIPAddress"
        ]
        assert ip_creates == []


# ---------------------------------------------------------------------------
# Milestone D — BGP peer-group shadow copies + BGP sessions
# ---------------------------------------------------------------------------


def _make_bgp_client(
    ns_id: str = "ns-1",
    vrf_id: str = "vrf-1",
    spine01_id: str = "dev-s1",
    spine02_id: str = "dev-s2",
) -> MagicMock:
    """Client stub that resolves namespace, VRF, spine devices, and AS nodes."""
    ns = _stub_node("namespace")
    ns.id = ns_id
    vrf = _stub_node("vrf")
    vrf.id = vrf_id
    spine01 = _stub_node("spine-01")
    spine01.id = spine01_id
    spine02 = _stub_node("spine-02")
    spine02.id = spine02_id

    def _as_node(asn: int) -> MagicMock:
        node = _stub_node(f"as-{asn}")
        node.id = f"as-id-{asn}"
        return node

    async def fake_filters(*, kind: str, **kwargs: Any) -> list[Any]:
        if kind == "IpamNamespace":
            return [ns]
        if kind == "IpamVRF":
            return [vrf]
        if kind == "DcimDevice":
            name = kwargs.get("name__value")
            if name == "spine-01":
                return [spine01]
            if name == "spine-02":
                return [spine02]
        if kind == "RoutingAutonomousSystem":
            asn = kwargs.get("asn__value")
            if asn is not None:
                return [_as_node(asn)]
        # upsert lookups (BGPPeerGroup, BGPSession, IpamIPAddress, ...) — nothing exists yet
        return []

    client = MagicMock()
    client.create = AsyncMock(side_effect=lambda **kw: _stub_node(kw.get("kind", "node")))
    client.filters = AsyncMock(side_effect=fake_filters)
    return client


def _minimal_bgp_dataset(*, num_spines: int = 2) -> dict[str, Any]:
    """Two sessions (one per spine) using the same peer group."""
    sessions = [
        {
            "description": f"spine-0{i} <-> leaf-01 eBGP",
            "session_type": "EXTERNAL",
            "role": "backbone",
            "local_device": f"spine-0{i}",
            "remote_device": "leaf-01",
            "local_as": 65000 + i - 1,
            "remote_as": 65011,
            "peer_group": "underlay-ipv4",
        }
        for i in range(1, num_spines + 1)
    ]
    return {
        "bgp_peer_groups": [
            {
                "name": "underlay-ipv4",
                "description": "eBGP underlay peer group",
                "address_family": "ipv4",
                "send_community": True,
            }
        ],
        "bgp_sessions": sessions,
    }


class TestBGPPeerGroupShadowCopies:
    """Milestone D — one BGPPeerGroup shadow per (peer_group, device) pair."""

    async def test_creates_one_shadow_per_device(self, tmp_path: Path):
        """Two sessions with different local_devices → two BGPPeerGroup creates."""
        dataset = _minimal_bgp_dataset(num_spines=2)
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = _make_bgp_client()
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        pg_creates = [
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "RoutingBGPPeerGroup"
        ]
        assert len(pg_creates) == 2

    async def test_shadow_name_is_scoped_to_device(self, tmp_path: Path):
        """Shadow name = '{pg_name}@{device_name}'."""
        dataset = _minimal_bgp_dataset(num_spines=1)
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = _make_bgp_client()
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        pg_call = next(
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "RoutingBGPPeerGroup"
        )
        assert pg_call.kwargs["data"]["name"] == "underlay-ipv4@spine-01"

    async def test_shadow_description_is_scoped(self, tmp_path: Path):
        """Shadow description includes device name for RoutingProtocol uniqueness."""
        dataset = _minimal_bgp_dataset(num_spines=1)
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = _make_bgp_client()
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        pg_call = next(
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "RoutingBGPPeerGroup"
        )
        desc = pg_call.kwargs["data"]["description"]
        assert "spine-01" in desc

    async def test_shadow_wires_device_and_vrf(self, tmp_path: Path):
        """Shadow has device=dev_id and vrf=vrf_id in payload."""
        dataset = _minimal_bgp_dataset(num_spines=1)
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = _make_bgp_client(vrf_id="vrf-42", spine01_id="dev-99")
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        pg_call = next(
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "RoutingBGPPeerGroup"
        )
        assert pg_call.kwargs["data"]["device"] == "dev-99"
        assert pg_call.kwargs["data"]["vrf"] == "vrf-42"

    async def test_shadow_defaults_status_active(self, tmp_path: Path):
        """YAML entry without status → shadow payload has status='active'."""
        dataset = _minimal_bgp_dataset(num_spines=1)
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = _make_bgp_client()
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        pg_call = next(
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "RoutingBGPPeerGroup"
        )
        assert pg_call.kwargs["data"]["status"] == "active"

    async def test_peer_group_idempotent(self, tmp_path: Path):
        """Second seed run finds existing shadow by scoped name — no new BGPPeerGroup create."""
        dataset = _minimal_bgp_dataset(num_spines=1)
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = _make_bgp_client()
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)
        pg_creates_first_run = [
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "RoutingBGPPeerGroup"
        ]

        existing_shadow = _stub_node("existing-shadow")
        existing_shadow.id = "shadow-id"

        original_filters = client.filters.side_effect

        async def filters_with_existing(*, kind: str, **kwargs: Any) -> list[Any]:
            if kind == "RoutingBGPPeerGroup" and kwargs.get("name__value") == "underlay-ipv4@spine-01":
                return [existing_shadow]
            return await original_filters(kind=kind, **kwargs)

        client.filters = AsyncMock(side_effect=filters_with_existing)
        await ingester.seed(use_case="dcfabric", data_path=path)

        pg_creates_total = [
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "RoutingBGPPeerGroup"
        ]
        assert len(pg_creates_total) == len(pg_creates_first_run)

    async def test_missing_vrf_raises_validation_error(self, tmp_path: Path):
        """No VRF in Infrahub → IntentValidationError before any BGP creates."""
        dataset = _minimal_bgp_dataset(num_spines=1)
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        async def no_vrf(*, kind: str, **kwargs: Any) -> list[Any]:
            if kind == "IpamNamespace":
                ns = _stub_node("ns")
                ns.id = "ns-1"
                return [ns]
            return []

        client = MagicMock()
        client.create = AsyncMock(side_effect=lambda **kw: _stub_node())
        client.filters = AsyncMock(side_effect=no_vrf)
        ingester = SeedIngester(client=client)

        with pytest.raises(IntentValidationError, match="VRF"):
            await ingester.seed(use_case="dcfabric", data_path=path)


class TestBGPSessionSeeding:
    """Milestone D — BGPSession upsert with full relationship resolution."""

    async def test_session_device_resolved_from_local_device(self, tmp_path: Path):
        """device in payload = resolved local_device DcimDevice id."""
        dataset = _minimal_bgp_dataset(num_spines=1)
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = _make_bgp_client(spine01_id="dev-spine01")
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        session_call = next(
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "RoutingBGPSession"
        )
        assert session_call.kwargs["data"]["device"] == "dev-spine01"

    async def test_session_local_as_resolved_by_asn(self, tmp_path: Path):
        """local_as integer → RoutingAutonomousSystem id."""
        as_node = _stub_node("as")
        as_node.id = "as-65000"

        original_client = _make_bgp_client()
        original_side = original_client.filters.side_effect

        async def with_as(*, kind: str, **kwargs: Any) -> list[Any]:
            if kind == "RoutingAutonomousSystem" and kwargs.get("asn__value") == 65000:
                return [as_node]
            return await original_side(kind=kind, **kwargs)

        original_client.filters = AsyncMock(side_effect=with_as)
        dataset = _minimal_bgp_dataset(num_spines=1)
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))
        ingester = SeedIngester(client=original_client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        session_call = next(
            c for c in original_client.create.await_args_list
            if c.kwargs.get("kind") == "RoutingBGPSession"
        )
        assert session_call.kwargs["data"]["local_as"] == "as-65000"

    async def test_session_peer_group_resolves_to_device_shadow(self, tmp_path: Path):
        """peer_group in session payload = shadow id for local_device, not the prototype."""
        shadow_node = _stub_node("shadow")
        shadow_node.id = "shadow-spine01"

        original_client = _make_bgp_client()
        original_side = original_client.filters.side_effect

        async def with_shadow(*, kind: str, **kwargs: Any) -> list[Any]:
            if kind == "RoutingBGPPeerGroup" and kwargs.get("name__value") == "underlay-ipv4@spine-01":
                return [shadow_node]
            return await original_side(kind=kind, **kwargs)

        original_client.filters = AsyncMock(side_effect=with_shadow)
        dataset = _minimal_bgp_dataset(num_spines=1)
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))
        ingester = SeedIngester(client=original_client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        session_call = next(
            c for c in original_client.create.await_args_list
            if c.kwargs.get("kind") == "RoutingBGPSession"
        )
        assert session_call.kwargs["data"].get("peer_group") == "shadow-spine01"

    async def test_session_uses_description_for_upsert_lookup(self, tmp_path: Path):
        """Upsert lookup uses description__value, not local_device/remote_device."""
        dataset = _minimal_bgp_dataset(num_spines=1)
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = _make_bgp_client()
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        session_lookups = [
            c for c in client.filters.await_args_list
            if c.kwargs.get("kind") == "RoutingBGPSession"
        ]
        assert session_lookups, "Expected at least one RoutingBGPSession filter call"
        assert all("description__value" in c.kwargs for c in session_lookups)

    async def test_session_idempotent(self, tmp_path: Path):
        """Second seed run finds existing session by description — no new create."""
        dataset = _minimal_bgp_dataset(num_spines=1)
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = _make_bgp_client()
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)
        creates_after_first = client.create.await_count

        existing_session = _stub_node("existing-session")

        original_side = client.filters.side_effect

        async def filters_with_existing_session(*, kind: str, **kwargs: Any) -> list[Any]:
            if kind == "RoutingBGPSession":
                return [existing_session]
            return await original_side(kind=kind, **kwargs)

        client.filters = AsyncMock(side_effect=filters_with_existing_session)
        await ingester.seed(use_case="dcfabric", data_path=path)

        session_creates = [
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "RoutingBGPSession"
        ]
        first_run_session_count = sum(
            1 for c in client.create.await_args_list[:creates_after_first]
            if c.kwargs.get("kind") == "RoutingBGPSession"
        )
        total_session_creates = len(session_creates)
        assert total_session_creates == first_run_session_count

    async def test_session_vrf_wired(self, tmp_path: Path):
        """vrf in session payload = resolved default VRF id."""
        dataset = _minimal_bgp_dataset(num_spines=1)
        path = tmp_path / "t.yml"
        path.write_text(yaml.safe_dump(dataset))

        client = _make_bgp_client(vrf_id="vrf-xyz")
        ingester = SeedIngester(client=client)
        await ingester.seed(use_case="dcfabric", data_path=path)

        session_call = next(
            c for c in client.create.await_args_list
            if c.kwargs.get("kind") == "RoutingBGPSession"
        )
        assert session_call.kwargs["data"]["vrf"] == "vrf-xyz"
