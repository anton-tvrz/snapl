"""NAF Intent building block — Source of Truth, desired state models, schemas."""

from snapl_intent.abc import IntentStore
from snapl_intent.exceptions import (
    IntentConnectionError,
    IntentDeletionError,
    IntentError,
    IntentNotFoundError,
    IntentSchemaError,
    IntentValidationError,
)
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

__all__ = [  # noqa: RUF022
    # ABC
    "IntentStore",
    # Exceptions
    "IntentError",
    "IntentConnectionError",
    "IntentNotFoundError",
    "IntentValidationError",
    "IntentSchemaError",
    "IntentDeletionError",
    # Models
    "Device",
    "Interface",
    "BGPSession",
    "DesiredState",
    "Schema",
    "ProvisionResult",
    "SeedResult",
    "DeleteResult",
]
