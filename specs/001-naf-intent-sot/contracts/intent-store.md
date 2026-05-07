# Contract: IntentStore

**Feature**: 001-naf-intent-sot
**Date**: 2026-04-15
**Type**: Abstract Base Class (Python)

## Overview

The `IntentStore` ABC is the public interface of the NAF Intent building block. All consumers (Executor, Orchestrator, Observability) interact with Intent exclusively through this contract.

## Interface Definition

```python
from abc import ABC, abstractmethod
from pathlib import Path
from uuid import UUID

class IntentStore(ABC):
    """NAF Intent building block — Source of Truth interface."""

    # ── Desired State Retrieval ──────────────────────────────────────────

    @abstractmethod
    async def get_desired_state(
        self,
        *,
        device_id: UUID | None = None,
        use_case: str | None = None,
        role: str | None = None,
        name: str | None = None,
    ) -> list[DesiredState]:
        """Retrieve desired network state with optional filters.

        Returns all matching devices with their full configuration
        (interfaces, BGP sessions). Filters are AND-combined.

        Raises:
            IntentConnectionError: Source of Truth unreachable
            IntentNotFoundError: No devices match the query (empty list returned, not raised)
        """

    # ── Schema Operations ────────────────────────────────────────────────

    @abstractmethod
    async def get_schema(self, use_case: str) -> Schema:
        """Retrieve the data model definition for a use case.

        Returns structured schema describing entities, fields, types,
        and relationships.

        Raises:
            IntentConnectionError: Source of Truth unreachable
            IntentSchemaError: Use case has no schema provisioned
        """

    @abstractmethod
    async def provision_schema(self, use_case: str) -> ProvisionResult:
        """Install or update the data model for a use case into the Source of Truth.

        Idempotent — repeated calls with the same schema produce no changes.
        Schema YAML files are resolved from the package's schemas/ directory.

        Raises:
            IntentConnectionError: Source of Truth unreachable
            IntentSchemaError: Schema definition invalid or dependency ordering failure
        """

    # ── Data Operations ──────────────────────────────────────────────────

    @abstractmethod
    async def seed(
        self,
        use_case: str,
        *,
        data_path: Path | None = None,
        branch: str | None = None,
    ) -> SeedResult:
        """Ingest seed data from declarative YAML files into the Source of Truth.

        Requires a provisioned schema. Uses upsert semantics — existing
        records are updated, new records are created. No duplicates.

        Args:
            use_case: Target use case identifier
            data_path: Override path to seed data directory (default: package seed/<use_case>/)
            branch: Target Infrahub branch (default: main)

        Raises:
            IntentConnectionError: Source of Truth unreachable
            IntentSchemaError: Schema not provisioned for this use case
            IntentValidationError: Seed data fails schema validation
        """

    @abstractmethod
    async def delete_device(self, device_id: UUID) -> DeleteResult:
        """Remove a device and its desired state from the Source of Truth.

        Deletion is not immediate — the Intent module exposes this as a
        deletable operation that other NAF modules (Collector, Orchestrator)
        can gate. The caller is responsible for coordination.

        Raises:
            IntentConnectionError: Source of Truth unreachable
            IntentNotFoundError: Device does not exist
            IntentDeletionError: Deletion preconditions not met
        """
```

## Data Models (Pydantic)

```python
from datetime import datetime
from pydantic import BaseModel
from uuid import UUID

class Device(BaseModel):
    id: UUID
    name: str
    management_address: str
    role: str
    use_case: str
    platform: str | None = None
    description: str | None = None

class Interface(BaseModel):
    id: UUID
    device_id: UUID
    name: str
    description: str | None = None
    ip_address: str | None = None
    prefix_length: int | None = None
    enabled: bool = True
    speed: str | None = None
    mtu: int = 9232
    peer_device: str | None = None
    peer_interface: str | None = None

class BGPSession(BaseModel):
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
    """Complete intended configuration for a single device."""
    device: Device
    interfaces: list[Interface]
    bgp_sessions: list[BGPSession]

class Schema(BaseModel):
    """Data model definition for a use case."""
    use_case: str
    version: str
    entities: list[str]
    source_files: list[str]
    provisioned_at: datetime | None = None

class ProvisionResult(BaseModel):
    use_case: str
    schemas_loaded: int
    changed: bool

class SeedResult(BaseModel):
    use_case: str
    devices_created: int
    devices_updated: int
    total_records: int
    branch: str

class DeleteResult(BaseModel):
    device_id: UUID
    device_name: str
    records_removed: int
```

## Exception Hierarchy

```python
class IntentError(Exception):
    """Base exception for all Intent module errors."""

class IntentConnectionError(IntentError):
    """Source of Truth is unreachable."""

class IntentNotFoundError(IntentError):
    """Requested entity does not exist."""

class IntentValidationError(IntentError):
    """Data fails schema validation."""
    field: str | None = None
    detail: str | None = None

class IntentSchemaError(IntentError):
    """Schema operation failed."""

class IntentDeletionError(IntentError):
    """Deletion preconditions not met."""
```

## Consumer Expectations

| Consumer | Methods Used | Notes |
|----------|-------------|-------|
| Executor | `get_desired_state()` | Retrieves config to deploy via gNMI |
| Collector | (none directly) | Collector feeds Observability, not Intent |
| Observability | `get_desired_state()` | Compares desired vs actual for drift detection |
| Orchestrator | All methods | Coordinates full lifecycle workflows |
| Presentation | `get_desired_state()`, `get_schema()` | Display/query operations for users |

## Guarantees

- All methods are **async** — callers must await them
- `get_desired_state()` with no matches returns an **empty list**, not an error
- `provision_schema()` is **idempotent** — safe to call repeatedly
- `seed()` uses **upsert** semantics — no duplicates on re-run
- All errors are domain exceptions (never raw SDK/HTTP errors)
- **10-second timeout** on all Source of Truth operations (SC-007)
