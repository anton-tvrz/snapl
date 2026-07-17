"""Unit tests for Pydantic models in snapl_intent.models.

Covers: Device, Interface, BGPSession, DesiredState, Schema,
ProvisionResult, SeedResult, DeleteResult.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from snapl_intent.models import (
    BGPSession,
    DeleteResult,
    DesiredState,
    Device,
    Interface,
    ProvisionResult,
    Schema,
    SeedResult,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------


class TestDevice:
    def test_minimal_valid_device(self):
        device = Device(
            id=uuid4(),
            name="spine-01",
            management_address="10.0.0.1",
            role="spine",
            use_case="dcfabric",
        )
        assert device.name == "spine-01"
        assert device.role == "spine"
        assert device.platform is None
        assert device.description is None

    def test_device_with_optional_fields(self):
        device = Device(
            id=uuid4(),
            name="spine-01",
            management_address="10.0.0.1",
            role="spine",
            use_case="dcfabric",
            platform="nokia-srlinux",
            description="primary spine",
        )
        assert device.platform == "nokia-srlinux"
        assert device.description == "primary spine"

    def test_device_requires_name(self):
        with pytest.raises(ValidationError):
            Device(
                id=uuid4(),
                management_address="10.0.0.1",
                role="spine",
                use_case="dcfabric",
            )

    def test_device_requires_management_address(self):
        with pytest.raises(ValidationError):
            Device(
                id=uuid4(),
                name="spine-01",
                role="spine",
                use_case="dcfabric",
            )

    def test_device_requires_use_case(self):
        with pytest.raises(ValidationError):
            Device(
                id=uuid4(),
                name="spine-01",
                management_address="10.0.0.1",
                role="spine",
            )


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class TestInterface:
    def test_minimal_valid_interface(self):
        iface = Interface(
            id=uuid4(),
            device_id=uuid4(),
            name="ethernet-1/1",
        )
        assert iface.enabled is True
        # No mtu default: intent must be able to express "no mtu" — SR Linux
        # rejects mtu on loopbacks outright (#78).
        assert iface.mtu is None
        assert iface.ip_address is None

    def test_interface_with_ip(self):
        iface = Interface(
            id=uuid4(),
            device_id=uuid4(),
            name="ethernet-1/1",
            ip_address="10.1.0.0",
            prefix_length=31,
            speed="100G",
            mtu=9214,
        )
        assert iface.ip_address == "10.1.0.0"
        assert iface.prefix_length == 31
        assert iface.mtu == 9214

    def test_interface_peer_link(self):
        iface = Interface(
            id=uuid4(),
            device_id=uuid4(),
            name="ethernet-1/1",
            peer_device="leaf-01",
            peer_interface="ethernet-1/49",
        )
        assert iface.peer_device == "leaf-01"

    def test_interface_requires_device_id(self):
        with pytest.raises(ValidationError):
            Interface(id=uuid4(), name="ethernet-1/1")


# ---------------------------------------------------------------------------
# BGPSession
# ---------------------------------------------------------------------------


class TestBGPSession:
    def test_minimal_valid_session(self):
        session = BGPSession(
            id=uuid4(),
            device_id=uuid4(),
            local_asn=65000,
            peer_address="10.1.0.1",
            peer_asn=65001,
        )
        assert session.address_family == "ipv4_unicast"
        assert session.enabled is True

    def test_session_with_policies(self):
        session = BGPSession(
            id=uuid4(),
            device_id=uuid4(),
            local_asn=65000,
            peer_address="10.1.0.1",
            peer_asn=65001,
            peer_group="underlay-ipv4",
            export_policy="deny-private",
            import_policy="accept-leaf-loopbacks",
        )
        assert session.peer_group == "underlay-ipv4"
        assert session.export_policy == "deny-private"

    def test_session_requires_asn(self):
        with pytest.raises(ValidationError):
            BGPSession(
                id=uuid4(),
                device_id=uuid4(),
                peer_address="10.1.0.1",
                peer_asn=65001,
            )


# ---------------------------------------------------------------------------
# DesiredState
# ---------------------------------------------------------------------------


class TestDesiredState:
    def _make_device(self) -> Device:
        return Device(
            id=uuid4(),
            name="spine-01",
            management_address="10.0.0.1",
            role="spine",
            use_case="dcfabric",
        )

    def test_empty_desired_state(self):
        state = DesiredState(device=self._make_device(), interfaces=[], bgp_sessions=[])
        assert state.interfaces == []
        assert state.bgp_sessions == []

    def test_desired_state_with_children(self):
        device = self._make_device()
        iface = Interface(id=uuid4(), device_id=device.id, name="ethernet-1/1")
        session = BGPSession(
            id=uuid4(),
            device_id=device.id,
            local_asn=65000,
            peer_address="10.1.0.1",
            peer_asn=65001,
        )
        state = DesiredState(device=device, interfaces=[iface], bgp_sessions=[session])
        assert len(state.interfaces) == 1
        assert state.interfaces[0].device_id == device.id
        assert len(state.bgp_sessions) == 1


# ---------------------------------------------------------------------------
# Schema / result models
# ---------------------------------------------------------------------------


class TestSchema:
    def test_schema_with_provisioned_at(self):
        schema = Schema(
            use_case="dcfabric",
            version="1.0",
            entities=["DcimDevice", "InterfacePhysical"],
            source_files=["schemas/base/dcim.yml"],
            provisioned_at=datetime.now(tz=UTC),
        )
        assert schema.use_case == "dcfabric"
        assert "DcimDevice" in schema.entities

    def test_schema_without_provisioned_at(self):
        schema = Schema(
            use_case="dcfabric",
            version="1.0",
            entities=[],
            source_files=[],
        )
        assert schema.provisioned_at is None


class TestResultModels:
    def test_provision_result(self):
        result = ProvisionResult(use_case="dcfabric", schemas_loaded=7, changed=True)
        assert result.schemas_loaded == 7
        assert result.changed is True

    def test_seed_result(self):
        result = SeedResult(
            use_case="dcfabric",
            devices_created=6,
            devices_updated=0,
            total_records=42,
            branch="main",
        )
        assert result.devices_created == 6
        assert result.branch == "main"

    def test_delete_result(self):
        device_id = uuid4()
        result = DeleteResult(device_id=device_id, device_name="spine-01", records_removed=5)
        assert result.device_id == device_id
        assert result.records_removed == 5
