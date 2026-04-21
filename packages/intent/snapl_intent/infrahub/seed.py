"""Dependency-ordered upsert of seed data into Infrahub.

Seed data is declarative YAML matching the shape of the provisioned schema.
Records are processed in ``SEED_ORDER`` so that each entity's dependencies
(organization, platform, device type, ASN, ...) exist before the entity itself
is written. Upsert is implemented by looking up the existing node via its
natural key (``name`` for most kinds) and saving it in place when present.

Relationship resolution
-----------------------
Sections can declare ``relationships`` on their ``_Section`` definition. For
each declared relationship, ``_upsert_item`` looks up the peer by its natural
key (e.g. ``manufacturer: "Nokia"`` → ``OrganizationManufacturer`` with
``name__value="Nokia"``) and substitutes the peer id into the payload before
calling ``client.create``. An unresolvable peer raises
:class:`IntentValidationError` so seed files fail fast with a clear message.

Deferred scope (see ``SEED_DEFERRED`` below)
--------------------------------------------
Sections still parked in ``SEED_DEFERRED`` need extra wiring the ingester
does not yet provide — IP-namespace bootstrap (``vrfs`` / ``ip_prefixes``),
parent-pointer materialization (``interfaces``), or RoutingProtocol shadow
copies (``bgp_peer_groups`` / ``bgp_sessions``). See
``specs/001-naf-intent-sot/tasks.md`` T028-followup for the remaining
milestones. The full topology YAML is authoritative; only the loader is
partial.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from snapl_intent.exceptions import (
    IntentConnectionError,
    IntentValidationError,
)
from snapl_intent.models import SeedResult

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

# Dependency order: supporting entities before the devices that reference them.
# Sections with relationship-only peers (manufacturer, organization, etc.)
# must appear after those peer sections.
SEED_ORDER: list[str] = [
    "organization",
    "location",
    "manufacturer",
    "platform",
    "device_types",
    "autonomous_systems",
    "devices",
]

# Sections present in topology YAML but not yet loaded — each requires extra
# scaffolding the ingester does not yet provide (IP-namespace bootstrap,
# parent-pointer wiring, RoutingProtocol shadow copies).
SEED_DEFERRED: list[str] = [
    "vrfs",                # needs ip_namespace bootstrap
    "ip_prefixes",         # needs ip_namespace bootstrap
    "interfaces",          # needs device Parent materialization + ip_address
    "bgp_peer_groups",     # inherits RoutingProtocol: needs device + vrf shadow copies
    "bgp_sessions",        # inherits RoutingProtocol: needs device + vrf shadow copies
]


DEVICE_REQUIRED_FIELDS: tuple[str, ...] = (
    "name",
    "role",
    "use_case",
    "device_type",
    "location",
)


@dataclass(frozen=True)
class _Rel:
    """Descriptor for a relationship field resolved by natural key.

    ``peer_kind`` is the Infrahub kind of the peer node; ``lookup_attr`` is the
    peer's attribute used as the natural key (``name`` for most kinds,
    ``shortname`` for locations, ``asn`` for autonomous systems).
    """

    peer_kind: str
    lookup_attr: str = "name"


@dataclass(frozen=True)
class _Section:
    kind: str  # Infrahub node kind
    lookup: tuple[str, ...]  # Fields used as natural key for upsert
    list_valued: bool  # Whether the section holds a list (vs. a single dict)
    relationships: Mapping[str, _Rel] = field(default_factory=dict)


_SECTIONS: dict[str, _Section] = {
    "organization": _Section("OrganizationProvider", ("name",), False),
    "manufacturer": _Section("OrganizationManufacturer", ("name",), False),
    "platform": _Section("DcimPlatform", ("name",), False),
    "location": _Section("LocationSite", ("shortname",), False),
    "device_types": _Section(
        "DcimDeviceType",
        ("name",),
        True,
        relationships={"manufacturer": _Rel("OrganizationManufacturer")},
    ),
    "autonomous_systems": _Section(
        "RoutingAutonomousSystem",
        ("name",),
        True,
        relationships={"organization": _Rel("OrganizationProvider")},
    ),
    "vrfs": _Section("IpamVRF", ("name",), True),
    "ip_prefixes": _Section("IpamPrefix", ("prefix",), True),
    "devices": _Section(
        "DcimDevice",
        ("name",),
        True,
        relationships={
            "device_type": _Rel("DcimDeviceType"),
            "platform": _Rel("DcimPlatform"),
            "location": _Rel("LocationSite", "shortname"),
            "asn": _Rel("RoutingAutonomousSystem", "asn"),
        },
    ),
    "interfaces": _Section("InterfacePhysical", ("device", "name"), True),
    "bgp_peer_groups": _Section("RoutingBGPPeerGroup", ("name",), True),
    "bgp_sessions": _Section(
        "RoutingBGPSession",
        ("local_device", "remote_device"),
        True,
    ),
}


def load_seed_file(path: Path) -> dict[str, Any]:
    """Parse a seed YAML file into a plain dict.

    Empty files become an empty dict. Malformed YAML is translated into
    :class:`IntentValidationError` — parsing is part of data validation from
    the caller's perspective.
    """
    text = path.read_text()
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise IntentValidationError(f"Malformed seed YAML {path}: {exc}") from exc
    return data or {}


class SeedIngester:
    """Upsert seed data section-by-section, respecting ``SEED_ORDER``."""

    def __init__(self, *, client: Any) -> None:
        self._client = client

    async def seed(
        self,
        *,
        use_case: str,
        data_path: Path,
        branch: str = "main",
    ) -> SeedResult:
        dataset = load_seed_file(data_path)
        self._validate(dataset)

        devices_created = 0
        devices_updated = 0
        total_records = 0

        try:
            for section_name in SEED_ORDER:
                if section_name not in dataset:
                    continue
                section = _SECTIONS[section_name]
                items = self._items_for_section(section, dataset[section_name])
                for item in items:
                    created = await self._upsert_item(
                        section=section,
                        item=item,
                        branch=branch,
                    )
                    total_records += 1
                    if section_name == "devices":
                        if created:
                            devices_created += 1
                        else:
                            devices_updated += 1
        except (OSError, TimeoutError) as exc:
            raise IntentConnectionError(f"Infrahub unreachable: {exc}") from exc

        return SeedResult(
            use_case=use_case,
            devices_created=devices_created,
            devices_updated=devices_updated,
            total_records=total_records,
            branch=branch,
        )

    @staticmethod
    def _validate(dataset: dict[str, Any]) -> None:
        for device in dataset.get("devices") or []:
            missing = [f for f in DEVICE_REQUIRED_FIELDS if f not in device]
            if missing:
                raise IntentValidationError(
                    f"Device {device.get('name', '<unknown>')} missing required fields: "
                    + ", ".join(missing),
                    field=missing[0],
                )

    @staticmethod
    def _items_for_section(section: _Section, value: Any) -> list[dict[str, Any]]:
        if section.list_valued:
            return list(value or [])
        # Dict-valued section (organization, platform, ...).
        return [value] if value else []

    async def _upsert_item(
        self,
        *,
        section: _Section,
        item: dict[str, Any],
        branch: str,
    ) -> bool:
        """Upsert one item. Returns ``True`` when a new node was created."""
        payload = await self._resolve_relationships(section, item)

        filters = {f"{key}__value": payload[key] for key in section.lookup if key in payload}
        existing = await self._client.filters(kind=section.kind, **filters)
        if existing:
            node = existing[0]
            for attr, value in payload.items():
                attr_obj = getattr(node, attr, None)
                # TODO(T028-followup): relationship updates don't propagate here.
                # The ``hasattr(attr_obj, "value")`` guard silently skips rel
                # attributes, so changes to device_type/location/... on an
                # existing node aren't written back. Safe for now because the
                # seed is idempotent on natural keys, not on relationship drift.
                if attr_obj is not None and hasattr(attr_obj, "value"):
                    with contextlib.suppress(AttributeError):
                        attr_obj.value = value
            await node.save()
            return False

        node = await self._client.create(kind=section.kind, data=payload, branch=branch)
        await node.save()
        return True

    async def _resolve_relationships(
        self, section: _Section, item: dict[str, Any]
    ) -> dict[str, Any]:
        """Return a copy of ``item`` with relationship fields rewritten to peer ids.

        Looks up each declared relationship peer via its natural key. Raises
        :class:`IntentValidationError` if a referenced peer does not exist —
        seeds are declarative, so an unresolvable reference is a data error,
        not a transient failure.
        """
        if not section.relationships:
            return dict(item)

        resolved = dict(item)
        for field_name, rel in section.relationships.items():
            if field_name not in resolved:
                continue
            value = resolved[field_name]
            filter_kwargs = {f"{rel.lookup_attr}__value": value}
            peers = await self._client.filters(kind=rel.peer_kind, **filter_kwargs)
            if not peers:
                raise IntentValidationError(
                    f"Unresolved {field_name}={value!r} for {section.kind}: "
                    f"no {rel.peer_kind} matching {rel.lookup_attr}__value={value!r}",
                    field=field_name,
                )
            resolved[field_name] = peers[0].id
        return resolved
