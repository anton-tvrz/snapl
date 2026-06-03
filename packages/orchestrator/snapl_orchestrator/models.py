"""Pydantic models for the NAF Orchestrator.

Public data contract — workflows return these, the AuditLog persists `AuditEvent`,
and Presentation / external automation consume them. Every model is frozen and
rejects extra fields, consistent with snapl_observability and the constitution.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import StrEnum
from typing import Any
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field, model_validator

from snapl_observability.models import DriftItem, DriftReport  # noqa: TC001

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class WorkflowReason(StrEnum):
    """Terminal reason codes for any WorkflowResult."""

    SUCCEEDED = "succeeded"
    INTENT_UNAVAILABLE = "intent_unavailable"
    APPLY_FAILED = "apply_failed"
    COLLECT_FAILED = "collect_failed"
    VERIFICATION_FAILED = "verification_failed"
    AUDIT_FAILED = "audit_failed"
    CANCELLED = "cancelled"
    DEVICE_NOT_FOUND = "device_not_found"


class AuditEventType(StrEnum):
    """Audit event types persisted to the durable AuditLog."""

    WORKFLOW_STARTED = "workflow_started"
    ACTIVITY_STARTED = "activity_started"
    ACTIVITY_COMPLETED = "activity_completed"
    ACTIVITY_FAILED = "activity_failed"
    WORKFLOW_TERMINATED = "workflow_terminated"
    WORKFLOW_CANCELLED = "workflow_cancelled"


_VALID_OUTCOMES = frozenset({"success", "failure", "cancelled"})


# ---------------------------------------------------------------------------
# Workflow result models
# ---------------------------------------------------------------------------


class WorkflowResult(BaseModel):
    """The terminal outcome of a single workflow run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str
    workflow_type: str
    target_id: UUID | str
    success: bool
    reason: WorkflowReason
    detail: str | None = None
    started_at: datetime
    ended_at: datetime
    drift_items: list[DriftItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_invariants(self) -> WorkflowResult:
        if self.success and self.reason != WorkflowReason.SUCCEEDED:
            raise ValueError(f"success=True requires reason=SUCCEEDED, got {self.reason}")
        if not self.success and self.reason == WorkflowReason.SUCCEEDED:
            raise ValueError("success=False requires a non-SUCCEEDED reason")
        if self.drift_items and self.reason != WorkflowReason.VERIFICATION_FAILED:
            raise ValueError("drift_items populated only for VERIFICATION_FAILED")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must be >= started_at")
        return self


class DriftScanResult(BaseModel):
    """The outcome of a ScanDrift workflow."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str
    use_case_id: str
    reports: dict[UUID, DriftReport]
    total: int
    clean: int
    drifted: int
    errored: int
    started_at: datetime
    ended_at: datetime

    @model_validator(mode="after")
    def _check_counts(self) -> DriftScanResult:
        if self.clean + self.drifted + self.errored != self.total:
            raise ValueError(
                f"clean ({self.clean}) + drifted ({self.drifted}) + errored ({self.errored}) "
                f"must equal total ({self.total})"
            )
        if len(self.reports) != self.total:
            raise ValueError(f"reports length ({len(self.reports)}) must equal total ({self.total})")
        return self


class ReconcileResult(BaseModel):
    """The aggregated outcome of a ReconcileDevices workflow."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str
    device_results: dict[UUID, WorkflowResult]
    total: int
    succeeded: int
    failed: int
    skipped: int = 0
    started_at: datetime
    ended_at: datetime

    @model_validator(mode="after")
    def _check_counts(self) -> ReconcileResult:
        if self.succeeded + self.failed + self.skipped != self.total:
            raise ValueError(
                f"succeeded ({self.succeeded}) + failed ({self.failed}) + skipped ({self.skipped}) "
                f"must equal total ({self.total})"
            )
        if len(self.device_results) + self.skipped != self.total:
            raise ValueError(
                f"device_results ({len(self.device_results)}) + skipped ({self.skipped}) "
                f"must equal total ({self.total})"
            )
        return self


# ---------------------------------------------------------------------------
# Audit event
# ---------------------------------------------------------------------------


class AuditEvent(BaseModel):
    """A single durable, append-only audit record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    workflow_id: str
    workflow_type: str
    target_id: UUID | str | None = None
    event_type: AuditEventType
    activity_name: str | None = None
    outcome: str | None = None
    reason: WorkflowReason | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime
    actor: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> AuditEvent:
        activity_events = {
            AuditEventType.ACTIVITY_STARTED,
            AuditEventType.ACTIVITY_COMPLETED,
            AuditEventType.ACTIVITY_FAILED,
        }
        if self.event_type in activity_events and self.activity_name is None:
            raise ValueError(f"{self.event_type.value} requires activity_name")
        if self.event_type == AuditEventType.WORKFLOW_TERMINATED and self.reason is None:
            raise ValueError("WORKFLOW_TERMINATED requires reason")
        if self.outcome is not None and self.outcome not in _VALID_OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(_VALID_OUTCOMES)} or None, got {self.outcome!r}")
        return self
