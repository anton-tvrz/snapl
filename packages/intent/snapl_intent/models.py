"""Pydantic models for the Intent module.

These models are the public data contract — consumers (Executor, Observability,
Orchestrator, Presentation) construct and consume them via the ``IntentStore``
ABC and never import Infrahub SDK types directly.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Core entities
# ---------------------------------------------------------------------------


class Device(BaseModel):
    """A network element within a single use case."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    id: UUID
    name: str
    management_address: str
    role: str
    use_case: str
    platform: str | None = None
    description: str | None = None
    lab_node_name: str | None = None
    """Containerlab hostname (e.g. clab-dcfabric-spine-01). When set, it is
    the gNMI dial target; management_address stays intent-only data."""


class Interface(BaseModel):
    """A network interface on a device."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    id: UUID
    device_id: UUID
    name: str
    description: str | None = None
    ip_address: str | None = None
    prefix_length: int | None = None
    enabled: bool = True
    speed: str | None = None
    mtu: int | None = None
    """None means "no mtu in intent" — rendered as no mtu key, so devices that
    reject mtu on an interface class (SR Linux loopbacks) still converge (#78)."""
    peer_device: str | None = None
    peer_interface: str | None = None


class BGPSession(BaseModel):
    """A BGP peering session on a device."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    id: UUID
    device_id: UUID
    local_asn: int
    peer_address: str
    peer_asn: int
    peer_group: str | None = None
    address_family: str = "ipv4_unicast"
    export_policy: str | None = None
    import_policy: str | None = None
    enabled: bool = True


class DesiredState(BaseModel):
    """The complete intended configuration for a single device."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    device: Device
    interfaces: list[Interface] = Field(default_factory=list)
    bgp_sessions: list[BGPSession] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Metadata / result models
# ---------------------------------------------------------------------------


class Schema(BaseModel):
    """A structured view of the data model for a use case.

    This is a read-only derivation from the Source of Truth's schema registry —
    it is not persisted as a separate entity.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    use_case: str
    version: str
    entities: list[str] = Field(default_factory=list)
    source_files: list[str] = Field(default_factory=list)
    provisioned_at: datetime | None = None


class ProvisionResult(BaseModel):
    """Outcome of ``provision_schema``."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    use_case: str
    schemas_loaded: int
    changed: bool


class SeedResult(BaseModel):
    """Outcome of ``seed``."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    use_case: str
    devices_created: int
    devices_updated: int
    total_records: int
    branch: str


class DeleteResult(BaseModel):
    """Outcome of ``delete_device``."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    device_id: UUID
    device_name: str
    records_removed: int
