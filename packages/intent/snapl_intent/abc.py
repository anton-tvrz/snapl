"""Abstract base class defining the Intent building block interface.

All consumers (Executor, Orchestrator, Observability, Presentation) depend
on this ABC rather than the concrete ``InfrahubIntentStore`` — the ABC keeps
the Source of Truth swappable and is the single integration point for the
rest of the NAF loop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from snapl_intent.models import (
        DeleteResult,
        DesiredState,
        ProvisionResult,
        Schema,
        SeedResult,
    )


class IntentStore(ABC):
    """NAF Intent building block — Source of Truth interface."""

    # ---- Desired state retrieval --------------------------------------------

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

        Filters are AND-combined. Returns an empty list when no devices match —
        callers should not treat an empty result as an error.

        Raises:
            IntentConnectionError: Source of Truth unreachable.
        """

    # ---- Schema operations --------------------------------------------------

    @abstractmethod
    async def get_schema(self, use_case: str) -> Schema:
        """Return the data model definition for a use case.

        Raises:
            IntentConnectionError: Source of Truth unreachable.
            IntentSchemaError: Use case has no schema provisioned.
        """

    @abstractmethod
    async def provision_schema(self, use_case: str) -> ProvisionResult:
        """Install or update the data model for a use case.

        Idempotent — repeat calls with the same schema produce no changes.
        YAML files are resolved from the package's ``schemas/`` directory and
        loaded in three dependency-ordered batches (base -> extensions ->
        project-specific).

        Raises:
            IntentConnectionError: Source of Truth unreachable.
            IntentSchemaError: Schema definition invalid or dependency ordering failure.
        """

    # ---- Data operations ----------------------------------------------------

    @abstractmethod
    async def seed(
        self,
        use_case: str,
        *,
        data_path: Path | None = None,
        branch: str | None = None,
    ) -> SeedResult:
        """Ingest seed data from declarative YAML files.

        Requires a provisioned schema. Upsert semantics — existing records
        are updated, new records created. Dependency order (supporting
        entities first) is enforced by the implementation.

        Args:
            use_case: Target use case identifier.
            data_path: Override path to seed data directory (default: package's
                ``seed/<use_case>/``).
            branch: Target Source-of-Truth branch (default: ``main``).

        Raises:
            IntentConnectionError: Source of Truth unreachable.
            IntentSchemaError: Schema not provisioned for this use case.
            IntentValidationError: Seed data fails schema validation.
        """

    @abstractmethod
    async def delete_device(self, device_id: UUID) -> DeleteResult:
        """Remove a device and its desired state.

        The Intent module exposes deletion as a gate-able operation; it does
        not query the Collector itself. Callers (typically the Orchestrator)
        must coordinate with observed-state checks before invoking this.

        Raises:
            IntentConnectionError: Source of Truth unreachable.
            IntentNotFoundError: Device does not exist.
            IntentDeletionError: Deletion preconditions not met.
        """
