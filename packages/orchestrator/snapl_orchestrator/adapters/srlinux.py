"""Translate raw SR Linux gNMI GET state into the Observability diff contract (#32).

The structural diff (`snapl_observability.structural.diff`) expects ``actual_data``
keyed by **per-entity paths** with **flat snake_case field dicts**:

- ``/interface[name=ethernet-1/1]`` → ``{"description", "ip_address", "prefix_length", "enabled", "mtu"}``
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
    return _get_local(node, "admin-state") == "enable"


def _interface_ip(iface: dict[str, Any]) -> tuple[str | None, int | None]:
    """First configured IPv4 address on an interface → (address, prefix_length)."""
    for sub in _get_local(iface, "subinterface", []) or []:
        ipv4 = _get_local(sub, "ipv4")
        for addr in _get_local(ipv4, "address", []) or []:
            prefix = _get_local(addr, "ip-prefix")
            if prefix:
                address, _, length = str(prefix).partition("/")
                return address or None, int(length) if length.isdigit() else None
    return None, None


def _normalize_interface(iface: dict[str, Any]) -> dict[str, Any]:
    ip_address, prefix_length = _interface_ip(iface)
    return {
        "description": _get_local(iface, "description"),
        "ip_address": ip_address,
        "prefix_length": prefix_length,
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
