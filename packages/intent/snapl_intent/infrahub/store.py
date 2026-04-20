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
from snapl_intent.exceptions import IntentConnectionError, IntentSchemaError
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


def _peer_id(node: Any, attr: str) -> str | None:
    """Read the id of the peer on a cardinality-one relation."""
    obj = getattr(node, attr, None)
    if obj is None:
        return None
    peer = getattr(obj, "peer", None)
    return getattr(peer, "id", None) if peer is not None else None


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

        try:
            nodes = await self._client.filters(kind=DEVICE_KIND, **filters)
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

        return [self._node_to_desired_state(node) for node in (nodes or [])]

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
            raise IntentSchemaError(
                f"No schema provisioned for use case {use_case!r} — run provision_schema first"
            )

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
        """Implemented in Phase 7 (polish)."""
        raise NotImplementedError("delete_device is implemented in Phase 7")

    # ------------------------------------------------------------------ mapping helpers

    def _node_to_desired_state(self, node: Any) -> DesiredState:
        device = self._node_to_device(node)
        interfaces = [
            self._node_to_interface(iface, fallback_device_id=device.id)
            for iface in _peers(node, "interfaces")
        ]
        sessions = [
            self._node_to_bgp_session(session, fallback_device_id=device.id)
            for session in _peers(node, "bgp_sessions")
        ]
        return DesiredState(device=device, interfaces=interfaces, bgp_sessions=sessions)

    def _node_to_device(self, node: Any) -> Device:
        return Device(
            id=UUID(str(node.id)),
            name=_value(node, "name"),
            management_address=_value(node, "management_address", default=""),
            role=_value(node, "role", default=""),
            use_case=_value(node, "use_case", default=""),
            platform=_value(node, "platform"),
            description=_value(node, "description"),
        )

    def _node_to_interface(self, node: Any, *, fallback_device_id: UUID) -> Interface:
        peer_id = _peer_id(node, "device")
        device_id = UUID(peer_id) if peer_id else fallback_device_id
        return Interface(
            id=UUID(str(node.id)),
            device_id=device_id,
            name=_value(node, "name", default=""),
            description=_value(node, "description"),
            ip_address=_value(node, "ip_address"),
            prefix_length=_value(node, "prefix_length"),
            enabled=_value(node, "enabled", default=True),
            speed=_value(node, "speed"),
            mtu=_value(node, "mtu", default=9232),
            peer_device=_value(node, "peer_device"),
            peer_interface=_value(node, "peer_interface"),
        )

    def _node_to_bgp_session(self, node: Any, *, fallback_device_id: UUID) -> BGPSession:
        peer_id = _peer_id(node, "device")
        device_id = UUID(peer_id) if peer_id else fallback_device_id
        return BGPSession(
            id=UUID(str(node.id)),
            device_id=device_id,
            local_asn=_value(node, "local_asn", default=0),
            peer_address=_value(node, "peer_address", default=""),
            peer_asn=_value(node, "peer_asn", default=0),
            peer_group=_value(node, "peer_group"),
            address_family=_value(node, "address_family", default="ipv4_unicast"),
            export_policy=_value(node, "export_policy"),
            import_policy=_value(node, "import_policy"),
            enabled=_value(node, "enabled", default=True),
        )
