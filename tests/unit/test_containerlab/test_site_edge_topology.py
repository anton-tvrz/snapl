"""Unit tests for the site_edge containerlab topology (spec 008, contracts C-7).

A sibling of test_topology.py: the same address-registry invariants for the
second lab, so both can run at once without sharing an address (FR-015).

Subnet registry (one /24 per lab, dev/knowledge/host-resource-registry.md):
    172.20.20.0/24  project-network-synapse-quattro  spine-leaf-lab
    172.20.21.0/24  snapl                            dcfabric
    172.20.22.0/24  snapl                            site_edge (this lab)

The seed's ``lab_node_name`` guard joins this file with the seed (T055).
"""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).parents[3]
TOPOLOGY_PATH = _ROOT / "containerlab" / "site_edge.yml"
DCFABRIC_PATH = _ROOT / "containerlab" / "dcfabric.yml"

SITE_EDGE_SUBNET = IPv4Network("172.20.22.0/24")
OTHER_SUBNETS = (IPv4Network("172.20.20.0/24"), IPv4Network("172.20.21.0/24"))

ROUTERS = {"site-edge-01": "172.20.22.11", "carrier-a": "172.20.22.21"}
HOSTS = {
    "ot-host": "172.20.22.31",
    "office-host": "172.20.22.32",
    "historian": "172.20.22.33",
    "wan-host": "172.20.22.34",
}

# Research R13.
LINKS = {
    frozenset({"site-edge-01:e1-1", "carrier-a:e1-1"}),  # WAN
    frozenset({"site-edge-01:e1-2", "office-host:eth1"}),
    frozenset({"site-edge-01:e1-3", "ot-host:eth1"}),
    frozenset({"carrier-a:e1-2", "historian:eth1"}),
    frozenset({"carrier-a:e1-3", "wan-host:eth1"}),
}

# The router ports intent enables (research R14): the WAN link and the two
# segments on the site edge, the WAN link and the two subnets on the carrier.
ENABLED_ROUTER_PORTS = (
    "site-edge-01:e1-1",
    "site-edge-01:e1-2",
    "site-edge-01:e1-3",
    "carrier-a:e1-1",
    "carrier-a:e1-2",
    "carrier-a:e1-3",
)


@pytest.fixture(scope="module")
def topology() -> dict:
    return yaml.safe_load(TOPOLOGY_PATH.read_text())


@pytest.fixture(scope="module")
def nodes(topology: dict) -> dict:
    return topology["topology"]["nodes"]


@pytest.fixture(scope="module")
def links(topology: dict) -> list[frozenset[str]]:
    return [frozenset(link["endpoints"]) for link in topology["topology"]["links"]]


def test_the_lab_is_named_site_edge(topology: dict) -> None:
    """Container names come out as clab-site-edge-<node>, which the lab tasks rely on."""
    assert topology["name"] == "site-edge"


def test_declares_its_own_mgmt_network(topology: dict) -> None:
    mgmt = topology["mgmt"]
    assert mgmt["network"] == "clab-snapl-site-edge"
    assert mgmt["ipv4-subnet"] == str(SITE_EDGE_SUBNET)


def test_mgmt_network_overlaps_no_other_registered_lab(topology: dict) -> None:
    subnet = IPv4Network(topology["mgmt"]["ipv4-subnet"])
    for other in OTHER_SUBNETS:
        assert not subnet.overlaps(other), f"{subnet} overlaps {other}"


def test_the_nodes_are_the_two_routers_and_four_hosts(nodes: dict) -> None:
    assert set(nodes) == set(ROUTERS) | set(HOSTS)


@pytest.mark.parametrize(("name", "pin"), sorted({**ROUTERS, **HOSTS}.items()))
def test_every_node_is_pinned_inside_the_subnet(nodes: dict, name: str, pin: str) -> None:
    assert nodes[name]["mgmt-ipv4"] == pin
    assert IPv4Address(pin) in SITE_EDGE_SUBNET


def test_routers_are_srlinux_ixr_d2(topology: dict, nodes: dict) -> None:
    default_kind = topology["topology"].get("defaults", {}).get("kind")
    for name in ROUTERS:
        assert nodes[name].get("kind", default_kind) == "nokia_srlinux", name
        assert nodes[name]["type"] == "ixr-d2", name


def test_srlinux_uses_the_same_pinned_image_as_dcfabric(topology: dict) -> None:
    dcfabric = yaml.safe_load(DCFABRIC_PATH.read_text())
    expected = dcfabric["topology"]["kinds"]["nokia_srlinux"]["image"]
    assert topology["topology"]["kinds"]["nokia_srlinux"]["image"] == expected


def test_hosts_are_linux_with_an_image_pinned_by_digest(topology: dict, nodes: dict) -> None:
    linux = topology["topology"]["kinds"]["linux"]
    image = linux["image"]
    repo_and_tag, _, digest = image.partition("@")
    assert repo_and_tag.split(":")[0] == "ghcr.io/srl-labs/network-multitool"
    assert digest.startswith("sha256:"), f"pin the host image by digest, got {image!r}"
    assert len(digest) == len("sha256:") + 64
    for name in HOSTS:
        assert nodes[name]["kind"] == "linux", name
        assert "image" not in nodes[name], f"{name} overrides the pinned host image"


def test_the_links_are_research_r13s(links: list[frozenset[str]]) -> None:
    assert len(links) == len(set(links)), "a link is declared twice"
    assert set(links) == LINKS


@pytest.mark.parametrize("port", ENABLED_ROUTER_PORTS)
def test_every_enabled_router_port_has_a_link_partner(links: list[frozenset[str]], port: str) -> None:
    """Health reads a port without a partner as down, forever (FR-019, research R18)."""
    assert sum(port in link for link in links) == 1, f"{port} needs exactly one link partner"


# Research R18: each host's address on its segment, and its gateway there.
HOST_ADDRESSING = {
    "ot-host": ("10.40.20.10/24", "10.40.20.1"),
    "office-host": ("10.40.10.10/24", "10.40.10.1"),
    "historian": ("10.50.10.10/24", "10.50.10.1"),
    "wan-host": ("10.50.99.10/24", "10.50.99.1"),
}


@pytest.mark.parametrize(("name", "addressing"), sorted(HOST_ADDRESSING.items()))
def test_every_host_sets_its_address_and_default_route(nodes: dict, name: str, addressing: tuple[str, str]) -> None:
    """Hosts are not intent devices, so containerlab configures them (R18)."""
    address, gateway = addressing
    lines = nodes[name].get("exec", [])
    assert f"ip addr add {address} dev eth1" in lines, name
    assert f"ip route replace default via {gateway} dev eth1" in lines, name
