"""Unit tests for the dcfabric containerlab topology (Issues #90, #96).

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

Issue #96 added the cross-file guard at the bottom: the static pins here and
the ``lab_node_name`` dial targets in the dcfabric seed are one logical
address registry split across two files, and they must not drift apart.
"""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

TOPOLOGY_PATH = Path(__file__).parents[3] / "containerlab" / "dcfabric.yml"
SEED_PATH = Path(__file__).parents[3] / "packages" / "intent" / "snapl_intent" / "seed" / "dcfabric" / "topology.yml"

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


# --------------------------------------------------------------------------
# Issue #96 — the seed's dial targets must match the lab's static pins.
#
# ``lab_node_name`` is the gNMI dial target (executor.py / collector.py resolve
# it first, ahead of management_address). Seeding a container hostname there
# makes every apply and collect fail from the host, where clab hostnames do not
# resolve; the pins from #90 are stable across redeploys and routable, so the
# seed carries them instead.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def seeded_dial_targets() -> dict[str, str]:
    seed = yaml.safe_load(SEED_PATH.read_text())
    return {device["name"]: device["lab_node_name"] for device in seed["devices"]}


def test_seed_covers_every_fabric_node(seeded_dial_targets: dict[str, str]) -> None:
    assert set(seeded_dial_targets) == set(FABRIC_NODES)


def test_seed_dial_targets_match_the_static_pins(nodes: dict, seeded_dial_targets: dict[str, str]) -> None:
    """The two files are one address registry — they must agree node for node."""
    for name in FABRIC_NODES:
        assert seeded_dial_targets[name] == nodes[name]["mgmt-ipv4"], (
            f"{name}: seed dials {seeded_dial_targets[name]!r} but the lab pins {nodes[name]['mgmt-ipv4']!r}"
        )


def test_seed_dial_targets_are_addresses_not_hostnames(seeded_dial_targets: dict[str, str]) -> None:
    """A clab hostname here is the #96 regression: unresolvable from the host."""
    for name, target in seeded_dial_targets.items():
        assert IPv4Address(target) in SNAPL_SUBNET, f"{name}: {target!r} is not an address in {SNAPL_SUBNET}"
