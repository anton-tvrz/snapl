"""Pydantic models for the Observability module.

Public data contract — consumers (Orchestrator, Presentation) construct and
consume these via the Observer ABC. Every model is frozen and rejects extra
fields.
"""

from __future__ import annotations

from collections.abc import Mapping  # noqa: TC003 — pydantic resolves field types at runtime
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DriftStatus(StrEnum):
    CLEAN = "clean"
    DRIFTED = "drifted"
    ERROR = "error"


class EventType(StrEnum):
    DRIFT_DETECTED = "drift_detected"
    STATE_CLEAN = "state_clean"
    DRIFT_ERROR = "drift_error"


class AuditOperation(StrEnum):
    DETECT_DRIFT = "detect_drift"
    EMIT_EVENT = "emit_event"
    LOG_AUDIT = "log_audit"


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"


# Status → EventType mapping. Single source of truth shared by the Observer
# and the ObservabilityEvent validator.
STATUS_TO_EVENT_TYPE: dict[DriftStatus, EventType] = {
    DriftStatus.DRIFTED: EventType.DRIFT_DETECTED,
    DriftStatus.CLEAN: EventType.STATE_CLEAN,
    DriftStatus.ERROR: EventType.DRIFT_ERROR,
}


# ---------------------------------------------------------------------------
# Drift models
# ---------------------------------------------------------------------------


class DriftItem(BaseModel):
    """A single detected discrepancy between desired and actual state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    desired: Any | None
    actual: Any | None
    entity_kind: str
    # Which way the difference runs. False (the default) means intent asked
    # for something the device lacks or got wrong; True means the device
    # carries something intent never asked for. The remediations differ — one
    # is an apply, the other a delete — so a reader must not have to infer the
    # direction from `desired is None`, which a missing value also produces.
    undesired: bool = False

    @model_validator(mode="after")
    def _check_values_differ(self) -> DriftItem:
        if self.desired == self.actual:
            raise ValueError(f"DriftItem at {self.path}: desired and actual are equal — not a discrepancy")
        return self


class DriftReport(BaseModel):
    """The complete drift analysis for a single device."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    device_id: UUID
    device_name: str
    status: DriftStatus
    items: list[DriftItem]
    error: str | None = None
    timestamp: datetime

    @model_validator(mode="after")
    def _check_status_invariants(self) -> DriftReport:
        if self.status == DriftStatus.CLEAN:
            if self.items:
                raise ValueError("DriftReport status=CLEAN must have empty items")
            if self.error is not None:
                raise ValueError("DriftReport status=CLEAN must have error=None")
        elif self.status == DriftStatus.DRIFTED:
            if not self.items:
                raise ValueError("DriftReport status=DRIFTED requires at least one item")
            if self.error is not None:
                raise ValueError("DriftReport status=DRIFTED must have error=None")
        elif self.status == DriftStatus.ERROR:
            if self.items:
                raise ValueError("DriftReport status=ERROR must have empty items")
            if self.error is None:
                raise ValueError("DriftReport status=ERROR requires error to be set")
        return self


class BatchDriftReport(BaseModel):
    """Aggregated outcome of detect_drift_batch()."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reports: dict[UUID, DriftReport]
    total: int
    clean: int
    drifted: int
    errored: int

    @model_validator(mode="after")
    def _check_counts_sum(self) -> BatchDriftReport:
        if self.clean + self.drifted + self.errored != self.total:
            raise ValueError(
                f"clean ({self.clean}) + drifted ({self.drifted}) + errored ({self.errored}) "
                f"must equal total ({self.total})"
            )
        return self


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------


class ObservabilityEvent(BaseModel):
    """A structured notification emitted after a drift check."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: EventType
    device_id: UUID
    device_name: str
    report: DriftReport
    timestamp: datetime

    @model_validator(mode="after")
    def _check_type_matches_status(self) -> ObservabilityEvent:
        expected = STATUS_TO_EVENT_TYPE[self.report.status]
        if self.event_type != expected:
            raise ValueError(
                f"event_type {self.event_type.value} does not match report.status "
                f"{self.report.status.value} — expected {expected.value}"
            )
        return self


# ---------------------------------------------------------------------------
# Audit model
# ---------------------------------------------------------------------------


class AuditEntry(BaseModel):
    """An immutable record of one Observability operation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: AuditOperation
    device_id: UUID | None = None
    component: str
    outcome: AuditOutcome
    detail: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


# ---------------------------------------------------------------------------
# Device health (spec 016)
# ---------------------------------------------------------------------------


class DeviceHealthState(StrEnum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class HealthReasonKind(StrEnum):
    INTERFACE = "interface"
    BGP_SESSION = "bgp_session"
    NO_TELEMETRY = "no_telemetry"


class HealthReason(BaseModel):
    """Why a device is not healthy. Built by the constructors, which render the fixed texts.

    The dashboard, the CLI and the audit trail all show ``text``, so they say the same thing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: HealthReasonKind
    item: str | None = None
    expected: str | None = None
    actual: str | None = None
    text: str

    @classmethod
    def interface(cls, name: str, *, actual: str, expected: str) -> Self:
        """``<name> <actual> (expected <expected>)``; ``actual`` is ``up``, ``down`` or ``absent``."""
        return cls(
            kind=HealthReasonKind.INTERFACE,
            item=name,
            expected=expected,
            actual=actual,
            text=f"{name} {actual} (expected {expected})",
        )

    @classmethod
    def bgp_session(cls, peer: str, *, actual: str) -> Self:
        """``BGP <peer> <actual> (expected established)``."""
        return cls(
            kind=HealthReasonKind.BGP_SESSION,
            item=peer,
            expected="established",
            actual=actual,
            text=f"BGP {peer} {actual} (expected established)",
        )

    @classmethod
    def no_telemetry(cls, since: datetime | None) -> Self:
        """``no telemetry since <ISO time>``, or ``no telemetry yet``."""
        if since is None:
            return cls(kind=HealthReasonKind.NO_TELEMETRY, text="no telemetry yet")
        when = since.astimezone(UTC).isoformat(timespec="seconds")
        return cls(kind=HealthReasonKind.NO_TELEMETRY, text=f"no telemetry since {when}")

    @classmethod
    def telemetry_stopped(cls) -> Self:
        """``telemetry stopped``: the worker stopped its streams cleanly (research R6)."""
        return cls(kind=HealthReasonKind.NO_TELEMETRY, text="telemetry stopped")


class HealthExpectation(BaseModel):
    """What intent expects of one device's interfaces and BGP sessions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    device_name: str
    interfaces_up: frozenset[str]
    interfaces_down: frozenset[str]
    sessions_established: frozenset[str]

    @model_validator(mode="after")
    def _up_and_down_are_apart(self) -> HealthExpectation:
        both = self.interfaces_up & self.interfaces_down
        if both:
            raise ValueError(f"interfaces expected both up and down: {sorted(both)}")
        return self


class ObservedItem(BaseModel):
    """An item's latest state, and when it was first read in that state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: str
    since: datetime


class HealthObservation(BaseModel):
    """What the device's telemetry has shown. ``last_sample_at`` is ``None`` until a sample arrives."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    last_sample_at: datetime | None
    interfaces: Mapping[str, ObservedItem] = Field(default_factory=dict)
    sessions: Mapping[str, ObservedItem] = Field(default_factory=dict)


class DeviceHealth(BaseModel):
    """One device's health at ``evaluated_at``. ``reasons`` is empty when healthy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    device_name: str
    state: DeviceHealthState
    reasons: tuple[HealthReason, ...] = ()
    evaluated_at: datetime
