"""The topology projection (spec 011 T001, research R6).

Links exist only in intent: each interface names its peer. The projection
turns those declarations into one link per pair of ends, and says when the two
ends disagree instead of correcting them.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from snapl_presentation.dashboard.models import LinkEnd, TopologyLink
from snapl_presentation.dashboard.projections import project_topology
from tests.unit.test_presentation.dashboard_fakes import seed_states, state

pytestmark = pytest.mark.unit


def _ends(link: TopologyLink) -> tuple[str, str, str, str]:
    return (link.a.device, link.a.interface, link.b.device, link.b.interface)


def test_dcfabric_seed_is_six_devices_and_eight_agreed_links():
    topology = project_topology(seed_states("dcfabric"))

    assert [node.name for node in topology.nodes] == [
        "spine-01",
        "spine-02",
        "leaf-01",
        "leaf-02",
        "leaf-03",
        "leaf-04",
    ]
    assert [node.role for node in topology.nodes] == ["spine", "spine", "leaf", "leaf", "leaf", "leaf"]
    assert len(topology.links) == 8
    assert all(link.declared_by == "both" and link.agreed for link in topology.links)
    assert topology.unlinked == []
    # Each spine reaches each leaf once, on the interfaces the seed declares.
    assert _ends(topology.links[0]) == ("leaf-01", "ethernet-1/49", "spine-01", "ethernet-1/1")
    assert {(link.a.device, link.b.device) for link in topology.links} == {
        (leaf, spine) for leaf in ("leaf-01", "leaf-02", "leaf-03", "leaf-04") for spine in ("spine-01", "spine-02")
    }


def test_test_edge_seed_is_two_devices_and_one_link():
    topology = project_topology(seed_states("test_edge"))

    assert [node.name for node in topology.nodes] == ["edge-01", "edge-02"]
    assert [_ends(link) for link in topology.links] == [("edge-01", "ethernet-1/1", "edge-02", "ethernet-1/1")]
    assert topology.links[0].agreed


def test_nodes_sort_by_role_tier_then_by_name_with_numbers_as_numbers():
    states = [
        state("leaf-10", "leaf"),
        state("wan-1", "wan"),
        state("leaf-2", "leaf"),
        state("border-1", "border"),
        state("spine-1", "spine"),
    ]

    assert [node.name for node in project_topology(states).nodes] == [
        "spine-1",
        "leaf-2",
        "leaf-10",
        "border-1",
        "wan-1",
    ]


def test_a_link_declared_from_both_ends_appears_once_with_its_ends_ordered():
    states = [
        state("spine-1", "spine", links=[("e1", "leaf-1", "e49")]),
        state("leaf-1", "leaf", links=[("e49", "spine-1", "e1")]),
    ]

    links = project_topology(states).links

    assert [_ends(link) for link in links] == [("leaf-1", "e49", "spine-1", "e1")]
    assert links[0].declared_by == "both"


def test_a_link_declared_at_one_end_only_names_that_end_and_is_not_agreed():
    states = [
        state("spine-1", "spine", links=[("e1", "leaf-1", "e49")]),
        state("leaf-1", "leaf"),
    ]

    (link,) = project_topology(states).links

    assert _ends(link) == ("leaf-1", "e49", "spine-1", "e1")
    assert link.declared_by == "b"
    assert link.agreed is False


def test_ends_that_name_different_interfaces_of_each_other_do_not_agree():
    states = [
        state("spine-1", "spine", links=[("e1", "leaf-1", "e49")]),
        state("leaf-1", "leaf", links=[("e49", "spine-1", "e2")]),
    ]

    links = project_topology(states).links

    # Intent is shown as it is: two one-sided claims, neither corrected.
    assert [_ends(link) for link in links] == [
        ("leaf-1", "e49", "spine-1", "e1"),
        ("leaf-1", "e49", "spine-1", "e2"),
    ]
    assert [link.declared_by for link in links] == ["b", "a"]
    assert not any(link.agreed for link in links)


def test_a_peer_outside_the_use_case_or_the_device_itself_is_unlinked():
    states = [
        state("leaf-1", "leaf", links=[("e1", "carrier-pe", "ge-0/0/0"), ("e2", "leaf-1", "e3"), ("lo0", None, None)]),
    ]

    topology = project_topology(states)

    assert topology.links == []
    assert [(u.device, u.interface, u.peer_device, u.peer_interface) for u in topology.unlinked] == [
        ("leaf-1", "e1", "carrier-pe", "ge-0/0/0"),
        ("leaf-1", "e2", "leaf-1", "e3"),
    ]


def test_a_peer_named_without_its_interface_is_unlinked_unless_the_peer_declares_the_link():
    declared_back = [
        state("spine-1", "spine", links=[("e1", "leaf-1", None)]),
        state("leaf-1", "leaf", links=[("e49", "spine-1", "e1")]),
    ]
    alone = [state("spine-1", "spine", links=[("e1", "leaf-1", None)]), state("leaf-1", "leaf")]

    topology = project_topology(declared_back)
    assert [(link.declared_by, link.agreed) for link in topology.links] == [("a", False)]
    assert topology.unlinked == []

    topology = project_topology(alone)
    assert topology.links == []
    assert [(u.device, u.interface, u.peer_device, u.peer_interface) for u in topology.unlinked] == [
        ("spine-1", "e1", "leaf-1", None)
    ]


def test_no_devices_is_an_empty_topology():
    topology = project_topology([])

    assert (topology.nodes, topology.links, topology.unlinked) == ([], [], [])


# --- model rules (data-model.md) -------------------------------------------------


def test_a_link_cannot_be_agreed_unless_both_ends_declared_it():
    with pytest.raises(ValidationError, match="agreed"):
        TopologyLink(
            a=LinkEnd(device="leaf-1", interface="e49"),
            b=LinkEnd(device="spine-1", interface="e1"),
            declared_by="a",
            agreed=True,
        )


def test_a_link_joins_two_different_devices():
    with pytest.raises(ValidationError, match="different devices"):
        TopologyLink(
            a=LinkEnd(device="leaf-1", interface="e1"),
            b=LinkEnd(device="leaf-1", interface="e2"),
            declared_by="both",
            agreed=True,
        )
