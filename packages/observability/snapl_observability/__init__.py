"""NAF Observability building block — drift detection, events, audit."""

from __future__ import annotations

from snapl_observability.abc import Observer
from snapl_observability.audit import AuditLog
from snapl_observability.events import EventBus
from snapl_observability.exceptions import ObserverError
from snapl_observability.models import (
    AuditEntry,
    AuditOperation,
    AuditOutcome,
    BatchDriftReport,
    DriftItem,
    DriftReport,
    DriftStatus,
    EventType,
    ObservabilityEvent,
)
from snapl_observability.structural.observer import StructuralObserver

__all__ = [
    "AuditEntry",
    "AuditLog",
    "AuditOperation",
    "AuditOutcome",
    "BatchDriftReport",
    "DriftItem",
    "DriftReport",
    "DriftStatus",
    "EventBus",
    "EventType",
    "ObservabilityEvent",
    "Observer",
    "ObserverError",
    "StructuralObserver",
]
