"""Unit tests for the pure structural diff function (T015)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

pytestmark = pytest.mark.unit


def _build_desired_state(*, mtu: int | None = 9000, description: str | None = "spine link"):
    from snapl_intent.models import BGPSession, DesiredState, Device, Interface

    dev_id = UUID("00000000-0000-0000-0000-000000000001")
    device = Device(
        id=dev_id,
        name="spine-01",
        management_address="10.0.0.1",
        role="spine",
        use_case="dcfabric",
        platform="nokia-srlinux",
        description=description,
    )
    iface = Interface(
        id=uuid4(),
        device_id=dev_id,
        name="ethernet-1/1",
        description=description,
        ip_address="10.1.1.0",
        prefix_length=31,
        enabled=True,
        mtu=mtu,
    )
    bgp = BGPSession(
        id=uuid4(),
        device_id=dev_id,
        local_asn=65000,
        peer_address="10.1.1.1",
        peer_asn=65001,
        peer_group="underlay-ipv4",
        enabled=True,
    )
    return DesiredState(device=device, interfaces=[iface], bgp_sessions=[bgp])


def _matching_actual_data(desired):
    """Build an actual_data dict that exactly matches the desired state."""
    iface = desired.interfaces[0]
    bgp = desired.bgp_sessions[0]
    return {
        f"/interface[name={iface.name}]": {
            "description": iface.description,
            "ip_address": iface.ip_address,
            "prefix_length": iface.prefix_length,
            "enabled": iface.enabled,
            "mtu": iface.mtu,
        },
        f"/network-instance[name=default]/protocols/bgp/neighbor[peer-address={bgp.peer_address}]": {
            "peer_address": bgp.peer_address,
            "peer_asn": bgp.peer_asn,
            "peer_group": bgp.peer_group,
            "enabled": bgp.enabled,
        },
    }


class TestEntityFieldMap:
    def test_map_covers_the_managed_entity_kinds(self):
        from snapl_observability.structural.diff import ENTITY_FIELD_MAP

        assert "interface" in ENTITY_FIELD_MAP
        assert "bgp_session" in ENTITY_FIELD_MAP
        # The executor manages no device-level (/system) fields, so the diff
        # must not compare any — a compared-but-never-rendered field is
        # permanent phantom drift (#59).
        assert "device" not in ENTITY_FIELD_MAP

    def test_interface_map_fields(self):
        from snapl_observability.structural.diff import ENTITY_FIELD_MAP

        entry = ENTITY_FIELD_MAP["interface"]
        assert entry["key_field"] == "name"
        assert "{name}" in entry["path_template"]
        assert "mtu" in entry["fields"]


class TestDiffMatchingState:
    def test_matching_returns_empty(self):
        from snapl_observability.structural.diff import diff_desired_vs_actual

        desired = _build_desired_state()
        actual = _matching_actual_data(desired)
        assert diff_desired_vs_actual(desired, actual) == []


class TestDiffInterfaceMismatches:
    def test_mtu_mismatch_produces_one_item(self):
        from snapl_observability.structural.diff import diff_desired_vs_actual

        desired = _build_desired_state(mtu=9000)
        actual = _matching_actual_data(desired)
        actual["/interface[name=ethernet-1/1]"]["mtu"] = 1500
        items = diff_desired_vs_actual(desired, actual)
        assert len(items) == 1
        assert items[0].path == "/interface[name=ethernet-1/1]/mtu"
        assert items[0].entity_kind == "interface"
        assert items[0].desired == 9000
        assert items[0].actual == 1500

    def test_mtu_unset_in_intent_does_not_drift_against_operational_default(self):
        """An ethernet interface with no mtu in intent renders without one, but
        the device still reports an operational mtu (e.g. 9232). An unset mtu
        means 'not managed' — it must not phantom-drift on every scan (#84)."""
        from snapl_observability.structural.diff import diff_desired_vs_actual

        desired = _build_desired_state(mtu=None)
        actual = _matching_actual_data(desired)
        actual["/interface[name=ethernet-1/1]"]["mtu"] = 9232
        items = diff_desired_vs_actual(desired, actual)
        assert [i for i in items if i.path.endswith("/mtu")] == []

    def test_description_mismatch(self):
        from snapl_observability.structural.diff import diff_desired_vs_actual

        desired = _build_desired_state()
        actual = _matching_actual_data(desired)
        actual["/interface[name=ethernet-1/1]"]["description"] = "wrong"
        items = diff_desired_vs_actual(desired, actual)
        assert len(items) == 1
        assert items[0].path.endswith("/description")

    def test_missing_interface_in_actual_produces_items_for_each_field(self):
        from snapl_observability.structural.diff import diff_desired_vs_actual

        desired = _build_desired_state()
        actual = _matching_actual_data(desired)
        del actual["/interface[name=ethernet-1/1]"]
        items = diff_desired_vs_actual(desired, actual)
        # one item per non-None desired interface field
        iface_items = [i for i in items if i.entity_kind == "interface"]
        assert len(iface_items) >= 1
        for item in iface_items:
            assert item.actual is None


class TestDiffBGPMismatches:
    def test_peer_asn_mismatch(self):
        from snapl_observability.structural.diff import diff_desired_vs_actual

        desired = _build_desired_state()
        actual = _matching_actual_data(desired)
        bgp_path = "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=10.1.1.1]"
        actual[bgp_path]["peer_asn"] = 99999
        items = diff_desired_vs_actual(desired, actual)
        assert len(items) == 1
        assert items[0].entity_kind == "bgp_session"
        assert "peer-address=10.1.1.1" in items[0].path
        assert items[0].path.endswith("/peer_asn")
        assert items[0].desired == 65001
        assert items[0].actual == 99999


class TestDeviceDescriptionNotCompared:
    def test_device_description_produces_no_drift(self):
        """Nothing renders a device description, so the diff must not compare
        one — seeded devices all carry descriptions and would otherwise drift
        forever (#59)."""
        from snapl_observability.structural.diff import diff_desired_vs_actual

        desired = _build_desired_state(description="Spine 01")
        actual = _matching_actual_data(desired)
        assert "/system" not in actual
        items = diff_desired_vs_actual(desired, actual)
        assert [i for i in items if i.entity_kind == "device"] == []


class TestDiffMultipleEntityKinds:
    def test_multiple_kinds_produces_multiple_items(self):
        from snapl_observability.structural.diff import diff_desired_vs_actual

        desired = _build_desired_state()
        actual = _matching_actual_data(desired)
        actual["/interface[name=ethernet-1/1]"]["mtu"] = 1500
        bgp_path = "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=10.1.1.1]"
        actual[bgp_path]["enabled"] = False
        items = diff_desired_vs_actual(desired, actual)
        kinds = {i.entity_kind for i in items}
        assert kinds == {"interface", "bgp_session"}
        assert len(items) == 2
