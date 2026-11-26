"""Pure-function structural diff between desired and actual state.

The single point of vendor coupling in the Observability block — adding a new
intent entity (e.g., OSPFNeighbor) means extending ENTITY_FIELD_MAP and nothing
else changes.

The actual_data dict is expected to be keyed by YANG-style path strings, with
each value a flat dict whose keys match the snake_case field names on the
intent models. Translating gNMI's nested kebab-case shape to this normalized
flat form is the caller's responsibility (typically the Orchestrator wrapping
the Collector).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any

from snapl_observability.models import DriftItem

if TYPE_CHECKING:
    from snapl_intent.models import DesiredState


# ---------------------------------------------------------------------------
# Per-entity comparison map
# ---------------------------------------------------------------------------


ENTITY_FIELD_MAP: dict[str, dict[str, Any]] = {
    "interface": {
        "fields": ["description", "ip_address", "prefix_length", "enabled", "mtu"],
        "path_template": "/interface[name={name}]",
        "key_field": "name",
        # Fields whose presence means somebody deliberately configured this
        # entity, used to tell real config sprawl from hardware that merely
        # exists. A chassis port nobody touched is reported by SR Linux with
        # every one of these unset — a spine reports ~30 of them — so without
        # this test the reverse diff would bury every scan in inventory.
        # `enabled` counts too (#175, spec 007 FR-003 as amended): a port with no
        # config entry reports admin-state disable, and an entry defaults to
        # enable the moment it exists, so an enabled port is one somebody made.
        "value_fields": ("description", "ip_address", "prefix_length", "mtu", "enabled"),
        # Values the device reports on its own, with nobody having set them. A
        # default is not intent, so it neither marks a port as configured nor
        # appears as an undesired item. SR Linux (v26.3.1) adds mtu 9232 to a port
        # given only a description (#122), and reports every port nobody created
        # as disabled (#175). Only the reverse diff consults this — a desired
        # value is always compared strictly.
        "device_defaults": {"mtu": 9232, "enabled": False},
        # Never reported as undesired, and never deleted once removal lands
        # (#65). These carry real config and are indistinguishable by shape
        # from something an operator added by hand — mgmt0 holds the very
        # address snapl dials over, so removing it strands the device for
        # good. They can only be excluded by name.
        "protected": ("mgmt0", "system0"),
        # Fields the renderer omits when unset in intent ("not managed"), where
        # the device nonetheless reports an operational value. Comparing them
        # against a None desired would phantom-drift on every scan: an mtu-less
        # ethernet interface renders no mtu but the device reports e.g. 9232
        # (#84). Scoped per-field rather than a blanket None rule so a set field
        # (e.g. description) still drifts once merge-only-apply deletion lands
        # (#65). Only applies when desired is None — a set value is always
        # compared strictly.
        "skip_when_none": ("mtu",),
    },
    "bgp_session": {
        # The policies decide what the session carries; without them SR Linux's
        # eBGP default sends and accepts nothing (#317).
        "fields": ["peer_address", "peer_asn", "peer_group", "enabled", "export_policy", "import_policy"],
        "path_template": "/network-instance[name=default]/protocols/bgp/neighbor[peer-address={peer_address}]",
        "key_field": "peer_address",
        # Unlike an interface, a neighbour entry only exists because somebody
        # created it — there is no hardware equivalent of a bare chassis port
        # here. peer_address is the key rather than a value, so the ASN and
        # the peer group are what mark the entry as configured.
        "value_fields": ("peer_asn", "peer_group"),
        "protected": (),
    },
    # A subinterface's membership of the default network instance, without
    # which its address is not routed (#315). Intent has no entity for it: every
    # interface with an address implies one, on subinterface 0, as the
    # executor's templates render it (see ``_routed_interfaces``).
    "routed_interface": {
        "fields": ["name"],
        "path_template": "/network-instance[name=default]/interface[name={name}]",
        "key_field": "name",
        "value_fields": ("name",),
        "protected": (),
    },
    # A routing policy a session names (#317), as the adapter folds SR Linux's
    # policy and its prefix-set into one view: what it accepts, and what meets
    # the rest. A policy only exists because somebody wrote it, so any on the
    # device that intent does not hold is undesired.
    "routing_policy": {
        "fields": ["accept_prefixes", "default_action"],
        "path_template": "/routing-policy/policy[name={name}]",
        "key_field": "name",
        "value_fields": ("accept_prefixes", "default_action"),
        # A policy that lists nothing has nothing to report under accept_prefixes;
        # its default_action already marks it as somebody's.
        "device_defaults": {"accept_prefixes": []},
        "protected": (),
    },
    # No device-level entry: the executor manages no /system fields, and a
    # compared-but-never-rendered field is permanent phantom drift (#59).
    # Every field listed here must be rendered by the executor's templates —
    # enforced by tests/unit/test_orchestrator/test_render_diff_contract.py.
}


# ---------------------------------------------------------------------------
# Core diff function
# ---------------------------------------------------------------------------


def diff_desired_vs_actual(
    desired: DesiredState,
    actual_data: dict[str, Any],
) -> list[DriftItem]:
    """Compare a DesiredState against actual collected data.

    Returns a list of DriftItem entries — one per attribute where the desired
    value differs from the observed value. Returns an empty list when every
    enumerated field matches.

    Missing entries in actual_data produce one DriftItem per compared field
    (with actual=None) so the report reflects the absence of expected state.
    """
    items: list[DriftItem] = []

    # interfaces
    iface_spec = ENTITY_FIELD_MAP["interface"]
    extra_addresses: list[DriftItem] = []
    for iface in desired.interfaces:
        path = iface_spec["path_template"].format(name=iface.name)
        entry, extras = _address_view(iface, actual_data.get(path))
        items.extend(_diff_entity(iface, iface_spec, {path: entry}, kind="interface"))
        extra_addresses.extend(
            DriftItem(
                path=f"{path}/{address['path']}",
                desired=None,
                actual=address["prefix"],
                entity_kind="interface",
                undesired=True,
            )
            for address in extras
        )

    # bgp sessions
    bgp_spec = ENTITY_FIELD_MAP["bgp_session"]
    for session in desired.bgp_sessions:
        items.extend(_diff_entity(session, bgp_spec, actual_data, kind="bgp_session"))

    # subinterfaces in the default network instance (#315)
    routed_spec = ENTITY_FIELD_MAP["routed_interface"]
    routed = _routed_interfaces(desired)
    for binding in routed:
        items.extend(_diff_entity(binding, routed_spec, actual_data, kind="routed_interface"))

    # routing policies the sessions name (#317)
    policy_spec = ENTITY_FIELD_MAP["routing_policy"]
    for policy in desired.routing_policies:
        items.extend(_diff_entity(policy, policy_spec, actual_data, kind="routing_policy"))

    # The reverse direction: entities the device carries that intent never
    # asked for (#54). Runs after the desired-driven pass so a report reads
    # "what is missing or wrong" before "what should not be here".
    items.extend(extra_addresses)
    items.extend(
        _undesired_entities(
            iface_spec,
            actual_data,
            kind="interface",
            desired_keys={iface.name for iface in desired.interfaces},
        )
    )
    items.extend(
        _undesired_entities(
            bgp_spec,
            actual_data,
            kind="bgp_session",
            desired_keys={session.peer_address for session in desired.bgp_sessions},
        )
    )
    items.extend(
        _undesired_entities(
            routed_spec,
            actual_data,
            kind="routed_interface",
            desired_keys={binding.name for binding in routed},
        )
    )
    items.extend(
        _undesired_entities(
            policy_spec,
            actual_data,
            kind="routing_policy",
            desired_keys={policy.name for policy in desired.routing_policies},
        )
    )

    return items


@dataclass(frozen=True)
class _RoutedInterface:
    name: str


def _routed_interfaces(desired: DesiredState) -> list[_RoutedInterface]:
    """The subinterfaces intent implies in the default instance: one per addressed interface."""
    return [_RoutedInterface(name=f"{iface.name}.0") for iface in desired.interfaces if iface.ip_address]


def _address_view(iface: Any, entry: Any) -> tuple[Any, list[dict[str, str]]]:
    """Match an intent interface's one address against every address on the device (#193).

    Intent gives an interface one address. The device may carry more, in an
    order it chooses, so comparing against "the first" either hides the extras
    or reports the intended address as wrong. When the entry carries
    ``ipv4_addresses`` (``{"prefix", "path"}`` dicts, ``path`` relative to the
    interface), this returns:

    - the entry to compare: if the intended address is on the device, its
      ``ip_address`` and ``prefix_length`` are the intended ones, wherever the
      device lists it. If it is not, the entry is unchanged, so the first
      address is reported against the intended one as before.
    - the addresses left over, which intent never asked for.

    An entry without the list is returned unchanged, with nothing left over.
    """
    addresses = entry.get("ipv4_addresses") if isinstance(entry, dict) else None
    if not isinstance(addresses, list) or not addresses:
        return entry, []

    if iface.ip_address is not None and iface.prefix_length is not None:
        wanted = f"{iface.ip_address}/{iface.prefix_length}"
        for index, address in enumerate(addresses):
            if address.get("prefix") == wanted:
                view = {**entry, "ip_address": iface.ip_address, "prefix_length": iface.prefix_length}
                return view, addresses[:index] + addresses[index + 1 :]

    return entry, addresses[1:]


@cache
def _key_pattern(template: str) -> re.Pattern[str]:
    """Compile a path template into a matcher for its key.

    ``/interface[name={name}]`` becomes a pattern that matches
    ``/interface[name=ethernet-1/7]`` and captures ``ethernet-1/7``. Built by
    splitting on the placeholder rather than escaping around it, because the
    literal parts are full of regex metacharacters (``[``, ``]``, ``/``).
    """
    placeholder_start = template.index("{")
    placeholder_end = template.index("}") + 1
    head = template[:placeholder_start]
    tail = template[placeholder_end:]
    return re.compile(f"^{re.escape(head)}(.+){re.escape(tail)}$")


def _undesired_entities(
    spec: dict[str, Any],
    actual_data: dict[str, Any],
    *,
    kind: str,
    desired_keys: set[str],
) -> list[DriftItem]:
    """Report entities present on the device but absent from intent.

    Only entities carrying a value-bearing field qualify: see ``value_fields``
    on the entity spec for why an unconfigured chassis port must not count.
    Protected entities are skipped outright.
    """
    pattern = _key_pattern(spec["path_template"])
    value_fields: tuple[str, ...] = spec.get("value_fields", ())
    protected: tuple[str, ...] = spec.get("protected", ())
    device_defaults: dict[str, Any] = spec.get("device_defaults", {})

    items: list[DriftItem] = []
    # Sorted so two scans of an unchanged device produce identical reports.
    for path in sorted(actual_data):
        match = pattern.match(path)
        if match is None:
            continue
        key = match.group(1)
        if key in protected or key in desired_keys:
            continue

        entry = actual_data.get(path)
        if not isinstance(entry, dict):
            continue

        set_values = [
            (field, value)
            for field in value_fields
            if (value := entry.get(field)) is not None and value != device_defaults.get(field)
        ]
        if not set_values:
            continue

        items.extend(
            DriftItem(path=f"{path}/{field}", desired=None, actual=value, entity_kind=kind, undesired=True)
            for field, value in set_values
        )
    return items


def _diff_entity(
    entity: Any,
    spec: dict[str, Any],
    actual_data: dict[str, Any],
    *,
    kind: str,
) -> list[DriftItem]:
    """Diff one intent entity against its corresponding actual_data entry."""
    template: str = spec["path_template"]
    key_field: str | None = spec["key_field"]
    fields: list[str] = spec["fields"]
    skip_when_none: tuple[str, ...] = spec.get("skip_when_none", ())

    if key_field is None:
        path = template
    else:
        key_value = getattr(entity, key_field)
        path = template.format(**{key_field: key_value})

    actual_entry = actual_data.get(path)

    items: list[DriftItem] = []
    for field in fields:
        desired_value = getattr(entity, field, None)
        # An unset ("not managed") field the renderer omits must not drift
        # against an operational value the device reports on its own (#84).
        if desired_value is None and field in skip_when_none:
            continue
        actual_value = actual_entry.get(field) if isinstance(actual_entry, dict) else None
        if desired_value == actual_value:
            continue
        items.append(
            DriftItem(
                path=f"{path}/{field}",
                desired=desired_value,
                actual=actual_value,
                entity_kind=kind,
            )
        )
    return items
