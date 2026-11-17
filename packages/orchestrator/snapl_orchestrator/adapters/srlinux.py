"""Translate raw SR Linux gNMI GET state into the Observability diff contract (#32).

The structural diff (`snapl_observability.structural.diff`) expects ``actual_data``
keyed by **per-entity paths** with **flat snake_case field dicts**:

- ``/interface[name=ethernet-1/1]``
  → ``{"description", "ip_address", "prefix_length", "ipv4_addresses", "enabled", "mtu"}``
- ``/network-instance[name=default]/protocols/bgp/neighbor[peer-address=10.10.1.1]``
  → ``{"peer_address", "peer_asn", "peer_group", "enabled"}``

The Collector returns the opposite: raw SR Linux ``json_ietf`` values keyed by the
requested container paths (``/interface`` → ``{"srl_nokia-interfaces:interface": [...]}``),
with nested kebab-case fields (``admin-state``, ``subinterface[].ipv4.address[].ip-prefix``).
This module is the pure, dependency-free translation between the two — the piece the
diff docstring says "is the caller's responsibility (typically the Orchestrator)".
"""

from __future__ import annotations

from typing import Any

# The container paths the Collector must fetch for drift detection. Both the
# deploy verification step and ScanDrift request exactly these so the adapter
# always receives the shapes it knows how to expand.
DRIFT_PATHS: tuple[str, ...] = (
    "/interface",
    "/network-instance[name=default]/protocols/bgp",
)

_BGP_PATH = "/network-instance[name=default]/protocols/bgp"


def _local_name(key: str) -> str:
    """Strip a YANG ``module:`` prefix from a json_ietf key (``a:b`` → ``b``)."""
    return key.split(":", 1)[1] if ":" in key else key


def _get_local(container: Any, local: str, default: Any = None) -> Any:
    """Look up a key by its local name, ignoring any module prefix."""
    if not isinstance(container, dict):
        return default
    for key, value in container.items():
        if _local_name(key) == local:
            return value
    return default


def _enabled(node: dict[str, Any]) -> bool:
    """SR Linux ``admin-state`` (``enable``/``disable``) → bool."""
    return bool(_get_local(node, "admin-state") == "enable")


def _interface_addresses(iface: dict[str, Any]) -> list[dict[str, str]]:
    """Every configured IPv4 address on an interface, in the device's order (#193).

    Each is ``{"prefix": "10.10.1.0/31", "path": "subinterface[index=0]/ipv4/address[ip-prefix=10.10.1.0/31]"}``,
    with ``path`` relative to the interface, so the diff can name an address
    intent never asked for.
    """
    addresses: list[dict[str, str]] = []
    for sub in _get_local(iface, "subinterface", []) or []:
        index = _get_local(sub, "index")
        ipv4 = _get_local(sub, "ipv4")
        for addr in _get_local(ipv4, "address", []) or []:
            prefix = _get_local(addr, "ip-prefix")
            if prefix:
                addresses.append(
                    {
                        "prefix": str(prefix),
                        "path": f"subinterface[index={index}]/ipv4/address[ip-prefix={prefix}]",
                    }
                )
    return addresses


def _normalize_interface(iface: dict[str, Any]) -> dict[str, Any]:
    addresses = _interface_addresses(iface)
    ip_address: str | None = None
    prefix_length: int | None = None
    if addresses:
        # The single-address fields carry the first address. The diff matches
        # intent's one address against the whole list (`ipv4_addresses`).
        address, _, length = addresses[0]["prefix"].partition("/")
        ip_address = address or None
        prefix_length = int(length) if length.isdigit() else None
    return {
        "description": _get_local(iface, "description"),
        "ip_address": ip_address,
        "prefix_length": prefix_length,
        "ipv4_addresses": addresses,
        "enabled": _enabled(iface),
        "mtu": _get_local(iface, "mtu"),
    }


def _normalize_neighbor(neighbor: dict[str, Any]) -> dict[str, Any]:
    return {
        "peer_address": _get_local(neighbor, "peer-address"),
        "peer_asn": _get_local(neighbor, "peer-as"),
        "peer_group": _get_local(neighbor, "peer-group"),
        "enabled": _enabled(neighbor),
    }


def normalize_srlinux_state(data: dict[str, Any]) -> dict[str, Any]:
    """Translate collector output into the structural-diff ``actual_data`` shape.

    ``data`` is keyed by the requested gNMI paths (see ``DRIFT_PATHS``). Returns a
    dict keyed by per-entity paths with flat snake_case field dicts. Entities the
    device does not report simply do not appear — the diff treats a missing key as
    ``actual=None`` per field.
    """
    out: dict[str, Any] = {}

    interface_container = data.get("/interface")
    for iface in _get_local(interface_container, "interface", []) or []:
        name = _get_local(iface, "name")
        if name is not None:
            out[f"/interface[name={name}]"] = _normalize_interface(iface)

    bgp = data.get(_BGP_PATH)
    for neighbor in _get_local(bgp, "neighbor", []) or []:
        peer = _get_local(neighbor, "peer-address")
        if peer is not None:
            out[f"{_BGP_PATH}/neighbor[peer-address={peer}]"] = _normalize_neighbor(neighbor)

    return out
