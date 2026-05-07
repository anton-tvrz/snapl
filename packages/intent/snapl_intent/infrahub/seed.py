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

BGP peer groups and sessions (Milestone D)
------------------------------------------
``RoutingBGPPeerGroup`` and ``RoutingBGPSession`` both inherit from
``RoutingProtocol``, which requires a ``device`` (Parent) and ``vrf``
relationship on every row.  The topology YAML declares one logical peer group
shared by all devices, so the ingester materialises N *shadow copies* — one
``RoutingBGPPeerGroup`` row per (peer_group, local_device) pair — with the
scoped name ``{pg_name}@{device_name}``.  ``RoutingBGPSession`` rows are
keyed by their ``description`` field (unique on ``RoutingProtocol``).
``peer_session`` cross-linking between the two sides of a session is deferred.
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
    "vrfs",
    "ip_prefixes",
    "devices",
    "interfaces",
    "bgp_peer_groups",  # Milestone D — handled by _seed_bgp_peer_groups (shadow copies)
    "bgp_sessions",     # Milestone D — handled by _seed_bgp_sessions
]

# All sections are now active; kept as an empty list for backwards compat with tests.
SEED_DEFERRED: list[str] = []

# Sections that need bespoke BGP logic rather than the generic _upsert_item path.
_BGP_SECTIONS: frozenset[str] = frozenset({"bgp_peer_groups", "bgp_sessions"})


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
    namespace_field: str | None = None  # if set, inject default IpamNamespace id here
    ip_field: str | None = None  # YAML field → IpamIPAddress → wired into ip_addresses list
    primary_address_field: str | None = None  # YAML field → IpamIPAddress → wired into primary_address
    lookup_rel_fields: frozenset[str] = field(default_factory=frozenset)  # lookup fields resolved as rel ids


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
    "vrfs": _Section("IpamVRF", ("name",), True, namespace_field="namespace"),
    "ip_prefixes": _Section("IpamPrefix", ("prefix",), True, namespace_field="ip_namespace"),
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
        primary_address_field="management_ip",
    ),
    "interfaces": _Section(
        "InterfacePhysical",
        ("device", "name"),
        True,
        relationships={"device": _Rel("DcimDevice")},
        ip_field="ip_address",
        lookup_rel_fields=frozenset({"device"}),
    ),
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

        # Fetch the default IP namespace once if any section will materialise IpamIPAddress.
        namespace_id: str | None = None
        _needs_ns = {"vrfs", "ip_prefixes", "interfaces"}.intersection(dataset) or any(
            d.get("management_ip") for d in (dataset.get("devices") or [])
        )
        if _needs_ns:
            namespace_id = await self._get_default_namespace_id()

        devices_created = 0
        devices_updated = 0
        total_records = 0

        try:
            for section_name in SEED_ORDER:
                if section_name not in dataset:
                    continue
                if section_name in _BGP_SECTIONS:
                    continue  # handled below via dedicated BGP methods
                section = _SECTIONS[section_name]
                items = self._items_for_section(section, dataset[section_name])
                for item in items:
                    created = await self._upsert_item(
                        section=section,
                        item=item,
                        branch=branch,
                        namespace_id=namespace_id,
                    )
                    total_records += 1
                    if section_name == "devices":
                        if created:
                            devices_created += 1
                        else:
                            devices_updated += 1

            if any(s in dataset for s in _BGP_SECTIONS):
                vrf_id = await self._get_default_vrf_id()
                pg_id_map: dict[str, dict[str, str]] = {}
                if "bgp_peer_groups" in dataset:
                    pg_id_map, pg_count = await self._seed_bgp_peer_groups(
                        pg_declarations=dataset["bgp_peer_groups"],
                        sessions=dataset.get("bgp_sessions", []),
                        branch=branch,
                        vrf_id=vrf_id,
                    )
                    total_records += pg_count
                if "bgp_sessions" in dataset:
                    session_count = await self._seed_bgp_sessions(
                        sessions=dataset["bgp_sessions"],
                        branch=branch,
                        vrf_id=vrf_id,
                        pg_id_map=pg_id_map,
                    )
                    total_records += session_count
        except (OSError, TimeoutError) as exc:
            raise IntentConnectionError(f"Infrahub unreachable: {exc}") from exc

        return SeedResult(
            use_case=use_case,
            devices_created=devices_created,
            devices_updated=devices_updated,
            total_records=total_records,
            branch=branch,
        )

    async def _materialise_ip_address(
        self, *, address: str, namespace_id: str, branch: str
    ) -> str:
        """Upsert an IpamIPAddress node and return its id."""
        existing = await self._client.filters(
            kind="IpamIPAddress",
            address__value=address,
            ip_namespace__ids=[namespace_id],
        )
        if existing:
            return existing[0].id
        node = await self._client.create(
            kind="IpamIPAddress",
            data={"address": address, "ip_namespace": namespace_id},
            branch=branch,
        )
        await node.save()
        return node.id

    async def _get_default_namespace_id(self) -> str:
        results = await self._client.filters(kind="IpamNamespace", default__value=True)
        if not results:
            raise IntentValidationError(
                "Default IP namespace not found in Infrahub — "
                "ensure Infrahub is fully initialized before seeding"
            )
        return results[0].id

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
        namespace_id: str | None = None,
    ) -> bool:
        """Upsert one item. Returns ``True`` when a new node was created."""
        payload = await self._resolve_relationships(section, item)
        if section.namespace_field and namespace_id:
            payload[section.namespace_field] = namespace_id

        # Materialise per-item IP address into IpamIPAddress, wire to ip_addresses list.
        if section.ip_field and section.ip_field in payload and namespace_id:
            ip_str = payload.pop(section.ip_field)
            ip_id = await self._materialise_ip_address(
                address=ip_str, namespace_id=namespace_id, branch=branch
            )
            payload["ip_addresses"] = [ip_id]

        # Materialise management IP into IpamIPAddress, wire to primary_address.
        if section.primary_address_field and section.primary_address_field in payload and namespace_id:
            ip_id = await self._materialise_ip_address(
                address=payload[section.primary_address_field],
                namespace_id=namespace_id,
                branch=branch,
            )
            payload["primary_address"] = ip_id

        filters: dict[str, Any] = {}
        for key in section.lookup:
            if key not in payload:
                continue
            if key in section.lookup_rel_fields:
                filters[f"{key}__ids"] = [payload[key]]
            else:
                filters[f"{key}__value"] = payload[key]
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

    async def _get_default_vrf_id(self) -> str:
        results = await self._client.filters(kind="IpamVRF", name__value="default")
        if not results:
            raise IntentValidationError(
                "Default VRF not found in Infrahub — "
                "ensure VRFs are seeded before BGP sections"
            )
        return results[0].id

    async def _seed_bgp_peer_groups(
        self,
        *,
        pg_declarations: list[dict[str, Any]],
        sessions: list[dict[str, Any]],
        branch: str,
        vrf_id: str,
    ) -> tuple[dict[str, dict[str, str]], int]:
        """Materialise one BGPPeerGroup shadow per (peer_group, device) pair.

        Returns ``(pg_id_map, count)`` where ``pg_id_map`` is a nested dict
        ``{pg_name: {device_name: shadow_node_id}}`` used by
        ``_seed_bgp_sessions`` to wire the correct shadow into each session.
        ``count`` is the number of shadow rows processed (created or updated).
        """
        # Collect unique local_devices per peer group name from sessions.
        pg_devices: dict[str, set[str]] = {}
        for session in sessions:
            pg_name = session.get("peer_group")
            if pg_name:
                pg_devices.setdefault(pg_name, set()).add(session["local_device"])

        pg_decl_map = {pg["name"]: pg for pg in pg_declarations}
        pg_id_map: dict[str, dict[str, str]] = {}
        count = 0

        for pg_name, pg_decl in pg_decl_map.items():
            for device_name in pg_devices.get(pg_name, set()):
                device_peers = await self._client.filters(kind="DcimDevice", name__value=device_name)
                if not device_peers:
                    raise IntentValidationError(
                        f"BGPPeerGroup shadow: device {device_name!r} not found",
                        field="local_device",
                    )
                device_id = device_peers[0].id

                shadow_name = f"{pg_name}@{device_name}"
                shadow_desc = f"{pg_decl.get('description', pg_name)} ({device_name})"
                payload: dict[str, Any] = {
                    "name": shadow_name,
                    "description": shadow_desc,
                    "status": pg_decl.get("status", "active"),
                    "device": device_id,
                    "vrf": vrf_id,
                }
                for attr in (
                    "address_family", "send_community", "import_policies",
                    "export_policies", "maximum_routes", "local_pref",
                ):
                    if attr in pg_decl:
                        payload[attr] = pg_decl[attr]

                existing = await self._client.filters(kind="RoutingBGPPeerGroup", name__value=shadow_name)
                if existing:
                    node = existing[0]
                    for attr, value in payload.items():
                        attr_obj = getattr(node, attr, None)
                        if attr_obj is not None and hasattr(attr_obj, "value"):
                            with contextlib.suppress(AttributeError):
                                attr_obj.value = value
                    await node.save()
                else:
                    node = await self._client.create(kind="RoutingBGPPeerGroup", data=payload, branch=branch)
                    await node.save()

                pg_id_map.setdefault(pg_name, {})[device_name] = node.id
                count += 1

        return pg_id_map, count

    async def _seed_bgp_sessions(
        self,
        *,
        sessions: list[dict[str, Any]],
        branch: str,
        vrf_id: str,
        pg_id_map: dict[str, dict[str, str]],
    ) -> int:
        """Upsert BGPSession rows, resolving all relationships.

        Upsert key: ``description__value`` (unique on ``RoutingProtocol``).
        ``peer_session`` cross-linking is deferred (T028-followup post-D).

        Returns the count of sessions processed.
        """
        for session in sessions:
            local_device_name = session["local_device"]

            device_peers = await self._client.filters(kind="DcimDevice", name__value=local_device_name)
            if not device_peers:
                raise IntentValidationError(
                    f"BGPSession: local_device {local_device_name!r} not found",
                    field="local_device",
                )
            device_id = device_peers[0].id

            payload: dict[str, Any] = {
                "description": session["description"],
                "status": session.get("status", "active"),
                "device": device_id,
                "vrf": vrf_id,
            }
            for attr in ("session_type", "role"):
                if attr in session:
                    payload[attr] = session[attr]

            for field_name, kind, lookup_attr in (
                ("local_as", "RoutingAutonomousSystem", "asn__value"),
                ("remote_as", "RoutingAutonomousSystem", "asn__value"),
            ):
                if field_name in session:
                    peers = await self._client.filters(kind=kind, **{lookup_attr: session[field_name]})
                    if not peers:
                        raise IntentValidationError(
                            f"BGPSession: {field_name}={session[field_name]!r} not found",
                            field=field_name,
                        )
                    payload[field_name] = peers[0].id

            for field_name in ("local_ip", "remote_ip"):
                if field_name in session:
                    peers = await self._client.filters(
                        kind="IpamIPAddress", address__value=session[field_name]
                    )
                    if peers:
                        payload[field_name] = peers[0].id

            if "peer_group" in session:
                shadow_id = pg_id_map.get(session["peer_group"], {}).get(local_device_name)
                if shadow_id:
                    payload["peer_group"] = shadow_id

            existing = await self._client.filters(
                kind="RoutingBGPSession", description__value=session["description"]
            )
            if existing:
                node = existing[0]
                for attr, value in payload.items():
                    attr_obj = getattr(node, attr, None)
                    if attr_obj is not None and hasattr(attr_obj, "value"):
                        with contextlib.suppress(AttributeError):
                            attr_obj.value = value
                await node.save()
            else:
                node = await self._client.create(kind="RoutingBGPSession", data=payload, branch=branch)
                await node.save()

        return len(sessions)
