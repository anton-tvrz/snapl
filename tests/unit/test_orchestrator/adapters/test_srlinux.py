"""Adapter tests for the SR Linux collect→diff normalization (#32).

Driven by a fixture captured from a live dcfabric SR Linux node, so the adapter
is validated against the real device shape rather than a hand-idealized one.

The ``/network-instance[name=default]`` entry was read from the same node on
2026-11-19, after #315's deploy, and kept to the fixture's three addressed
interfaces (the node also binds ethernet-1/3.0 and ethernet-1/4.0).
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


_DESCRIPTION_ONLY_FIXTURE = _FIXTURE.parent / "srlinux_v26_3_1_description_only_port.json"


def test_a_description_only_port_reports_its_description_and_enabled_as_undesired() -> None:
    """Real v26.3.1 payload (#122, #175): the device adds its default mtu to a port given
    only a description, and that default must not read as hand-added config. Creating
    the entry also enables the port, which is reported (spec 007 FR-003, amended). The
    untouched neighbouring port reports disabled and stays invisible."""
    collected = json.loads(_DESCRIPTION_ONLY_FIXTURE.read_text())
    normalized = normalize_srlinux_state(collected)
    assert normalized["/interface[name=ethernet-1/7]"]["mtu"] == 9232, "fixture no longer shows the device default"
    assert normalized["/interface[name=ethernet-1/7]"]["enabled"] is True, "a created entry defaults to enable"
    assert normalized["/interface[name=ethernet-1/8]"]["enabled"] is False, "an untouched port reports disable"

    items = diff_desired_vs_actual(_desired_matching_fixture(), normalized)

    assert [(item.path, item.actual) for item in items if item.undesired] == [
        ("/interface[name=ethernet-1/7]/description", "hand-added-by-operator"),
        ("/interface[name=ethernet-1/7]/enabled", True),
    ]


# ---------------------------------------------------------------------------
# Every IPv4 address, not only the first (#193)
# ---------------------------------------------------------------------------


def _with_extra_address(collected: dict, name: str, prefix: str, *, first: bool = False) -> dict:
    """The captured payload with one more address on ``name``'s subinterface 0.

    The entry copies the shape the device returned for the address already
    there (``ip-prefix`` and ``origin``). The payload with a hand-added address
    has not been captured from the lab yet.
    """
    for iface in collected["/interface"]["srl_nokia-interfaces:interface"]:
        if iface["name"] == name:
            addresses = iface["subinterface"][0]["ipv4"]["address"]
            entry = {"ip-prefix": prefix, "origin": "static"}
            addresses.insert(0, entry) if first else addresses.append(entry)
            return collected
    raise AssertionError(f"{name} is not in the fixture")


def test_every_ipv4_address_is_normalized_with_its_path(collected: dict) -> None:
    out = normalize_srlinux_state(_with_extra_address(collected, "ethernet-1/1", "10.99.0.1/31"))

    eth1 = out["/interface[name=ethernet-1/1]"]
    assert eth1["ipv4_addresses"] == [
        {"prefix": "10.10.1.0/31", "path": "subinterface[index=0]/ipv4/address[ip-prefix=10.10.1.0/31]"},
        {"prefix": "10.99.0.1/31", "path": "subinterface[index=0]/ipv4/address[ip-prefix=10.99.0.1/31]"},
    ]
    # The single-address fields still carry the first address.
    assert eth1["ip_address"] == "10.10.1.0"
    assert eth1["prefix_length"] == 31


def test_an_interface_without_addresses_normalizes_to_an_empty_list() -> None:
    out = normalize_srlinux_state({"/interface": {"srl_nokia-interfaces:interface": [{"name": "ethernet-1/9"}]}})

    assert out["/interface[name=ethernet-1/9]"]["ipv4_addresses"] == []


def test_an_extra_address_on_an_intent_managed_interface_is_undesired(collected: dict) -> None:
    normalized = normalize_srlinux_state(_with_extra_address(collected, "ethernet-1/1", "10.99.0.1/31"))

    items = diff_desired_vs_actual(_desired_matching_fixture(), normalized)

    assert [(item.path, item.desired, item.actual, item.undesired) for item in items] == [
        (
            "/interface[name=ethernet-1/1]/subinterface[index=0]/ipv4/address[ip-prefix=10.99.0.1/31]",
            None,
            "10.99.0.1/31",
            True,
        )
    ]


def test_an_extra_address_listed_first_does_not_read_as_a_wrong_intended_address(collected: dict) -> None:
    """The device decides the order. The intended address is still there, so
    only the extra one is reported."""
    normalized = normalize_srlinux_state(_with_extra_address(collected, "ethernet-1/1", "10.1.0.1/31", first=True))

    items = diff_desired_vs_actual(_desired_matching_fixture(), normalized)

    assert [(item.path, item.undesired) for item in items] == [
        ("/interface[name=ethernet-1/1]/subinterface[index=0]/ipv4/address[ip-prefix=10.1.0.1/31]", True)
    ]


# --- #315: subinterfaces in the default network instance ---------------------

# Measured on SR Linux v26.3.1 (2026-11-19): a GET of
# /network-instance[name=<ni>]/interface comes back keyed by the instance entry,
# with state leaves beside the name. An instance with no binding comes back empty.
_BOUND = {
    "/network-instance[name=default]": {
        "interface": [
            {"name": "ethernet-1/1.0", "oper-state": "up", "index": "1"},
            {"name": "lo0.0", "oper-state": "up", "index": "2"},
        ]
    }
}


def test_drift_paths_collect_the_default_instances_interfaces() -> None:
    assert "/network-instance[name=default]/interface" in DRIFT_PATHS


def test_each_bound_subinterface_normalizes_to_its_own_entry() -> None:
    out = normalize_srlinux_state(_BOUND)

    assert out["/network-instance[name=default]/interface[name=ethernet-1/1.0]"] == {"name": "ethernet-1/1.0"}
    assert out["/network-instance[name=default]/interface[name=lo0.0]"] == {"name": "lo0.0"}


def test_bindings_keyed_by_the_requested_path_are_read_too() -> None:
    data = {"/network-instance[name=default]/interface": _BOUND["/network-instance[name=default]"]}

    assert "/network-instance[name=default]/interface[name=lo0.0]" in normalize_srlinux_state(data)


def test_an_instance_with_no_binding_normalizes_to_nothing() -> None:
    out = normalize_srlinux_state({})

    assert not [key for key in out if key.startswith("/network-instance[name=default]/interface")]


def _addressed(name: str, ip: str, length: int) -> Interface:
    return Interface(id=uuid4(), device_id=uuid4(), name=name, ip_address=ip, prefix_length=length, enabled=True)


def _desired_with(interfaces: list[Interface]) -> DesiredState:
    device = Device(id=uuid4(), name="spine-01", management_address="10.0.0.1", role="spine", use_case="dcfabric")
    return DesiredState(device=device, interfaces=interfaces)


def test_a_missing_binding_is_drift() -> None:
    desired = _desired_with([_addressed("ethernet-1/1", "10.10.1.0", 31)])
    actual = normalize_srlinux_state({"/interface": {"interface": []}})

    items = [i for i in diff_desired_vs_actual(desired, actual) if i.entity_kind == "routed_interface"]

    assert [(i.path, i.desired, i.actual, i.undesired) for i in items] == [
        ("/network-instance[name=default]/interface[name=ethernet-1/1.0]/name", "ethernet-1/1.0", None, False)
    ]


def test_a_binding_intent_never_asked_for_is_undesired() -> None:
    desired = _desired_with([_addressed("ethernet-1/1", "10.10.1.0", 31)])
    actual = normalize_srlinux_state(
        {
            "/network-instance[name=default]": {
                "interface": [{"name": "ethernet-1/1.0"}, {"name": "ethernet-1/9.0"}],
            }
        }
    )

    items = [i for i in diff_desired_vs_actual(desired, actual) if i.entity_kind == "routed_interface"]

    assert [(i.path, i.actual, i.undesired) for i in items] == [
        ("/network-instance[name=default]/interface[name=ethernet-1/9.0]/name", "ethernet-1/9.0", True)
    ]


# --- #317: routing policies, and the policies each neighbour names ------------

_POLICY = {
    "/routing-policy": {
        "srl_nokia-routing-policy:routing-policy": {
            "prefix-set": [
                {"name": "underlay-loopbacks", "prefix": [{"ip-prefix": "10.1.0.0/24", "mask-length-range": "32..32"}]}
            ],
            "policy": [
                {
                    "name": "underlay-loopbacks",
                    "default-action": {"policy-result": "reject"},
                    "statement": [
                        {
                            "name": "accept",
                            "match": {"prefix": {"prefix-set": "underlay-loopbacks"}},
                            "action": {"policy-result": "accept"},
                        }
                    ],
                }
            ],
        }
    }
}
_POLICY_PATH = "/routing-policy/policy[name=underlay-loopbacks]"


def _policy_container() -> dict:
    return json.loads(json.dumps(_POLICY["/routing-policy"]["srl_nokia-routing-policy:routing-policy"]))


def test_drift_paths_collect_the_routing_policies() -> None:
    assert "/routing-policy" in DRIFT_PATHS


def test_a_policy_normalizes_to_what_it_accepts_and_its_default() -> None:
    out = normalize_srlinux_state(_POLICY)

    assert out[_POLICY_PATH] == {
        "name": "underlay-loopbacks",
        "accept_prefixes": ["10.1.0.0/24 32..32"],
        "default_action": "reject",
    }


def test_a_policy_answered_without_its_container_is_read_too() -> None:
    assert _POLICY_PATH in normalize_srlinux_state({"/routing-policy": _policy_container()})


def test_a_prefix_with_no_mask_length_range_matches_exactly() -> None:
    container = _policy_container()
    del container["prefix-set"][0]["prefix"][0]["mask-length-range"]

    out = normalize_srlinux_state({"/routing-policy": container})

    assert out[_POLICY_PATH]["accept_prefixes"] == ["10.1.0.0/24 exact"]


def test_a_statement_intent_cannot_write_shows_as_itself() -> None:
    """Intent writes one shape of statement: accept a prefix-set. Anything else
    on the device changes what the policy does, so it must not vanish from the
    view, or the policy would look in sync while it accepts more."""
    container = _policy_container()
    container["policy"][0]["statement"].append(
        {"name": "everything-bgp", "match": {"protocol": "bgp"}, "action": {"policy-result": "accept"}}
    )

    out = normalize_srlinux_state({"/routing-policy": container})

    assert out[_POLICY_PATH]["accept_prefixes"] == ["10.1.0.0/24 32..32", "statement everything-bgp"]


def _neighbor(**extra) -> dict:
    return {
        "/network-instance[name=default]/protocols/bgp": {
            "neighbor": [{"peer-address": "10.10.1.0", "peer-as": 65000, "admin-state": "enable", **extra}]
        }
    }


def test_a_neighbours_policies_normalize_to_their_names() -> None:
    out = normalize_srlinux_state(
        _neighbor(**{"export-policy": ["underlay-loopbacks"], "import-policy": ["underlay-loopbacks"]})
    )

    entry = out["/network-instance[name=default]/protocols/bgp/neighbor[peer-address=10.10.1.0]"]
    assert (entry["export_policy"], entry["import_policy"]) == ("underlay-loopbacks", "underlay-loopbacks")


def test_a_neighbour_with_no_policy_normalizes_to_none() -> None:
    out = normalize_srlinux_state(_neighbor())
    entry = out["/network-instance[name=default]/protocols/bgp/neighbor[peer-address=10.10.1.0]"]

    assert (entry["export_policy"], entry["import_policy"]) == (None, None)


def test_a_neighbour_with_two_policies_shows_both() -> None:
    entry = normalize_srlinux_state(_neighbor(**{"export-policy": ["a", "b"]}))[
        "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=10.10.1.0]"
    ]

    assert entry["export_policy"] == "a, b"


def _desired_with_policy() -> DesiredState:
    from snapl_intent.models import RoutingPolicy

    device = Device(id=uuid4(), name="leaf-01", management_address="10.0.0.11", role="leaf", use_case="dcfabric")
    session = BGPSession(
        id=uuid4(),
        device_id=device.id,
        local_asn=65011,
        peer_address="10.10.1.0",
        peer_asn=65000,
        export_policy="underlay-loopbacks",
        import_policy="underlay-loopbacks",
    )
    policy = RoutingPolicy(name="underlay-loopbacks", accept_prefixes=["10.1.0.0/24 32..32"])
    return DesiredState(device=device, bgp_sessions=[session], routing_policies=[policy])


def test_a_device_without_the_policy_drifts_on_the_policy_and_each_neighbour() -> None:
    """The lab before #317's deploy: sessions up, no policy anywhere."""
    items = diff_desired_vs_actual(_desired_with_policy(), normalize_srlinux_state(_neighbor()))

    assert sorted((i.entity_kind, i.path, i.desired, i.actual) for i in items) == [
        (
            "bgp_session",
            "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=10.10.1.0]/export_policy",
            "underlay-loopbacks",
            None,
        ),
        (
            "bgp_session",
            "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=10.10.1.0]/import_policy",
            "underlay-loopbacks",
            None,
        ),
        ("routing_policy", f"{_POLICY_PATH}/accept_prefixes", ["10.1.0.0/24 32..32"], None),
        ("routing_policy", f"{_POLICY_PATH}/default_action", "reject", None),
    ]


def test_a_device_with_the_policy_is_in_sync() -> None:
    actual = normalize_srlinux_state(
        {
            **_POLICY,
            **_neighbor(**{"export-policy": ["underlay-loopbacks"], "import-policy": ["underlay-loopbacks"]}),
        }
    )

    assert diff_desired_vs_actual(_desired_with_policy(), actual) == []


def test_a_policy_intent_never_asked_for_is_undesired() -> None:
    container = _policy_container()
    container["policy"].append({"name": "hand-made", "default-action": {"policy-result": "accept"}})
    actual = normalize_srlinux_state(
        {
            "/routing-policy": container,
            **_neighbor(**{"export-policy": ["underlay-loopbacks"], "import-policy": ["underlay-loopbacks"]}),
        }
    )

    items = diff_desired_vs_actual(_desired_with_policy(), actual)

    assert [(i.path, i.undesired) for i in items] == [("/routing-policy/policy[name=hand-made]/default_action", True)]


# Measured on leaf-01, SR Linux v26.3.1 (2026-11-20), after #317's deploy: a GET
# of /routing-policy with datatype all answers with the container's children,
# and fills every match container with its defaults (match-set-options any).
_MEASURED_POLICY = Path(__file__).resolve().parent.parent / "fixtures" / "srlinux_leaf01_routing_policy.json"


def test_the_measured_policy_normalizes_to_what_intent_wrote() -> None:
    out = normalize_srlinux_state({"/routing-policy": json.loads(_MEASURED_POLICY.read_text())})

    assert out[_POLICY_PATH] == {
        "name": "underlay-loopbacks",
        "accept_prefixes": ["10.1.0.0/24 32..32"],
        "default_action": "reject",
    }


def test_a_default_filled_statement_with_a_real_second_condition_shows_as_itself() -> None:
    container = json.loads(_MEASURED_POLICY.read_text())
    container["policy"][0]["statement"][0]["match"]["protocol"] = "bgp"

    out = normalize_srlinux_state({"/routing-policy": container})

    assert out[_POLICY_PATH]["accept_prefixes"] == ["statement accept"]
