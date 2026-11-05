"""Pydantic models for the NAF Orchestrator.

Public data contract — workflows return these, the AuditLog persists `AuditEvent`,
and Presentation / external automation consume them. Every model is frozen and
rejects extra fields, consistent with snapl_observability and the constitution.
"""

from __future__ import annotations

from datetime import datetime, timedelta  # noqa: TC003
from enum import StrEnum
from typing import Any
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from snapl_observability.models import DriftItem, DriftReport, DriftStatus

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
    # A scheduled repair's reconcile failed as a whole; its deploys may have written.
    REMEDIATION_FAILED = "remediation_failed"


class AuditEventType(StrEnum):
    """Audit event types persisted to the durable AuditLog."""

    WORKFLOW_STARTED = "workflow_started"
    ACTIVITY_STARTED = "activity_started"
    ACTIVITY_COMPLETED = "activity_completed"
    ACTIVITY_FAILED = "activity_failed"
    WORKFLOW_TERMINATED = "workflow_terminated"
    WORKFLOW_CANCELLED = "workflow_cancelled"
    SCHEDULE_CHANGED = "schedule_changed"
    DRIFT_RESPONSE = "drift_response"


class DriftResponseMode(StrEnum):
    """What a scheduled run does with what its scan found (constitution VIII.2).

    The third response, ``suppress``, is a paused schedule: nothing runs, so
    nothing carries a mode.
    """

    REPORT = "report"
    REMEDIATE = "remediate"


class ExclusionReason(StrEnum):
    """Why a remediate run left a drifted or errored device alone."""

    # Only a removal fixes it, and removal is never automatic (VIII.5, #65).
    UNDESIRED_ONLY = "undesired_only"
    # The scan could not see the device; writing to it would be a guess.
    ERRORED = "errored"


class ScheduleChange(StrEnum):
    """The kind of change a SCHEDULE_CHANGED audit event records."""

    CREATED = "created"
    INTERVAL_CHANGED = "interval_changed"
    RESPONSE_CHANGED = "response_changed"
    PAUSED = "paused"
    RESUMED = "resumed"
    TRIGGERED = "triggered"
    DELETED = "deleted"


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
    # A cancelled scan returns what it has (nothing), which must not read as
    # a clean scan of zero devices.
    cancelled: bool = False

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
        if self.event_type == AuditEventType.SCHEDULE_CHANGED:
            # A schedule change is a policy decision about unattended writes,
            # so "who" is not optional (009 FR-021).
            if not self.actor:
                raise ValueError("schedule_changed requires actor")
            if self.payload.get("change") not in set(ScheduleChange):
                raise ValueError(
                    f"schedule_changed requires payload['change'] in {sorted(ScheduleChange)}, "
                    f"got {self.payload.get('change')!r}"
                )
        return self


# ---------------------------------------------------------------------------
# Drift schedules (spec 009)
# ---------------------------------------------------------------------------


def drift_schedule_id(use_case_id: str) -> str:
    """The one drift schedule a use case can have.

    Lives here rather than in ``schedules`` so ``DriftResponseRequest`` can
    validate against it without pulling ``temporalio.client`` into a module
    the workflow sandbox passes through.
    """
    return f"drift-response-{use_case_id}"


def has_forward_drift(report: DriftReport) -> bool:
    """Drifted, with at least one item intent wants (not only undesired config).

    The one rule for what a scheduled repair may touch: ``select_remediation``
    applies it and ``DriftResponseResult`` enforces it.
    """
    return report.status == DriftStatus.DRIFTED and any(not item.undesired for item in report.items)


class DriftResponseRequest(BaseModel):
    """The single argument a drift schedule passes to every run it starts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    use_case_id: str = Field(min_length=1)
    mode: DriftResponseMode = DriftResponseMode.REPORT
    schedule_id: str

    @model_validator(mode="after")
    def _check_schedule_id(self) -> DriftResponseRequest:
        expected = drift_schedule_id(self.use_case_id)
        if self.schedule_id != expected:
            raise ValueError(f"schedule_id must be {expected!r} for use case {self.use_case_id!r}")
        return self


class DriftResponseResult(BaseModel):
    """The terminal outcome of one scheduled run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str
    schedule_id: str
    use_case_id: str
    # The mode the run started with; a change mid-run applies from the next run.
    mode: DriftResponseMode
    scan: DriftScanResult
    remediated: list[UUID] = Field(default_factory=list)
    excluded: dict[UUID, ExclusionReason] = Field(default_factory=dict)
    reconcile: ReconcileResult | None = None
    started_at: datetime
    ended_at: datetime

    @model_validator(mode="after")
    def _check_invariants(self) -> DriftResponseResult:
        if self.mode == DriftResponseMode.REPORT and (self.remediated or self.excluded or self.reconcile):
            raise ValueError("a REPORT run must not remediate, exclude or reconcile")
        if (self.reconcile is None) != (not self.remediated):
            raise ValueError("reconcile must be present exactly when devices were remediated")
        if self.reconcile is not None and self.reconcile.total != len(self.remediated):
            raise ValueError(f"reconcile total ({self.reconcile.total}) must equal remediated ({len(self.remediated)})")
        if set(self.remediated) & set(self.excluded):
            raise ValueError("remediated and excluded must be disjoint")
        unknown = (set(self.remediated) | set(self.excluded)) - set(self.scan.reports)
        if unknown:
            raise ValueError(f"devices not in the scan: {sorted(map(str, unknown))}")
        ineligible = [d for d in self.remediated if not has_forward_drift(self.scan.reports[d])]
        if ineligible:
            raise ValueError(f"remediated devices without forward drift: {sorted(map(str, ineligible))}")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must be >= started_at")
        return self


class RunSummary(BaseModel):
    """One completed scheduled run, as the schedule view summarises it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int
    clean: int
    drifted: int
    errored: int
    remediated: int
    succeeded: int
    failed: int
    # A failed apply may still have written, so it counts; a skip does not.
    wrote: bool

    @model_validator(mode="after")
    def _check_wrote(self) -> RunSummary:
        if self.wrote != (self.succeeded + self.failed > 0):
            raise ValueError("wrote must be true exactly when an apply was attempted")
        return self

    @classmethod
    def from_result(cls, result: DriftResponseResult) -> RunSummary:
        reconcile = result.reconcile
        succeeded = reconcile.succeeded if reconcile else 0
        failed = reconcile.failed if reconcile else 0
        return cls(
            total=result.scan.total,
            clean=result.scan.clean,
            drifted=result.scan.drifted,
            errored=result.scan.errored,
            remediated=len(result.remediated),
            succeeded=succeeded,
            failed=failed,
            wrote=succeeded + failed > 0,
        )


class ScheduledRunInfo(BaseModel):
    """One recent firing of a drift schedule."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str
    scheduled_at: datetime
    started_at: datetime
    status: str
    # Completed runs only; None while running, after a failure, or when the
    # result could not be fetched.
    summary: RunSummary | None = None


class DriftScheduleView(BaseModel):
    """A drift schedule projected out of Temporal, so Presentation never sees client types."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schedule_id: str
    use_case_id: str
    interval: timedelta
    mode: DriftResponseMode
    paused: bool
    note: str | None = None
    next_runs: list[datetime] = Field(default_factory=list)
    recent_runs: list[ScheduledRunInfo] = Field(default_factory=list)
    running: list[str] = Field(default_factory=list)
    skipped_overlap: int = 0

    @field_serializer("interval", when_used="json")
    def _interval_seconds(self, interval: timedelta) -> int:
        return int(interval.total_seconds())
