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
    },
    "bgp_session": {
        "fields": ["peer_address", "peer_asn", "peer_group", "enabled"],
        "path_template": "/network-instance[name=default]/protocols/bgp/neighbor[peer-address={peer_address}]",
        "key_field": "peer_address",
    },
    "device": {
        "fields": ["description"],
        "path_template": "/system",
        "key_field": None,
    },
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
    for iface in desired.interfaces:
        items.extend(_diff_entity(iface, iface_spec, actual_data, kind="interface"))

    # bgp sessions
    bgp_spec = ENTITY_FIELD_MAP["bgp_session"]
    for session in desired.bgp_sessions:
        items.extend(_diff_entity(session, bgp_spec, actual_data, kind="bgp_session"))

    # device
    dev_spec = ENTITY_FIELD_MAP["device"]
    items.extend(_diff_entity(desired.device, dev_spec, actual_data, kind="device"))

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

    if key_field is None:
        path = template
    else:
        key_value = getattr(entity, key_field)
        path = template.format(**{key_field: key_value})

    actual_entry = actual_data.get(path)

    items: list[DriftItem] = []
    for field in fields:
        desired_value = getattr(entity, field, None)
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
