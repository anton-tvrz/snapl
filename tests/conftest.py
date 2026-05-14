"""Shared test fixtures for snapl."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

# ---------------------------------------------------------------------------
# Legacy compatibility fixture (kept for packages that reference it by name).
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_device_config() -> dict:
    """Sample device configuration for legacy tests."""
    return {
        "hostname": "spine01",
        "platform": "nokia_srlinux",
        "management_ip": "172.20.20.11",
        "role": "spine",
    }


# ---------------------------------------------------------------------------
# Intent module fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_infrahub_client() -> MagicMock:
    """A mock InfrahubClient with AsyncMock methods used by the Intent store.

    The object is a MagicMock so tests can freely assert on arbitrary attributes;
    the frequently-used async methods are replaced with AsyncMock instances so
    `await client.<method>(...)` works out of the box.
    """
    client = MagicMock(name="InfrahubClient")

    # Core async methods used by the store. Tests can override return values.
    client.all = AsyncMock(name="client.all", return_value=[])
    client.filters = AsyncMock(name="client.filters", return_value=[])
    client.get = AsyncMock(name="client.get")
    client.create = AsyncMock(name="client.create")

    # Schema API
    client.schema = MagicMock(name="client.schema")
    client.schema.load = AsyncMock(name="client.schema.load", return_value={"errors": []})
    client.schema.all = AsyncMock(name="client.schema.all", return_value={})

    # Branch API
    client.branch = MagicMock(name="client.branch")
    client.branch.create = AsyncMock(name="client.branch.create")
    client.branch.all = AsyncMock(name="client.branch.all", return_value=[])

    return client


@pytest.fixture
def spine_leaf_topology() -> dict:
    """A minimal spine-leaf fabric fixture used by unit tests.

    Shape matches the dcfabric seed YAML: 2 spines, 4 leaves, fabric interfaces,
    loopbacks, and eBGP sessions between each spine/leaf pair. IDs are stable
    UUIDs generated once per test invocation so tests can reference them.
    """

    def _uuid(index: int) -> UUID:
        # Deterministic UUIDs so fixture output is reproducible within a test.
        return UUID(int=index)

    spine1 = _uuid(1)
    spine2 = _uuid(2)
    leaves = [_uuid(10 + i) for i in range(4)]

    devices = [
        {
            "id": str(spine1),
            "name": "spine-01",
            "management_address": "10.0.0.1",
            "role": "spine",
            "use_case": "dcfabric",
            "platform": "nokia-srlinux",
        },
        {
            "id": str(spine2),
            "name": "spine-02",
            "management_address": "10.0.0.2",
            "role": "spine",
            "use_case": "dcfabric",
            "platform": "nokia-srlinux",
        },
    ]
    for i, leaf_id in enumerate(leaves, start=1):
        devices.append(
            {
                "id": str(leaf_id),
                "name": f"leaf-{i:02d}",
                "management_address": f"10.0.1.{i}",
                "role": "leaf",
                "use_case": "dcfabric",
                "platform": "nokia-srlinux",
            }
        )

    interfaces = []
    bgp_sessions = []
    for i, leaf_id in enumerate(leaves, start=1):
        # Spine-side fabric interface toward this leaf
        interfaces.append(
            {
                "id": str(uuid4()),
                "device_id": str(spine1),
                "name": f"ethernet-1/{i}",
                "ip_address": f"10.1.{i}.0",
                "prefix_length": 31,
                "enabled": True,
                "peer_device": f"leaf-{i:02d}",
                "peer_interface": "ethernet-1/49",
            }
        )
        # Leaf-side fabric interface toward spine-01
        interfaces.append(
            {
                "id": str(uuid4()),
                "device_id": str(leaf_id),
                "name": "ethernet-1/49",
                "ip_address": f"10.1.{i}.1",
                "prefix_length": 31,
                "enabled": True,
                "peer_device": "spine-01",
                "peer_interface": f"ethernet-1/{i}",
            }
        )
        # eBGP session between spine-01 and this leaf
        bgp_sessions.append(
            {
                "id": str(uuid4()),
                "device_id": str(spine1),
                "local_asn": 65000,
                "peer_address": f"10.1.{i}.1",
                "peer_asn": 65000 + i,
                "peer_group": "underlay-ipv4",
                "address_family": "ipv4_unicast",
                "enabled": True,
            }
        )

    return {
        "devices": devices,
        "interfaces": interfaces,
        "bgp_sessions": bgp_sessions,
    }


@pytest.fixture
def intent_package_root() -> Path:
    """Absolute path to the packages/intent/snapl_intent directory."""
    return Path(__file__).resolve().parent.parent / "packages" / "intent" / "snapl_intent"


# ---------------------------------------------------------------------------
# Executor module fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_gnmi_client() -> MagicMock:
    """A mock gNMIclient with a synchronous set() method.

    The set() method returns a dict resembling a gNMI SET response.
    Tests override set.side_effect to simulate errors.
    """
    client = MagicMock(name="gNMIclient")
    client.set = MagicMock(name="gNMIclient.set", return_value={"response": [{"timestamp": 0}]})
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    return client


@pytest.fixture
def dcfabric_desired_state():
    """A minimal DesiredState for a dcfabric spine device with 2 interfaces and 1 BGP session."""
    from snapl_intent.models import BGPSession, DesiredState, Device, Interface

    device_id = UUID("00000000-0000-0000-0000-000000000001")
    device = Device(
        id=device_id,
        name="spine-01",
        management_address="10.0.0.1",
        role="spine",
        use_case="dcfabric",
        platform="nokia-srlinux",
    )
    interfaces = [
        Interface(
            id=UUID("00000000-0000-0000-0000-000000000011"),
            device_id=device_id,
            name="ethernet-1/1",
            ip_address="10.1.1.0",
            prefix_length=31,
            enabled=True,
            mtu=9232,
        ),
        Interface(
            id=UUID("00000000-0000-0000-0000-000000000012"),
            device_id=device_id,
            name="ethernet-1/2",
            ip_address="10.1.2.0",
            prefix_length=31,
            enabled=True,
            mtu=9232,
        ),
    ]
    bgp_sessions = [
        BGPSession(
            id=UUID("00000000-0000-0000-0000-000000000021"),
            device_id=device_id,
            local_asn=65000,
            peer_address="10.1.1.1",
            peer_asn=65001,
            peer_group="underlay-ipv4",
            address_family="ipv4_unicast",
            enabled=True,
        )
    ]
    return DesiredState(device=device, interfaces=interfaces, bgp_sessions=bgp_sessions)


@pytest.fixture
def make_desired():
    """Factory fixture — returns a callable that builds a minimal DesiredState.

    Usage: make_desired("spine-01") or make_desired("spine-01", device_id=uuid)
    Useful for batch tests that need multiple distinct devices.
    """
    from snapl_intent.models import BGPSession, DesiredState, Device, Interface

    def _factory(device_name: str, device_id: UUID | None = None) -> DesiredState:
        dev_id = device_id or uuid4()
        device = Device(
            id=dev_id,
            name=device_name,
            management_address="127.0.0.1",
            role="spine",
            use_case="dcfabric",
            platform="nokia-srlinux",
        )
        ifaces = [
            Interface(
                id=uuid4(),
                device_id=dev_id,
                name="ethernet-1/1",
                ip_address="10.0.0.0",
                prefix_length=31,
                enabled=True,
                mtu=9232,
            )
        ]
        sessions = [
            BGPSession(
                id=uuid4(),
                device_id=dev_id,
                local_asn=65000,
                peer_address="10.0.0.1",
                peer_asn=65001,
                enabled=True,
                address_family="ipv4_unicast",
            )
        ]
        return DesiredState(device=device, interfaces=ifaces, bgp_sessions=sessions)

    return _factory


# ---------------------------------------------------------------------------
# Collector module fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def make_device():
    """Factory fixture — returns a callable that builds a snapl_intent Device.

    Usage: make_device("spine-01") or make_device("spine-01", device_id=uuid, address="10.0.0.1")
    """
    from snapl_intent.models import Device

    def _factory(
        name: str,
        device_id: UUID | None = None,
        address: str = "127.0.0.1",
    ) -> Device:
        return Device(
            id=device_id or uuid4(),
            name=name,
            management_address=address,
            role="spine",
            use_case="dcfabric",
            platform="nokia-srlinux",
        )

    return _factory
