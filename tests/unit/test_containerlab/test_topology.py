"""Unit tests for the dcfabric containerlab topology (Issue #90).

The lab must own its own management network so it never shares the default
``clab`` / 172.20.20.0/24 bridge with another project's lab on the same host.
Docker assigns addresses dynamically from the bottom of a shared subnet, so
two labs on the same bridge interleave and one project's SoT address can
resolve to the other's SR Linux nodes (same default credentials, plaintext
gNMI). A dedicated bridge + static pins gives each lab a non-overlapping,
deterministic address block, so every address belongs to exactly one lab.
(On OrbStack the bridges remain mutually routable — this is address
isolation, not L3 isolation.)

Subnet registry (one /24 per lab):
    172.20.20.0/24  project-network-synapse-quattro  spine-leaf-lab
    172.20.21.0/24  snapl                            dcfabric  (this lab)
"""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

TOPOLOGY_PATH = Path(__file__).parents[3] / "containerlab" / "dcfabric.yml"

SNAPL_SUBNET = IPv4Network("172.20.21.0/24")
DEFAULT_SHARED_SUBNET = IPv4Network("172.20.20.0/24")

FABRIC_NODES = ("spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04")


@pytest.fixture(scope="module")
def topology() -> dict:
    return yaml.safe_load(TOPOLOGY_PATH.read_text())


@pytest.fixture(scope="module")
def nodes(topology: dict) -> dict:
    return topology["topology"]["nodes"]


def test_declares_a_dedicated_mgmt_network(topology: dict) -> None:
    """A named network + subnet — not the inherited global default."""
    mgmt = topology["mgmt"]
    assert mgmt["network"] == "clab-snapl"
    assert mgmt["ipv4-subnet"] == str(SNAPL_SUBNET)


def test_mgmt_network_is_not_the_shared_default(topology: dict) -> None:
    """The whole point of the issue: no overlap with quattro's default."""
    subnet = IPv4Network(topology["mgmt"]["ipv4-subnet"])
    assert not subnet.overlaps(DEFAULT_SHARED_SUBNET)


def test_every_fabric_node_has_a_static_pin(nodes: dict) -> None:
    for name in FABRIC_NODES:
        assert "mgmt-ipv4" in nodes[name], f"{name} has no static mgmt-ipv4"


def test_pins_are_inside_the_snapl_subnet(nodes: dict) -> None:
    for name in FABRIC_NODES:
        address = IPv4Address(nodes[name]["mgmt-ipv4"])
        assert address in SNAPL_SUBNET, f"{name}: {address} outside {SNAPL_SUBNET}"


def test_pins_are_unique(nodes: dict) -> None:
    pins = [nodes[name]["mgmt-ipv4"] for name in FABRIC_NODES]
    assert len(pins) == len(set(pins))
