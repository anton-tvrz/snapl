"""Adapter tests for the SR Linux collect→diff normalization (#32).

Driven by a fixture captured from a live dcfabric SR Linux node, so the adapter
is validated against the real device shape rather than a hand-idealized one.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from snapl_intent.models import BGPSession, DesiredState, Device, Interface
from snapl_observability.structural.diff import diff_desired_vs_actual
from snapl_orchestrator.adapters.srlinux import DRIFT_PATHS, normalize_srlinux_state

pytestmark = pytest.mark.unit

_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "srlinux_spine01_collected.json"


@pytest.fixture
def collected() -> dict:
    return json.loads(_FIXTURE.read_text())


def _desired_matching_fixture() -> DesiredState:
    """A DesiredState matching the config applied to the fixture node."""
    dev_id = uuid4()
    device = Device(
        id=dev_id,
        name="spine-01",
        management_address="10.0.0.1",
        role="spine",
        use_case="dcfabric",
    )
    interfaces = [
        Interface(
            id=uuid4(),
            device_id=dev_id,
            name="ethernet-1/1",
            ip_address="10.10.1.0",
            prefix_length=31,
            enabled=True,
            mtu=9232,
        ),
        Interface(
            id=uuid4(),
            device_id=dev_id,
            name="ethernet-1/2",
            ip_address="10.10.1.2",
            prefix_length=31,
            enabled=True,
            mtu=9232,
        ),
        # Loopback: no mtu in intent, and the device reports none either (#78).
        Interface(
            id=uuid4(),
            device_id=dev_id,
            name="lo0",
            ip_address="10.0.0.1",
            prefix_length=32,
            enabled=True,
            mtu=None,
        ),
    ]
    sessions = [
        BGPSession(
            id=uuid4(),
            device_id=dev_id,
            local_asn=65001,
            peer_address="10.10.1.1",
            peer_asn=65011,
            peer_group="underlay-ipv4",
            enabled=True,
        ),
    ]
    return DesiredState(device=device, interfaces=interfaces, bgp_sessions=sessions)


def test_drift_paths_match_the_diff_contract_entities() -> None:
    # The collector must fetch the container paths the adapter expands.
    assert "/interface" in DRIFT_PATHS
    assert "/network-instance[name=default]/protocols/bgp" in DRIFT_PATHS
    # /system is no longer collected: the diff compares no device-level fields (#59).
    assert "/system" not in DRIFT_PATHS


def test_normalizes_interface_to_entity_keyed_flat_fields(collected: dict) -> None:
    out = normalize_srlinux_state(collected)

    eth1 = out["/interface[name=ethernet-1/1]"]
    assert eth1["ip_address"] == "10.10.1.0"
    assert eth1["prefix_length"] == 31
    assert eth1["enabled"] is True
    assert eth1["mtu"] == 9232


def test_normalizes_mtu_less_loopback_to_none(collected: dict) -> None:
    # SR Linux reports no mtu on lo0; the adapter must yield None so an
    # mtu-less loopback intent compares clean (#78).
    out = normalize_srlinux_state(collected)

    lo0 = out["/interface[name=lo0]"]
    assert lo0["mtu"] is None
    assert lo0["ip_address"] == "10.0.0.1"
    assert lo0["prefix_length"] == 32


def test_normalizes_bgp_neighbor_to_entity_keyed_flat_fields(collected: dict) -> None:
    out = normalize_srlinux_state(collected)

    key = "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=10.10.1.1]"
    neighbor = out[key]
    assert neighbor["peer_address"] == "10.10.1.1"
    assert neighbor["peer_asn"] == 65011
    assert neighbor["peer_group"] == "underlay-ipv4"
    assert neighbor["enabled"] is True


def test_normalized_output_yields_clean_diff_for_converged_device(collected: dict) -> None:
    # The whole point of #32: real collected state, run through the adapter,
    # must diff CLEAN against the intent that produced it.
    desired = _desired_matching_fixture()
    normalized = normalize_srlinux_state(collected)

    items = diff_desired_vs_actual(desired, normalized)

    assert items == [], f"unexpected drift on converged device: {items}"


def test_a_real_change_is_flagged_as_drift(collected: dict) -> None:
    desired = _desired_matching_fixture()
    # Intent wants the peer AS to be 65099; device has 65011 → must drift.
    desired.bgp_sessions[0].peer_asn = 65099
    normalized = normalize_srlinux_state(collected)

    items = diff_desired_vs_actual(desired, normalized)

    asn_drift = [i for i in items if i.path.endswith("/peer_asn")]
    assert len(asn_drift) == 1
    assert asn_drift[0].desired == 65099
    assert asn_drift[0].actual == 65011
