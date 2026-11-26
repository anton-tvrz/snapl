"""Drift reports for the removal planner's tests (spec 013, T002 and T006).

``REPORTS`` holds one report per row of contracts/blocks.md's table, plus one
built by the real diff from a device carrying every removable kind, plus
adversarial ones naming the protected interfaces. ``test_removal_plan.py``
asserts the planner's output for each, and ``test_removal_paths.py`` holds the
planner and the executor's guard to agree on all of them (SC-003).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

DEVICE_ID = UUID("00000000-0000-0000-0000-000000000004")
SCANNED_AT = datetime(2026, 11, 24, 14, 5, 12, tzinfo=UTC)

PORT = "/interface[name=ethernet-1/7]"
BINDING = "/network-instance[name=default]/interface[name=ethernet-1/7.0]"
NEIGHBOR = "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=192.0.2.0]"
EXTRA_ADDRESS = "/interface[name=ethernet-1/49]/subinterface[index=0]/ipv4/address[ip-prefix=192.0.2.1/31]"
POLICY = "/routing-policy/policy[name=c-probe]"
UPLINK_NEIGHBOR = "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=10.0.1.6]"


def undesired(path: str, actual: Any, kind: str) -> Any:
    from snapl_observability.models import DriftItem

    return DriftItem(path=path, desired=None, actual=actual, entity_kind=kind, undesired=True)


def forward(path: str, desired: Any, actual: Any, kind: str = "interface") -> Any:
    from snapl_observability.models import DriftItem

    return DriftItem(path=path, desired=desired, actual=actual, entity_kind=kind)


def report(*items: Any) -> Any:
    from snapl_observability.models import DriftReport, DriftStatus

    return DriftReport(
        device_id=DEVICE_ID,
        device_name="leaf-04",
        status=DriftStatus.DRIFTED if items else DriftStatus.CLEAN,
        items=list(items),
        timestamp=SCANNED_AT,
    )


def _diffed_report() -> Any:
    """leaf-04 as the captures left it, read through the real diff."""
    from snapl_intent.models import BGPSession, DesiredState, Device, Interface
    from snapl_observability.structural.diff import diff_desired_vs_actual

    device = Device(
        id=DEVICE_ID,
        name="leaf-04",
        management_address="172.20.21.14",
        role="leaf",
        use_case="dcfabric",
        platform="nokia-srlinux",
    )
    uplink = Interface(
        id=uuid4(),
        device_id=DEVICE_ID,
        name="ethernet-1/49",
        ip_address="10.0.1.7",
        prefix_length=31,
        enabled=True,
    )
    session = BGPSession(
        id=uuid4(),
        device_id=DEVICE_ID,
        local_asn=65104,
        peer_address="10.0.1.6",
        peer_asn=65000,
        peer_group="underlay-ipv4@leaf-04",
        enabled=True,
    )
    desired = DesiredState(device=device, interfaces=[uplink], bgp_sessions=[session])

    def address(prefix: str) -> dict[str, str]:
        return {"prefix": prefix, "path": f"subinterface[index=0]/ipv4/address[ip-prefix={prefix}]"}

    actual = {
        "/interface[name=ethernet-1/49]": {
            "description": None,
            "ip_address": "10.0.1.7",
            "prefix_length": 31,
            "enabled": True,
            "mtu": None,
            "ipv4_addresses": [address("10.0.1.7/31"), address("192.0.2.1/31")],
        },
        PORT: {
            "description": "c-probe",
            "ip_address": "192.0.2.5",
            "prefix_length": 31,
            "enabled": True,
            "mtu": 9232,
        },
        # Protected and bare ports the diff must keep out of the report.
        "/interface[name=mgmt0]": {"description": "mgmt", "ip_address": "172.20.21.14", "enabled": True},
        "/interface[name=ethernet-1/1]": {"enabled": False},
        UPLINK_NEIGHBOR: {
            "peer_address": "10.0.1.6",
            "peer_asn": 65000,
            "peer_group": "underlay-ipv4@leaf-04",
            "enabled": True,
        },
        NEIGHBOR: {"peer_address": "192.0.2.0", "peer_asn": 65099, "peer_group": "c-probe-group"},
        "/network-instance[name=default]/interface[name=ethernet-1/49.0]": {"name": "ethernet-1/49.0"},
        BINDING: {"name": "ethernet-1/7.0"},
        POLICY: {"accept_prefixes": ["192.0.2.0/24"], "default_action": "reject"},
    }
    return report(*diff_desired_vs_actual(desired, actual))


def reports() -> dict[str, Any]:
    """Every report the planner is tested on, by name."""
    protected_binding = "/network-instance[name=default]/interface[name=system0.0]"
    return {
        "clean": report(),
        "port_fields": report(
            undesired(f"{PORT}/description", "c-probe", "interface"),
            undesired(f"{PORT}/enabled", True, "interface"),
        ),
        "port_enabled_only": report(undesired(f"{PORT}/enabled", True, "interface")),
        "extra_address": report(undesired(EXTRA_ADDRESS, "192.0.2.1/31", "interface")),
        "neighbor": report(
            undesired(f"{NEIGHBOR}/peer_asn", 65099, "bgp_session"),
            undesired(f"{NEIGHBOR}/peer_group", "c-probe-group", "bgp_session"),
        ),
        "port_with_binding": report(
            undesired(f"{PORT}/ip_address", "192.0.2.5", "interface"),
            undesired(f"{PORT}/prefix_length", 31, "interface"),
            undesired(f"{BINDING}/name", "ethernet-1/7.0", "routed_interface"),
        ),
        "system0_binding": report(undesired(f"{protected_binding}/name", "system0.0", "routed_interface")),
        "routing_policy": report(
            undesired(f"{POLICY}/accept_prefixes", ["192.0.2.0/24"], "routing_policy"),
            undesired(f"{POLICY}/default_action", "reject", "routing_policy"),
        ),
        "unknown_kind": report(
            undesired("/network-instance[name=default]/protocols/ospf/area[id=0]/name", "0", "ospf_area"),
        ),
        "unknown_shape": report(
            # A subinterface leaf on a known kind: the key would swallow the
            # subinterface, so it must be refused rather than planned.
            undesired("/interface[name=ethernet-1/7]/subinterface[index=0]/description", "x", "interface"),
            # An IPv6 address: no row in the table.
            undesired(
                "/interface[name=ethernet-1/49]/subinterface[index=0]/ipv6/address[ip-prefix=2001:db8::1/64]",
                "2001:db8::1/64",
                "interface",
            ),
            # A field the kind does not compare.
            undesired(f"{NEIGHBOR}/local_as", 65104, "bgp_session"),
            # A network instance other than default.
            undesired(
                "/network-instance[name=mgmt]/protocols/bgp/neighbor[peer-address=192.0.2.9]/peer_asn",
                65001,
                "bgp_session",
            ),
        ),
        "forward_and_undesired": report(
            forward("/interface[name=ethernet-1/49]/description", "uplink", None),
            forward(UPLINK_NEIGHBOR + "/enabled", True, False, "bgp_session"),
            undesired(f"{PORT}/description", "c-probe", "interface"),
        ),
        "diffed": _diffed_report(),
        # Adversarial: the diff never reports these, the planner must still refuse them.
        "protected_interfaces": report(
            undesired("/interface[name=mgmt0]/description", "mgmt", "interface"),
            undesired("/interface[name=system0]/ip_address", "10.255.0.4", "interface"),
            undesired(
                "/interface[name=mgmt0]/subinterface[index=0]/ipv4/address[ip-prefix=172.20.21.99/24]",
                "172.20.21.99/24",
                "interface",
            ),
            undesired("/network-instance[name=default]/interface[name=mgmt0.0]/name", "mgmt0.0", "routed_interface"),
            undesired(f"{protected_binding}/name", "system0.0", "routed_interface"),
        ),
    }
