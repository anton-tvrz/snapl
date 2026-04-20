"""Dependency-ordered upsert of seed data into Infrahub.

Seed data is declarative YAML matching the shape of the provisioned schema.
Records are processed in ``SEED_ORDER`` so that each entity's dependencies
(organization, platform, device type, ASN, ...) exist before the entity itself
is written. Upsert is implemented by looking up the existing node via its
natural key (``name`` for most kinds) and saving it in place when present.

Deferred scope (see ``SEED_DEFERRED`` below)
--------------------------------------------
The current ingester treats every YAML field as an attribute and passes it
straight to ``client.create(data=...)``. That is sufficient for nodes whose
only mandatory fields are attributes (organization, manufacturer, location,
platform), but it cannot resolve *relationships* — e.g. a ``DcimDeviceType``
needs ``manufacturer`` resolved to a node reference, a ``DcimDevice`` needs
``device_type`` / ``location`` / ``platform`` / ``asn``, interfaces need
``device``, and BGP peer groups / sessions need ``device`` + ``vrf``.

Wiring those up requires a relationship-resolution layer on top of the
current section framework. That work is tracked separately — see
``specs/001-naf-intent-sot/tasks.md`` T028-followup. Until it lands, the
extra sections sit in ``SEED_DEFERRED`` so the full topology YAML remains
authoritative (the data is real; only the loader is partial).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from snapl_intent.exceptions import (
    IntentConnectionError,
    IntentValidationError,
)
from snapl_intent.models import SeedResult

if TYPE_CHECKING:
    from pathlib import Path

# Dependency order: supporting entities before the devices that reference them.
# Only attribute-only sections are loaded today; everything that needs
# relationship resolution is deferred (see module docstring + SEED_DEFERRED).
SEED_ORDER: list[str] = [
    "organization",
    "location",
    "manufacturer",
    "platform",
]

# Sections present in topology YAML but not yet loaded — each requires
# resolving at least one mandatory relationship that the current ingester
# cannot handle. Kept here so the full ordered list remains visible.
SEED_DEFERRED: list[str] = [
    "device_types",        # needs manufacturer
    "autonomous_systems",  # needs organization
    "vrfs",                # needs ip_namespace
    "ip_prefixes",         # needs ip_namespace
    "devices",             # needs device_type, location, platform, asn
    "interfaces",          # needs device (Parent)
    "bgp_peer_groups",     # inherits RoutingProtocol: needs device + vrf
    "bgp_sessions",        # inherits RoutingProtocol: needs device + vrf
]


DEVICE_REQUIRED_FIELDS: tuple[str, ...] = ("name", "role", "use_case", "device_type")


@dataclass(frozen=True)
class _Section:
    kind: str  # Infrahub node kind
    lookup: tuple[str, ...]  # Fields used as natural key for upsert
    list_valued: bool  # Whether the section holds a list (vs. a single dict)


_SECTIONS: dict[str, _Section] = {
    "organization": _Section("OrganizationProvider", ("name",), False),
    "manufacturer": _Section("OrganizationManufacturer", ("name",), False),
    "platform": _Section("DcimPlatform", ("name",), False),
    "location": _Section("LocationSite", ("shortname",), False),
    "device_types": _Section("DcimDeviceType", ("name",), True),
    "autonomous_systems": _Section("RoutingAutonomousSystem", ("name",), True),
    "vrfs": _Section("IpamVRF", ("name",), True),
    "ip_prefixes": _Section("IpamPrefix", ("prefix",), True),
    "devices": _Section("DcimDevice", ("name",), True),
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
        filters = {f"{key}__value": item[key] for key in section.lookup if key in item}
        existing = await self._client.filters(kind=section.kind, **filters)
        if existing:
            node = existing[0]
            for attr, value in item.items():
                attr_obj = getattr(node, attr, None)
                if attr_obj is not None and hasattr(attr_obj, "value"):
                    with contextlib.suppress(AttributeError):
                        attr_obj.value = value
            await node.save()
            return False

        node = await self._client.create(kind=section.kind, data=item, branch=branch)
        await node.save()
        return True
