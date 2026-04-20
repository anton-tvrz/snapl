"""Domain exceptions for the Intent module.

All consumers interact with Intent only through these exceptions — Infrahub
SDK errors and raw HTTP failures are mapped here at the client boundary so
downstream code never imports Infrahub symbols just to catch errors.
"""

from __future__ import annotations


class IntentError(Exception):
    """Base exception for all Intent module errors."""


class IntentConnectionError(IntentError):
    """Source of Truth is unreachable (network, DNS, timeout, auth)."""


class IntentNotFoundError(IntentError):
    """A specific requested entity does not exist.

    Note: ``get_desired_state`` returns an empty list when no devices match
    the filter — it does not raise this exception. This is raised by
    operations that target a single, known entity (e.g. ``delete_device``).
    """


class IntentValidationError(IntentError):
    """Submitted data failed schema validation.

    Attributes:
        field: The schema field that failed, if known.
        detail: A human-readable description of the violation.
    """

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.field = field
        self.detail = detail


class IntentSchemaError(IntentError):
    """Schema provisioning or inspection failed.

    Raised when:
    - A use case has no schema files on disk
    - A use case has no schema provisioned in the Source of Truth
    - Schema YAML fails to parse
    - Infrahub rejects a schema definition
    """


class IntentDeletionError(IntentError):
    """Deletion preconditions were not met.

    Callers (the Orchestrator, typically) are responsible for coordinating
    with the Collector to confirm safe decommissioning before invoking
    ``delete_device``. This error surfaces when the Source of Truth itself
    cannot perform the deletion — the Intent module does not gate on
    observed state.
    """
