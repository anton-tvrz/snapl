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


# ---------------------------------------------------------------------------
# Undesired config — the reverse direction of the diff (#54, spec 007)
#
# The diff only ever walked *desired* entities, so config the device carries
# but intent never asked for produced zero drift. The naive fix is dangerous:
# a real SR Linux spine reports 31 interfaces intent does not name — 30 bare
# chassis ports plus mgmt0, whose address is the one snapl dials over. These
# tests pin the ownership rule that makes the difference tractable: an entity
# is undesired only when it carries a value-bearing field, and the protected
# set is never reported at all.
# ---------------------------------------------------------------------------


def _bare_interface() -> dict:
    """What SR Linux reports for a chassis port nobody configured."""
    return {"description": None, "ip_address": None, "prefix_length": None, "enabled": False, "mtu": None}


class TestUndesiredEntities:
    def test_unconfigured_chassis_ports_are_not_drift(self):
        """SC-001: 30 bare ports per spine must not bury the signal."""
        from snapl_observability.structural.diff import diff_desired_vs_actual

        desired = _build_desired_state()
        actual = _matching_actual_data(desired)
        for n in range(5, 35):
            actual[f"/interface[name=ethernet-1/{n}]"] = _bare_interface()

        assert diff_desired_vs_actual(desired, actual) == []

    def test_an_interface_configured_outside_intent_is_reported(self):
        """SC-002: a hand-added IP is exactly the sprawl this is for."""
        from snapl_observability.structural.diff import diff_desired_vs_actual

        desired = _build_desired_state()
        actual = _matching_actual_data(desired)
        actual["/interface[name=ethernet-1/7]"] = {
            **_bare_interface(),
            "ip_address": "192.0.2.1",
            "prefix_length": 30,
            "enabled": True,
        }

        items = diff_desired_vs_actual(desired, actual)

        paths = {item.path for item in items}
        assert any("ethernet-1/7" in path for path in paths), f"undesired interface not reported: {paths}"
        reported = [item for item in items if "ethernet-1/7" in item.path]
        assert all(item.desired is None for item in reported), "an undesired entity has no desired value"
        assert {item.actual for item in reported} == {"192.0.2.1", 30}

    def test_the_management_interface_is_never_reported(self):
        """SC-003: mgmt0 is shape-identical to real config, and deleting it
        would strand the device — it can only be excluded by name."""
        from snapl_observability.structural.diff import diff_desired_vs_actual

        desired = _build_desired_state()
        actual = _matching_actual_data(desired)
        actual["/interface[name=mgmt0]"] = {
            "description": None,
            "ip_address": "172.20.21.11",
            "prefix_length": 24,
            "enabled": True,
            "mtu": 1514,
        }
        actual["/interface[name=system0]"] = {
            **_bare_interface(),
            "ip_address": "10.255.0.1",
            "prefix_length": 32,
        }

        items = diff_desired_vs_actual(desired, actual)

        assert not [i for i in items if "mgmt0" in i.path or "system0" in i.path], (
            f"a protected interface was reported: {[i.path for i in items]}"
        )

    def test_enabled_alone_does_not_make_an_interface_configured(self):
        """FR-003: a port that is merely up is not evidence of intent."""
        from snapl_observability.structural.diff import diff_desired_vs_actual

        desired = _build_desired_state()
        actual = _matching_actual_data(desired)
        actual["/interface[name=ethernet-1/9]"] = {**_bare_interface(), "enabled": True}

        assert diff_desired_vs_actual(desired, actual) == []

    def test_a_bgp_neighbor_outside_intent_is_reported(self):
        """US1 scenario 3 — the same rule applies to the other entity kind."""
        from snapl_observability.structural.diff import diff_desired_vs_actual

        desired = _build_desired_state()
        actual = _matching_actual_data(desired)
        actual["/network-instance[name=default]/protocols/bgp/neighbor[peer-address=203.0.113.9]"] = {
            "peer_address": "203.0.113.9",
            "peer_asn": 65099,
            "peer_group": "underlay-ipv4",
            "enabled": True,
        }

        items = diff_desired_vs_actual(desired, actual)

        assert any("203.0.113.9" in item.path for item in items), (
            f"undesired bgp neighbor not reported: {[i.path for i in items]}"
        )

    def test_undesired_items_are_marked_as_such(self):
        """FR-007: direction must be readable without inferring it from
        `desired is None`, which a missing-value case can also produce."""
        from snapl_observability.structural.diff import diff_desired_vs_actual

        desired = _build_desired_state()
        actual = _matching_actual_data(desired)
        actual["/interface[name=ethernet-1/7]"] = {**_bare_interface(), "description": "hand-added"}

        items = diff_desired_vs_actual(desired, actual)

        undesired = [item for item in items if "ethernet-1/7" in item.path]
        assert undesired, "the undesired interface produced no drift items"
        assert all(item.undesired for item in undesired)

    def test_ordinary_drift_is_not_marked_undesired(self):
        """The existing direction must keep its meaning."""
        from snapl_observability.structural.diff import diff_desired_vs_actual

        desired = _build_desired_state()
        actual = _matching_actual_data(desired)
        actual[f"/interface[name={desired.interfaces[0].name}]"]["description"] = "changed by hand"

        items = diff_desired_vs_actual(desired, actual)

        assert items, "an ordinary mismatch produced no drift items"
        assert not any(item.undesired for item in items)

    def test_an_unparseable_key_is_ignored(self):
        """An unreadable key is not evidence of sprawl."""
        from snapl_observability.structural.diff import diff_desired_vs_actual

        desired = _build_desired_state()
        actual = _matching_actual_data(desired)
        actual["/interface[malformed"] = {**_bare_interface(), "description": "x"}
        actual["/something/entirely/unrelated"] = {"description": "x"}

        assert diff_desired_vs_actual(desired, actual) == []

    def test_report_is_deterministic(self):
        """FR-009: two scans of an unchanged device must be identical."""
        from snapl_observability.structural.diff import diff_desired_vs_actual

        desired = _build_desired_state()
        actual = _matching_actual_data(desired)
        for n in (12, 3, 30, 7):
            actual[f"/interface[name=ethernet-1/{n}]"] = {**_bare_interface(), "description": f"x{n}"}

        first = diff_desired_vs_actual(desired, actual)
        second = diff_desired_vs_actual(desired, actual)

        assert [i.path for i in first] == [i.path for i in second]
        assert len(first) == 4
