"""Render → normalize → diff contract tests (#59, #64, #72).

The executor's rendered payload, replayed through the SR Linux adapter, must
diff CLEAN against the intent that produced it — otherwise the closed loop can
never verify green. These tests pin the contract from both ends:

- every field the structural diff compares is rendered by the templates, and
- every desired entity appears in the rendered payload.

If a field is ever added to ``ENTITY_FIELD_MAP`` without a matching renderer
change (or vice versa), these tests fail instead of production deploys
terminating VERIFICATION_FAILED forever.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from snapl_executor.gnmi.renderer import ConfigRenderer
from snapl_intent.models import BGPSession, DesiredState, Device, Interface
from snapl_observability.structural.diff import ENTITY_FIELD_MAP, diff_desired_vs_actual
from snapl_orchestrator.adapters.srlinux import normalize_srlinux_state

pytestmark = pytest.mark.unit

_BGP_PATH = "/network-instance[name=default]/protocols/bgp"


def _full_desired_state() -> DesiredState:
    """A DesiredState that exercises every diff-compared field.

    Includes an IP-less enabled interface and a disabled interface (#64), and
    BGP sessions with and without a peer group, so entity-level coverage is
    enforced alongside field-level coverage.
    """
    dev_id = uuid4()
    device = Device(
        id=dev_id,
        name="spine-01",
        management_address="10.0.0.1",
        role="spine",
        use_case="dcfabric",
        description="Spine 01",
    )
    interfaces = [
        Interface(
            id=uuid4(),
            device_id=dev_id,
            name="ethernet-1/1",
            description="to leaf-01:ethernet-1/49",
            ip_address="10.10.1.0",
            prefix_length=31,
            enabled=True,
            mtu=9214,
        ),
        # IP-less but enabled — must still be rendered (#64).
        Interface(
            id=uuid4(),
            device_id=dev_id,
            name="ethernet-1/10",
            description="l2 access",
            enabled=True,
            mtu=9214,
        ),
        # Disabled — the shutdown must be pushed, not skipped (#64).
        Interface(
            id=uuid4(),
            device_id=dev_id,
            name="ethernet-1/11",
            enabled=False,
        ),
        # Loopback — mtu-less, must render without an mtu key and round-trip
        # clean as mtu None == None (#78).
        Interface(
            id=uuid4(),
            device_id=dev_id,
            name="lo0",
            description="Router ID spine-01",
            ip_address="10.1.0.1",
            prefix_length=32,
            enabled=True,
            mtu=None,
        ),
    ]
    sessions = [
        BGPSession(
            id=uuid4(),
            device_id=dev_id,
            local_asn=65000,
            peer_address="10.10.1.1",
            peer_asn=65001,
            peer_group="underlay-ipv4",
            enabled=True,
        ),
        BGPSession(
            id=uuid4(),
            device_id=dev_id,
            local_asn=65000,
            peer_address="10.10.2.1",
            peer_asn=65002,
            enabled=False,
        ),
    ]
    return DesiredState(device=device, interfaces=interfaces, bgp_sessions=sessions)


def _replay_through_adapter(payload: dict) -> dict:
    """Wrap a rendered payload in the collector's path-keyed shape and normalize.

    Simulates a device that accepted the SET verbatim and reported the same
    containers back through a gNMI GET on the drift paths.
    """
    data: dict = {"/interface": {"interface": payload["interface"]}}
    if "network-instance" in payload:
        data[_BGP_PATH] = payload["network-instance"][0]["protocols"]["bgp"]
    return normalize_srlinux_state(data)


def test_fixture_exercises_every_diff_field() -> None:
    """Meta-guard: the contract fixture populates every compared field.

    If ENTITY_FIELD_MAP grows a field (or an entity kind), this fails until the
    fixture exercises it — which then forces the render contract below to hold.
    """
    desired = _full_desired_state()
    entities: dict[str, list] = {
        "interface": list(desired.interfaces),
        "bgp_session": list(desired.bgp_sessions),
    }
    for kind, spec in ENTITY_FIELD_MAP.items():
        pool = entities[kind]
        for field in spec["fields"]:
            assert any(getattr(entity, field, None) is not None for entity in pool), (
                f"{kind}.{field} is compared by the diff but never populated in "
                f"the contract fixture — extend _full_desired_state()"
            )


def test_rendered_payload_diffs_clean_against_its_intent() -> None:
    desired = _full_desired_state()
    payload = ConfigRenderer(use_case="dcfabric").render(desired)

    items = diff_desired_vs_actual(desired, _replay_through_adapter(payload))

    assert items == [], "renderer/diff contract violated: " + ", ".join(
        f"{i.path} desired={i.desired!r} actual={i.actual!r}" for i in items
    )


def test_every_desired_entity_is_rendered() -> None:
    desired = _full_desired_state()
    payload = ConfigRenderer(use_case="dcfabric").render(desired)
    normalized = _replay_through_adapter(payload)

    for iface in desired.interfaces:
        assert f"/interface[name={iface.name}]" in normalized, f"{iface.name} missing from rendered payload"
    for session in desired.bgp_sessions:
        key = f"{_BGP_PATH}/neighbor[peer-address={session.peer_address}]"
        assert key in normalized, f"neighbor {session.peer_address} missing from rendered payload"
