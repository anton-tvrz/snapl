"""The dashboard's response contract, v1 (spec 010 data-model.md).

Every response is an ``Envelope`` holding one or more ``View``s. A view says
which source it came from, whether that source answered, and how old its data
is. A source failure is a view with ``available=False``, never an HTTP error
(FR-005, FR-007). These models are the contract: the OpenAPI document is
generated from them, and spec 011's UI builds on it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID  # noqa: TC003 — pydantic resolves field types at runtime

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from snapl_orchestrator.models import DriftScheduleView, WorkflowReason  # noqa: TC001 — runtime field types


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


# Times must carry an offset on the way in, and leave in UTC (data-model.md).
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]

Suite = Literal["e2e", "integration"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- Envelope and source status ----------------------------------------------


class SourceName(StrEnum):
    AUDIT = "audit"
    TEMPORAL = "temporal"
    SOT = "sot"
    CI = "ci"
    LAB_RUNS = "lab_runs"
    # Spec 012. Feeds /sources and the metrics routes, never the overview.
    PROMETHEUS = "prometheus"


class View[T](_Model):
    """One source's answer, with its provenance and age (FR-005, FR-031)."""

    source: SourceName
    # False when the last attempt to read the source failed.
    available: bool
    # When `data` was read. None only when the source never answered.
    fetched_at: UtcDatetime | None = None
    # True when `data` is from an earlier successful read than the last attempt.
    stale: bool = False
    reason: str | None = None
    # A command to run, e.g. `uv run invoke dev.deps`.
    remedy: str | None = None
    data: T | None = None

    @model_validator(mode="after")
    def _check(self) -> View[T]:
        if not self.available and not (self.reason and self.remedy):
            raise ValueError("an unavailable view needs a reason and remedy")
        if self.stale and (self.data is None or self.fetched_at is None):
            raise ValueError("a stale view needs the data it is stale about and when it was fetched")
        return self


class Envelope(_Model):
    contract_version: Literal["1"] = "1"
    use_case: str
    generated_at: UtcDatetime


# --- Devices (US1) -------------------------------------------------------------


class DriftState(StrEnum):
    IN_SYNC = "in_sync"
    MISSING = "missing"
    UNDESIRED = "undesired"
    MISSING_AND_UNDESIRED = "missing_and_undesired"
    # A scan recorded before FR-012a: the direction of its paths is unknown.
    DRIFTED = "drifted"
    ERROR = "error"
    NEVER_SCANNED = "never_scanned"


_PATH_LISTS: dict[str, set[DriftState]] = {
    "missing_paths": {DriftState.MISSING, DriftState.MISSING_AND_UNDESIRED},
    "undesired_paths": {DriftState.UNDESIRED, DriftState.MISSING_AND_UNDESIRED},
    "unclassified_paths": {DriftState.DRIFTED},
}


class DeployOutcome(_Model):
    workflow_id: str
    success: bool
    reason: WorkflowReason | None = None
    ended_at: UtcDatetime


class DeviceView(_Model):
    device_id: UUID
    name: str
    # None only in the degraded, audit-only view (research R13).
    role: str | None
    # False for a device seen in the audit trail but no longer in the SoT.
    in_intent: bool
    drift_state: DriftState
    missing_paths: list[str] = Field(default_factory=list)
    undesired_paths: list[str] = Field(default_factory=list)
    unclassified_paths: list[str] = Field(default_factory=list)
    error: str | None = None
    last_scanned_at: UtcDatetime | None = None
    last_scan_workflow_id: str | None = None
    last_deploy: DeployOutcome | None = None
    # `snapl reconcile <name>` for forward drift. None for undesired-only drift,
    # whose removal is not automatic (#65); `note` says so.
    suggested_command: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _check(self) -> DeviceView:
        for field, states in _PATH_LISTS.items():
            if getattr(self, field) and self.drift_state not in states:
                raise ValueError(f"{field} must be empty for drift state {self.drift_state.value}")
        if self.drift_state is DriftState.ERROR and not self.error:
            raise ValueError("an errored device needs its error")
        if (self.last_scanned_at is None) != (self.drift_state is DriftState.NEVER_SCANNED):
            raise ValueError("last_scanned_at must be empty exactly when the device is never_scanned")
        return self


