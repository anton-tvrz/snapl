"""Infrahub-backed implementation of :class:`IntentStore`.

Phase 3 (US1) ships ``get_desired_state``. Later phases append
``provision_schema``, ``seed``, ``get_schema`` and ``delete_device`` to this
class. Keeping all store methods in one class keeps the boundary between the
Intent contract and the Infrahub SDK in a single place.

The class accepts an injected client in the constructor; production callers
typically create one via :func:`snapl_intent.infrahub.client.build_client` or
:func:`client_session`, while tests pass a mock.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from snapl_intent.abc import IntentStore
from snapl_intent.exceptions import (
    IntentConnectionError,
    IntentDeletionError,
    IntentNotFoundError,
    IntentSchemaError,
)
from snapl_intent.infrahub.schema import SchemaLoader, discover_schema_batches
from snapl_intent.infrahub.seed import SeedIngester
from snapl_intent.models import (
    BGPSession,
    DeleteResult,
    DesiredState,
    Device,
    Interface,
    ProvisionResult,
    Schema,
    SeedResult,
)

# Root of the packaged schema YAML tree — resolved once at import time so
# callers don't need to know where the files live on disk.
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_SCHEMAS_ROOT = _PACKAGE_ROOT / "schemas"
_SEED_ROOT = _PACKAGE_ROOT / "seed"


# The Infrahub kind representing our Device. The base schema-library uses
# ``DcimDevice``; project-specific extensions layer on top without changing
# the kind.
DEVICE_KIND = "DcimDevice"

# Child kinds carrying a device's config intent. Live device nodes expose no
# usable interface/session peers, so desired state is assembled from
# child-side queries filtered by ``device__ids`` (#33).
INTERFACE_KIND = "InterfacePhysical"
SESSION_KIND = "RoutingBGPSession"

# Schema namespaces declared in the packaged YAML tree. ``get_schema`` uses
# this set to filter out Infrahub built-ins (Core*, Builtin*, Profile*) from
# the provisioned kind list.
_PROJECT_NAMESPACES: tuple[str, ...] = (
    "Dcim",
    "Interface",
    "Ipam",
    "Location",
    "Organization",
    "Routing",
    "Business",
)


def _is_project_kind(kind: str) -> bool:
    return any(kind.startswith(ns) for ns in _PROJECT_NAMESPACES)


def _value(node: Any, attr: str, default: Any = None) -> Any:
    """Read a scalar attribute off an SDK node safely.

    Infrahub SDK attributes expose their scalar as ``.value``. Mocks in tests
    follow the same shape via :class:`types.SimpleNamespace`.
    """
    obj = getattr(node, attr, None)
    if obj is None:
        return default
    return getattr(obj, "value", default)


def _peers(node: Any, attr: str) -> list[Any]:
    """Read a cardinality-many relation off an SDK node."""
    obj = getattr(node, attr, None)
    if obj is None:
        return []
    peers = getattr(obj, "peers", None)
    return list(peers) if peers is not None else []


def _resolve_peer(rel: Any) -> Any | None:
    """Resolve a ``RelatedNode``-like relation object to its peer node, or None.

    The live SDK's ``.peer`` is a property that *raises* when the relation is
    unset or the peer isn't in the SDK store — degrade to None rather than let
    it propagate. Used for both cardinality-one relations and the individual
    peers of a cardinality-many relation (#45).
    """
    if rel is None:
        return None
    try:
        return rel.peer
    except Exception:
        return None


def _peer_node(node: Any, attr: str) -> Any | None:
    """Resolve a cardinality-one relation's peer node, or None."""
    return _resolve_peer(getattr(node, attr, None))


def _peer_id(node: Any, attr: str) -> str | None:
    """Read the id of the peer on a cardinality-one relation."""
    peer = _peer_node(node, attr)
    return getattr(peer, "id", None) if peer is not None else None


def _peer_value(node: Any, rel_attr: str, value_attr: str, default: Any = None) -> Any:
    """Read a scalar attribute off a cardinality-one relation's peer."""
    peer = _peer_node(node, rel_attr)
    if peer is None:
        return default
    return _value(peer, value_attr, default=default)


def _split_cidr(raw: Any) -> tuple[str | None, int | None]:
    """Split a CIDR value (str or ipaddress object) into (address, prefixlen)."""
    if not raw:
        return None, None
    text = str(raw)
    address, _, prefix = text.partition("/")
    return address or None, int(prefix) if prefix.isdigit() else None


class InfrahubIntentStore(IntentStore):
    """Concrete :class:`IntentStore` backed by Infrahub."""

    def __init__(self, *, client: Any, branch: str = "main") -> None:
        """Construct a store.

        Args:
            client: An :class:`infrahub_sdk.InfrahubClient` (or compatible mock).
            branch: Default Infrahub branch for queries.
        """
        self._client = client
        self._default_branch = branch

    # ------------------------------------------------------------------ US1

    async def get_desired_state(
        self,
        *,
        device_id: UUID | None = None,
        use_case: str | None = None,
        role: str | None = None,
        name: str | None = None,
        branch: str | None = None,
    ) -> list[DesiredState]:
        filters: dict[str, Any] = {}
        if device_id is not None:
            filters["ids"] = [str(device_id)]
        if use_case is not None:
            filters["use_case__value"] = use_case
        if role is not None:
            filters["role__value"] = role
        if name is not None:
            filters["name__value"] = name

        resolved_branch = branch or self._default_branch
        device_nodes = await self._filters(kind=DEVICE_KIND, branch=resolved_branch, **filters)
        if not device_nodes:
            return []

        # Live device nodes carry no usable interface/session peers, so the
        # config intent is queried from the child side in two batch calls
        # and grouped back onto the devices (#33).
        device_ids = [str(node.id) for node in device_nodes]
        relation_kwargs: dict[str, Any] = {
            "branch": resolved_branch,
            "device__ids": device_ids,
            "prefetch_relationships": True,
            "populate_store": True,
        }
        iface_nodes = await self._filters(kind=INTERFACE_KIND, **relation_kwargs)
        session_nodes = await self._filters(kind=SESSION_KIND, **relation_kwargs)

        ifaces_by_device: dict[str, list[Any]] = {}
        for node in iface_nodes:
            device_ref = _peer_id(node, "device")
            if device_ref is not None:
                ifaces_by_device.setdefault(device_ref, []).append(node)
        sessions_by_device: dict[str, list[Any]] = {}
        for node in session_nodes:
            device_ref = _peer_id(node, "device")
            if device_ref is not None:
                sessions_by_device.setdefault(device_ref, []).append(node)

        states: list[DesiredState] = []
        for node in device_nodes:
            device = self._node_to_device(node)
            node_id = str(node.id)
            states.append(
                DesiredState(
                    device=device,
                    interfaces=[
                        self._node_to_interface(iface, fallback_device_id=device.id)
                        for iface in ifaces_by_device.get(node_id, [])
                    ],
                    bgp_sessions=[
                        self._node_to_bgp_session(session, fallback_device_id=device.id)
                        for session in sessions_by_device.get(node_id, [])
                    ],
                )
            )
        return states

    async def _filters(self, **kwargs: Any) -> list[Any]:
        """Call ``client.filters`` translating SDK failures to domain errors."""
        try:
            return list(await self._client.filters(**kwargs) or [])
        except (OSError, TimeoutError) as exc:
            raise IntentConnectionError(f"Infrahub unreachable: {exc}") from exc
        except IntentConnectionError:
            raise
        except Exception as exc:
            # Any non-domain error escaping the SDK is treated as a connection
            # problem for callers. SDK-specific exceptions that may be subclasses
            # of IntentConnectionError pass through unchanged above.
            if exc.__class__.__module__.startswith("infrahub"):
                raise IntentConnectionError(f"Infrahub error: {exc}") from exc
            raise

    # ------------------------------------------------------------------ Phase stubs
    # These raise NotImplementedError until later tasks add them, but they
    # satisfy the ABC so the class is instantiable in Phase 3 unit tests.

    async def get_schema(self, use_case: str) -> Schema:
        # A use case is "known" iff its seed directory exists in the package.
        if not (_SEED_ROOT / use_case).is_dir():
            raise IntentSchemaError(f"Unknown use case: {use_case}")

        try:
            registry = await self._client.schema.all(branch=self._default_branch)
        except (OSError, TimeoutError) as exc:
            raise IntentConnectionError(f"Infrahub unreachable: {exc}") from exc
        except Exception as exc:
            if exc.__class__.__module__.startswith("infrahub"):
                raise IntentConnectionError(f"Infrahub error: {exc}") from exc
            raise

        project_kinds = sorted(k for k in (registry or {}) if _is_project_kind(k))
        if not project_kinds:
            raise IntentSchemaError(f"No schema provisioned for use case {use_case!r} — run provision_schema first")

        batches = discover_schema_batches(_SCHEMAS_ROOT)
        source_files = sorted(path.name for batch in batches for path in batch)

        return Schema(
            use_case=use_case,
            version="1.0",
            entities=project_kinds,
            source_files=source_files,
        )

    async def provision_schema(self, use_case: str) -> ProvisionResult:
        loader = SchemaLoader(
            client=self._client,
            schemas_root=_SCHEMAS_ROOT,
            use_case=use_case,
            branch=self._default_branch,
        )
        return await loader.load()

    async def seed(
        self,
        use_case: str,
        *,
        data_path: Path | None = None,
        branch: str | None = None,
    ) -> SeedResult:
        resolved_path = data_path or (_SEED_ROOT / use_case / "topology.yml")
        ingester = SeedIngester(client=self._client)
        return await ingester.seed(
            use_case=use_case,
            data_path=resolved_path,
            branch=branch or self._default_branch,
        )

    async def delete_device(self, device_id: UUID) -> DeleteResult:
        try:
            nodes = await self._client.filters(
                kind=DEVICE_KIND,
                ids=[str(device_id)],
                prefetch_relationships=True,
            )
        except (OSError, TimeoutError) as exc:
            raise IntentConnectionError(f"Infrahub unreachable: {exc}") from exc

        if not nodes:
            raise IntentNotFoundError(f"Device {device_id} not found")

        node = nodes[0]
        device_name = _value(node, "name", default="unknown")
        interfaces = _peers(node, "interfaces")
        bgp_sessions = _peers(node, "bgp_sessions")
        records_removed = 1 + len(interfaces) + len(bgp_sessions)

        try:
            await node.delete()
        except Exception as exc:
            raise IntentDeletionError(f"Failed to delete device {device_name!r}: {exc}") from exc

        return DeleteResult(
            device_id=device_id,
            device_name=device_name,
            records_removed=records_removed,
        )

    # ------------------------------------------------------------------ mapping helpers

    def _node_to_device(self, node: Any) -> Device:
        # Schema attribute is ``management_ip`` (IPHost — the live SDK yields
        # an ipaddress interface object, mocks a CIDR string); the Device
        # contract wants a bare address usable as router-id, loopback, or
        # dial target, so stringify and strip the /prefix.
        management_ip = _value(node, "management_ip", default="") or ""
        return Device(
            id=UUID(str(node.id)),
            name=_value(node, "name"),
            management_address=str(management_ip).split("/", 1)[0],
            role=_value(node, "role", default=""),
            use_case=_value(node, "use_case", default=""),
            platform=_value(node, "platform"),
            description=_value(node, "description"),
            lab_node_name=_value(node, "lab_node_name"),
        )

    def _node_to_interface(self, node: Any, *, fallback_device_id: UUID) -> Interface:
        # Live InterfacePhysical shape: the IP lives on the ``ip_addresses``
        # relation (IpamIPAddress peer with a CIDR ``address``); enablement is
        # the ``status`` attribute. There are no ip_address/prefix_length/
        # enabled scalars (#33).
        peer_id = _peer_id(node, "device")
        device_id = UUID(peer_id) if peer_id else fallback_device_id
        ip_address: str | None = None
        prefix_length: int | None = None
        for ip_rel in _peers(node, "ip_addresses"):
            ip_peer = _resolve_peer(ip_rel)
            ip_address, prefix_length = _split_cidr(_value(ip_peer, "address"))
            if ip_address:
                break
        status = _value(node, "status")
        return Interface(
            id=UUID(str(node.id)),
            device_id=device_id,
            name=_value(node, "name", default=""),
            description=_value(node, "description"),
            ip_address=ip_address,
            prefix_length=prefix_length,
            enabled=True if status is None else status == "active",
            speed=_value(node, "speed"),
            mtu=_value(node, "mtu"),
            peer_device=_value(node, "peer_device"),
            peer_interface=_value(node, "peer_interface"),
        )

    def _node_to_bgp_session(self, node: Any, *, fallback_device_id: UUID) -> BGPSession:
        # Live RoutingBGPSession shape: ASNs and endpoint IPs are relations
        # (local_as/remote_as → RoutingAutonomousSystem, remote_ip →
        # IpamIPAddress), and the policy attributes are plural (#33).
        peer_id = _peer_id(node, "device")
        device_id = UUID(peer_id) if peer_id else fallback_device_id
        peer_address, _prefix = _split_cidr(_peer_value(node, "remote_ip", "address"))
        status = _value(node, "status")
        return BGPSession(
            id=UUID(str(node.id)),
            device_id=device_id,
            local_asn=_peer_value(node, "local_as", "asn", default=0) or 0,
            peer_address=peer_address or "",
            peer_asn=_peer_value(node, "remote_as", "asn", default=0) or 0,
            peer_group=_peer_value(node, "peer_group", "name"),
            address_family=_value(node, "address_family", default="ipv4_unicast"),
            export_policy=_value(node, "export_policies"),
            import_policy=_value(node, "import_policies"),
            enabled=True if status is None else status == "active",
        )
