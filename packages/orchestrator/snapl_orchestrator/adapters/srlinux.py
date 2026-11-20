"""Translate raw SR Linux gNMI GET state into the Observability diff contract (#32).

The structural diff (`snapl_observability.structural.diff`) expects ``actual_data``
keyed by **per-entity paths** with **flat snake_case field dicts**:

- ``/interface[name=ethernet-1/1]``
  → ``{"description", "ip_address", "prefix_length", "ipv4_addresses", "enabled", "mtu"}``
- ``/network-instance[name=default]/protocols/bgp/neighbor[peer-address=10.10.1.1]``
  → ``{"peer_address", "peer_asn", "peer_group", "enabled", "export_policy", "import_policy"}``
- ``/routing-policy/policy[name=underlay-loopbacks]``
  → ``{"name", "accept_prefixes", "default_action"}`` (#317)

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
    "/network-instance[name=default]/interface",
    "/routing-policy",
)

_BGP_PATH = "/network-instance[name=default]/protocols/bgp"
_INSTANCE = "/network-instance[name=default]"
# SR Linux (v26.3.1) answers a GET of the instance's interface list keyed by the
# instance entry, not by the path asked for (measured, #315). Both are read.
_BINDING_KEYS = (_INSTANCE, f"{_INSTANCE}/interface")


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
        "export_policy": _policy_names(neighbor, "export-policy"),
        "import_policy": _policy_names(neighbor, "import-policy"),
    }


def _policy_names(neighbor: dict[str, Any], leaf: str) -> str | None:
    """A neighbour's ``export-policy``/``import-policy`` leaf-list as intent writes it.

    Intent names one policy, so one name is that name and none is None. More
    than one is joined, which never equals a single name, so it shows as drift.
    """
    names = _get_local(neighbor, leaf) or []
    if isinstance(names, str):
        names = [names]
    return ", ".join(names) if names else None


def _prefix_sets(container: Any) -> dict[str, list[str]]:
    """Each prefix-set's entries in the model's form, ``"<prefix> <range>"``."""
    sets: dict[str, list[str]] = {}
    for prefix_set in _get_local(container, "prefix-set", []) or []:
        entries = [
            f"{_get_local(entry, 'ip-prefix')} {_get_local(entry, 'mask-length-range') or 'exact'}"
            for entry in _get_local(prefix_set, "prefix", []) or []
        ]
        sets[_get_local(prefix_set, "name")] = entries
    return sets


def _set_leaves(node: Any, path: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    """The leaves a match container sets, as paths, leaving out the defaults.

    A GET with datatype all fills every match container in with
    ``match-set-options: any`` (measured on v26.3.1, #317); that sets no condition.
    """
    if not isinstance(node, dict):
        return {path}
    leaves: set[tuple[str, ...]] = set()
    for key, value in node.items():
        local = _local_name(key)
        if local != "match-set-options":
            leaves |= _set_leaves(value, (*path, local))
    return leaves


def _normalize_policy(policy: dict[str, Any], sets: dict[str, list[str]]) -> dict[str, Any]:
    """A policy as intent describes one: what it accepts, and what meets the rest.

    Intent writes one shape of statement, accept a prefix-set. A statement of any
    other shape changes what the policy does, so it is listed as
    ``"statement <name>"`` rather than dropped, which makes it drift.
    """
    accepted: list[str] = []
    for statement in _get_local(policy, "statement", []) or []:
        match = _get_local(statement, "match") or {}
        result = _get_local(_get_local(statement, "action"), "policy-result")
        prefix_set = _get_local(_get_local(match, "prefix"), "prefix-set")
        only_a_prefix_set = _set_leaves(match) == {("prefix", "prefix-set")}
        if result == "accept" and only_a_prefix_set:
            accepted.extend(sets.get(prefix_set, []))
        else:
            accepted.append(f"statement {_get_local(statement, 'name')}")
    return {
        "name": _get_local(policy, "name"),
        "accept_prefixes": sorted(set(accepted)),
        "default_action": _get_local(_get_local(policy, "default-action"), "policy-result"),
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

    for key in _BINDING_KEYS:
        for binding in _get_local(data.get(key), "interface", []) or []:
            name = _get_local(binding, "name")
            if name is not None:
                out[f"{_INSTANCE}/interface[name={name}]"] = {"name": name}

    container = data.get("/routing-policy")
    policies = _get_local(container, "routing-policy", container)
    sets = _prefix_sets(policies)
    for policy in _get_local(policies, "policy", []) or []:
        name = _get_local(policy, "name")
        if name is not None:
            out[f"/routing-policy/policy[name={name}]"] = _normalize_policy(policy, sets)

    return out