# --- Runs and schedule (US2) ---------------------------------------------------


class Trigger(StrEnum):
    SCHEDULE = "schedule"
    OPERATOR = "operator"
    # A DeployIntent recorded before FR-016a: its parent cannot be told (research R5).
    UNKNOWN = "unknown"


class RunState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # Started, no terminal event, and not running in Temporal.
    UNFINISHED = "unfinished"


class RunView(_Model):
    """One workflow run. Its identity is (workflow_id, started_at), because
    `deploy-intent-{device}` is reused across runs (research R5)."""

    workflow_id: str
    workflow_type: str
    # A device name if resolvable, else the use case or the raw target id.
    target: str | None
    started_at: UtcDatetime
    ended_at: UtcDatetime | None = None
    # For a running run: seconds so far.
    duration_s: int | None = None
    state: RunState
    reason: str | None = None
    trigger: Trigger
    # Only when the audit trail records one.
    actor: str | None = None
    web_ui_url: str


class RepeatedRepairFailure(_Model):
    device_id: UUID
    name: str
    consecutive_failures: int = Field(ge=3)
    last_failed_at: UtcDatetime
    # `snapl schedule mode report --use-case <uc>`
    suggested_command: str


class ScheduleStatus(_Model):
    exists: bool
    schedule: DriftScheduleView | None = None
    # The actor and time of the latest SCHEDULE_CHANGED that paused it.
    paused_by: str | None = None
    paused_at: UtcDatetime | None = None
    repeated_failures: list[RepeatedRepairFailure] = Field(default_factory=list)
    # `snapl schedule create --use-case <uc>` when no schedule exists.
    suggested_command: str | None = None

    @model_validator(mode="after")
    def _check(self) -> ScheduleStatus:
        if self.exists != (self.schedule is not None):
            raise ValueError("schedule must be set exactly when one exists")
        return self


class WorkerStatus(_Model):
    pollers: int
    task_queue: str


# --- History (US3) -------------------------------------------------------------


class Bucket(StrEnum):
    FIVE_MINUTES = "5m"
    HOUR = "1h"
    DAY = "1d"
    WEEK = "1w"


class SeriesName(StrEnum):
    DRIFTED_DEVICES = "drifted_devices"
    UNDESIRED_PATHS = "undesired_paths"
    DEPLOYS = "deploys"
    REPAIRS = "repairs"


class Point(_Model):
    # The scan time for per-scan series, the bucket start for counted series.
    t: UtcDatetime
    # Per-scan series.
    value: int | None = None
    # Counted series: `succeeded` and `failed`, each None in an empty bucket.
    by_outcome: dict[str, int | None] | None = None


class Marker(_Model):
    t: UtcDatetime
    kind: Literal["reconcile", "remediation"]
    workflow_id: str
    trigger: Trigger


class Series(_Model):
    name: SeriesName
    start: UtcDatetime
    end: UtcDatetime
    # None for per-scan series.
    bucket: Bucket | None
    points: list[Point]
    markers: list[Marker]
    # The oldest audit event, so a UI can say "history starts here".
    first_event_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _check(self) -> Series:
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


# --- CI (US4) ------------------------------------------------------------------


class JobResult(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    IN_PROGRESS = "in_progress"
    QUEUED = "queued"
    NEUTRAL = "neutral"


class CiJob(_Model):
    name: str
    result: JobResult
    url: str


class CiTarget(_Model):
    kind: Literal["main", "pull_request"]
    number: int | None = None
    title: str | None = None
    head_sha: str
    # The result of the quality gate check run.
    gate: JobResult
    jobs: list[CiJob]
    run_url: str | None = None
    # `main` only, from Codecov.
    coverage_pct: float | None = None


class CiStatus(_Model):
    # owner/name
    repo: str
    authenticated: bool
    rate_limit_remaining: int | None = None
    # `main` first, then PRs by number.
    targets: list[CiTarget]
    # Open PRs left out to stay inside the unauthenticated rate limit (research R9).
    omitted_prs: int = 0


# --- Lab runs (US4) ------------------------------------------------------------


class LabRunStatus(StrEnum):
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    # Derived: still `running` on disk, and its pid is gone.
    INCOMPLETE = "incomplete"
    UNREADABLE = "unreadable"


class LabRun(_Model):
    # The folder name.
    id: str
    suite: Suite
    status: LabRunStatus
    started_at: UtcDatetime | None = None
    ended_at: UtcDatetime | None = None
    commit: str | None = None
    tree: str | None = None
    branch: str | None = None
    dirty: bool | None = None
    device_image: str | None = None
    passed: int | None = None
    failed: int | None = None
    skipped: int | None = None
    errors: int | None = None
    problem: str | None = None

    @model_validator(mode="after")
    def _check(self) -> LabRun:
        if self.status is LabRunStatus.UNREADABLE and not self.problem:
            raise ValueError("an unreadable run needs its problem")
        return self


class LabEvidence(_Model):
    # The newest readable run per suite.
    latest: dict[Suite, LabRun]
    commits_on_main_since: dict[Suite, int | None]
    # Only for suites whose count is known (research R10).
    matched_by: dict[Suite, Literal["ancestor", "tree"]] = Field(default_factory=dict)
    # Only for suites whose count is None.
    commits_unknown_reason: dict[Suite, str] = Field(default_factory=dict)
    # Newest first, at most 20.
    runs: list[LabRun] = Field(max_length=20)


# --- Overview (the MVP page's poll) ----------------------------------------


class Overview(Envelope):
    devices: View[list[DeviceView]]
    runs: View[list[RunView]]
    # State `running` only, from Temporal.
    running: View[list[RunView]]
    schedule: View[ScheduleStatus]
    worker: View[WorkerStatus]
    ci: View[CiStatus]
    lab: View[LabEvidence]


# --- Links (#173) ---------------------------------------------------------------


class LinkGroup(StrEnum):
    STACK = "stack"
    GITHUB = "github"
    API = "api"
    DOCS = "docs"


class Link(_Model):
    group: LinkGroup
    label: str
    # Absolute, or a path on this dashboard (the API links).
    url: str


class LinksResponse(Envelope):
    links: list[Link]
    # Set when some links are left out, saying which and why.
    reason: str | None = None
    remedy: str | None = None


# --- Metrics (spec 012) ----------------------------------------------------------


class MetricsViewName(StrEnum):
    """The named PromQL views. A request picks one; it never supplies a query (FR-011)."""

    WORKFLOW_DURATION = "workflow-duration"
    GNMI_DURATION = "gnmi-duration"
    GNMI_ERRORS = "gnmi-errors"
    WORKER_LAG = "worker-lag"
    WORKER_UP = "worker-up"
    SOURCE_HEALTH = "source-health"


MetricUnit = Literal["ms", "per_second", "up"]


class MetricPoint(_Model):
    t: UtcDatetime
    # None for NaN or +/-Inf: JSON has no such numbers, and the page shows a gap.
    value: float | None


class MetricSeries(_Model):
    # Only the view's series labels: __name__, job and instance are dropped.
    labels: dict[str, str]
    points: list[MetricPoint]


class MetricsData(_Model):
    view: MetricsViewName
    unit: MetricUnit
    start: UtcDatetime
    end: UtcDatetime
    # Seconds, as used. It may be coarser than requested (FR-013).
    step: int = Field(gt=0)
    # Empty when there is no data: a note, not an error.
    series: list[MetricSeries] = Field(default_factory=list)
    note: str | None = None


class MetricsResponse(Envelope):
    metrics: View[MetricsData]
