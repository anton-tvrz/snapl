"""Pure projections from audit events to dashboard views (spec 010 research R4).

The ``AuditLog`` ABC has no "latest per device" query, and adding one would
widen a block contract for one reader (research R3). So the dashboard reads
events and folds them here. These functions touch no I/O, which keeps every
rule below testable from hand-built events.

Events are folded in ``timestamp`` order, never arrival order: a row can be
inserted well after its timestamp (research R3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote
from uuid import UUID

from snapl_orchestrator.models import AuditEventType, ScheduleChange, WorkflowReason
from snapl_presentation.dashboard.models import (
    Bucket,
    DeployOutcome,
    DeviceView,
    DriftState,
    Marker,
    Point,
    RepeatedRepairFailure,
    RunState,
    RunView,
    Series,
    SeriesName,
    Trigger,
)
from snapl_presentation.exceptions import first_line

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from snapl_intent.models import DesiredState
    from snapl_orchestrator.models import AuditEvent
    from snapl_orchestrator.worker.client import RunningWorkflowInfo

# Undesired config is reported, never removed automatically (#65).
UNDESIRED_NOTE = "Config on the device but not in intent is not removed automatically (#65)."

_DEPLOY_TERMINAL = {AuditEventType.WORKFLOW_TERMINATED, AuditEventType.WORKFLOW_CANCELLED}


def _device_id(event: AuditEvent) -> UUID | None:
    """The device an event is about, or None for a use-case-level event."""
    return event.target_id if isinstance(event.target_id, UUID) else None


def _is_scan_record(event: AuditEvent) -> bool:
    """A ScanDrift per-device record (``scan_drift._audit_device``).

    DeployIntent also writes ``detect_drift`` against the device, for its own
    post-apply check, with an empty payload. That is not a scan of the fabric.
    """
    return (
        event.workflow_type == "ScanDrift"
        and event.activity_name == "detect_drift"
        and event.event_type in {AuditEventType.ACTIVITY_COMPLETED, AuditEventType.ACTIVITY_FAILED}
        and _device_id(event) is not None
    )


def _is_deploy_end(event: AuditEvent) -> bool:
    return event.workflow_type == "DeployIntent" and event.event_type in _DEPLOY_TERMINAL


def _scan_fields(event: AuditEvent | None, name: str) -> dict:
    """The DeviceView fields one scan record implies."""
    if event is None:
        return {"drift_state": DriftState.NEVER_SCANNED}
    base = {"last_scanned_at": event.timestamp, "last_scan_workflow_id": event.workflow_id}
    payload = event.payload
    if event.event_type is AuditEventType.ACTIVITY_FAILED:
        return {**base, "drift_state": DriftState.ERROR, "error": payload.get("error") or "scan failed"}
    paths: list[str] = list(payload.get("paths") or [])
    if not paths:
        return {**base, "drift_state": DriftState.IN_SYNC}
    return {**base, **_drift_fields(paths, payload, name)}


def _drift_fields(paths: list[str], payload: dict, name: str) -> dict:
    """Classify a drifted scan by direction (FR-012a).

    Reconcile is suggested whenever intent may be missing from the device,
    including a pre-FR-012a scan whose direction is unknown. Undesired config is
    not removed by reconcile (#65), so it gets a note and no command.
    """
    reconcile = f"snapl reconcile {name}"
    if "undesired_paths" not in payload:
        # Recorded before FR-012a: the direction is unknown, so do not guess it.
        return {"drift_state": DriftState.DRIFTED, "unclassified_paths": paths, "suggested_command": reconcile}
    undesired_set = set(payload["undesired_paths"])
    missing = [path for path in paths if path not in undesired_set]
    undesired = [path for path in paths if path in undesired_set]
    if not undesired:
        return {"drift_state": DriftState.MISSING, "missing_paths": missing, "suggested_command": reconcile}
    if not missing:
        return {"drift_state": DriftState.UNDESIRED, "undesired_paths": undesired, "note": UNDESIRED_NOTE}
    return {
        "drift_state": DriftState.MISSING_AND_UNDESIRED,
        "missing_paths": missing,
        "undesired_paths": undesired,
        "suggested_command": reconcile,
        "note": UNDESIRED_NOTE,
    }


def _deploy_outcome(event: AuditEvent | None) -> DeployOutcome | None:
    if event is None:
        return None
    return DeployOutcome(
        workflow_id=event.workflow_id,
        success=event.outcome == "success",
        reason=event.reason,
        ended_at=event.timestamp,
    )


def project_devices(
    events: Sequence[AuditEvent],
    states: Sequence[DesiredState] | None,
    names: Mapping[UUID, str],
) -> list[DeviceView]:
    """Each device's drift state, last scan and last deploy.

    Args:
        events: audit events, in any order.
        states: the Source of Truth's devices for the use case, or None when it
            could not be read. Then the list is the degraded one (research
            R13): every device the audit trail mentions, with no role.
        names: device names from the last successful SoT read in this process,
            used to name devices in the degraded list.

    Returns:
        The devices, spines first and then by name.
    """
    last_scan: dict[UUID, AuditEvent] = {}
    last_deploy: dict[UUID, AuditEvent] = {}
    seen: set[UUID] = set()
    for event in sorted(events, key=lambda e: e.timestamp):
        device_id = _device_id(event)
        if device_id is None:
            continue
        seen.add(device_id)
        if _is_scan_record(event):
            last_scan[device_id] = event
        elif _is_deploy_end(event):
            last_deploy[device_id] = event

    devices: list[tuple[UUID, str, str | None, bool]]
    if states is not None:
        devices = [(s.device.id, s.device.name, s.device.role, True) for s in states]
    else:
        devices = [(d, names.get(d, str(d)), None, d in names) for d in seen]

    views = [
        DeviceView(
            device_id=device_id,
            name=name,
            role=role,
            in_intent=in_intent,
            last_deploy=_deploy_outcome(last_deploy.get(device_id)),
            **_scan_fields(last_scan.get(device_id), name),
        )
        for device_id, name, role, in_intent in devices
    ]
    return sorted(views, key=lambda view: (view.role != "spine", view.name))


# --- runs (research R5) --------------------------------------------------------

_RUN_END = {
    AuditEventType.WORKFLOW_TERMINATED,
    AuditEventType.WORKFLOW_CANCELLED,
}


@dataclass(frozen=True)
class RunFilters:
    # Empty means every type.
    workflow_types: frozenset[str] = frozenset()
    # A device name, matched against the run's target.
    device: str | None = None


@dataclass
class _Run:
    workflow_id: str
    workflow_type: str
    start: AuditEvent
    end: AuditEvent | None = None
    actor: str | None = None
    # Another run started under the same id before this one recorded its end.
    superseded: bool = False


def _split_runs(events: Sequence[AuditEvent]) -> list[_Run]:
    """One run per WORKFLOW_STARTED, closed by the next terminal event of its id.

    ``deploy-intent-{device}`` holds many runs. A run whose worker died never
    records its end; the next start under its id closes it as unfinished. An
    event whose start row has not arrived yet is left out until it does: rows
    land after their timestamp (research R3), and inventing a start time would
    be a guess.
    """
    runs: list[_Run] = []
    open_runs: dict[str, _Run] = {}
    for event in sorted(events, key=lambda e: e.timestamp):
        if event.event_type is AuditEventType.SCHEDULE_CHANGED:
            continue
        current = open_runs.get(event.workflow_id)
        if event.event_type is AuditEventType.WORKFLOW_STARTED:
            if current is not None:
                current.superseded = True
            current = _Run(event.workflow_id, event.workflow_type, event, actor=event.actor)
            open_runs[event.workflow_id] = current
            runs.append(current)
            continue
        if current is None:
            continue
        current.actor = current.actor or event.actor
        if event.event_type in _RUN_END:
            current.end = event
            del open_runs[event.workflow_id]
    return runs


def _scheduled_ids(runs: Sequence[_Run], events: Sequence[AuditEvent]) -> set[str]:
    """Workflow ids a schedule started: each DriftResponse and its children.

    A DriftResponse names its children in its use-case payload, but only once
    it ends, and never when it fails. The children's ids are fixed by
    ``drift_response.py`` (``{wf}-scan``, ``{wf}-reconcile``), so they are
    derived from the parent's id instead.
    """
    responses = {run.workflow_id for run in runs if run.workflow_type == "DriftResponse"}
    scheduled = set(responses)
    for workflow_id in responses:
        scheduled |= {f"{workflow_id}-scan", f"{workflow_id}-reconcile"}
    for event in events:
        if event.workflow_type == "DriftResponse" and event.event_type is AuditEventType.DRIFT_RESPONSE:
            for key in ("scan_workflow_id", "reconcile_workflow_id"):
                if event.payload.get(key):
                    scheduled.add(event.payload[key])
    return scheduled


def _trigger(run: _Run, scheduled: set[str]) -> Trigger:
    if run.workflow_type != "DeployIntent":
        return Trigger.SCHEDULE if run.workflow_id in scheduled else Trigger.OPERATOR
    # A deploy's trigger is its parent's, and only the parent id is evidence:
    # a deploy that merely started during a scheduled reconcile may be an
    # operator's (research R5).
    if "parent_workflow_id" not in run.start.payload:
        return Trigger.UNKNOWN  # recorded before FR-016a
    parent = run.start.payload["parent_workflow_id"]
    if parent is None:
        return Trigger.OPERATOR
    if parent in scheduled:
        return Trigger.SCHEDULE
    if parent.endswith("-reconcile"):
        # Shaped like a scheduled reconcile, whose run is not in the trail.
        return Trigger.UNKNOWN
    return Trigger.OPERATOR


def _target(target_id: UUID | str | None, names: Mapping[UUID, str]) -> str | None:
    if isinstance(target_id, UUID):
        return names.get(target_id, str(target_id))
    return target_id


def _end_reason(end: AuditEvent | None) -> str | None:
    if end is None or end.reason in (None, WorkflowReason.SUCCEEDED):
        return None
    detail = end.payload.get("detail") or end.payload.get("error")
    code = end.reason.value if end.reason else ""
    return f"{code}: {first_line(str(detail))}" if detail else code


def web_ui_url(ui_address: str, namespace: str, workflow_id: str) -> str:
    """The Temporal Web UI page of a workflow id (research R12).

    It lists every run of the id: the audit trail stores no run id to link one.
    """
    return f"{ui_address.rstrip('/')}/namespaces/{quote(namespace, safe='')}/workflows/{quote(workflow_id, safe='')}"


def _state(run: _Run, running_ids: Collection[str] | None) -> RunState:
    if run.end is not None:
        if run.end.event_type is AuditEventType.WORKFLOW_CANCELLED:
            return RunState.CANCELLED
        return RunState.SUCCEEDED if run.end.outcome == "success" else RunState.FAILED
    if not run.superseded and running_ids is not None and run.workflow_id in running_ids:
        return RunState.RUNNING
    return RunState.UNFINISHED


def project_runs(
    events: Sequence[AuditEvent],
    running_ids: Collection[str] | None,
    now: datetime,
    *,
    names: Mapping[UUID, str],
    ui_address: str,
    namespace: str,
    limit: int = 50,
    filters: RunFilters = RunFilters(),  # noqa: B008 — frozen, so sharing it is safe
) -> list[RunView]:
    """Recent workflow runs, newest first.

    Args:
        running_ids: workflow ids Temporal lists as running, or None when
            Temporal could not be asked. A run with no end record is then
            ``unfinished``: nothing confirms it is still going.
    """
    runs = _split_runs(events)
    scheduled = _scheduled_ids(runs, events)
    views: list[RunView] = []
    for run in sorted(runs, key=lambda r: r.start.timestamp, reverse=True):
        target = _target(run.start.target_id, names)
        if filters.workflow_types and run.workflow_type not in filters.workflow_types:
            continue
        if filters.device is not None and target != filters.device:
            continue
        state = _state(run, running_ids)
        started = run.start.timestamp
        ended = run.end.timestamp if run.end else None
        if ended is not None:
            duration = int((ended - started).total_seconds())
        elif state is RunState.RUNNING:
            duration = int((now - started).total_seconds())
        else:
            duration = None
        views.append(
            RunView(
                workflow_id=run.workflow_id,
                workflow_type=run.workflow_type,
                target=target,
                started_at=started,
                ended_at=ended,
                duration_s=duration,
                state=state,
                reason=_end_reason(run.end),
                trigger=_trigger(run, scheduled),
                actor=run.actor,
                web_ui_url=web_ui_url(ui_address, namespace, run.workflow_id),
            )
        )
        if len(views) >= limit:
            break
    return views


def project_running(
    running: Sequence[RunningWorkflowInfo],
    runs: Sequence[RunView],
    now: datetime,
    *,
    ui_address: str,
    namespace: str,
) -> list[RunView]:
    """What Temporal says is running, newest first.

    A run the audit trail already holds is shown as projected from it. One the
    trail has not recorded yet is shown from Temporal alone: no target, and a
    trigger of ``unknown``, since nothing recorded says what started it.
    """
    from_trail = {run.workflow_id: run for run in runs if run.state is RunState.RUNNING}
    views = []
    for info in running:
        if info.workflow_id in from_trail:
            views.append(from_trail[info.workflow_id])
            continue
        views.append(
            RunView(
                workflow_id=info.workflow_id,
                workflow_type=info.workflow_type,
                target=None,
                started_at=info.start_time,
                duration_s=int((now - info.start_time).total_seconds()),
                state=RunState.RUNNING,
                trigger=Trigger.UNKNOWN,
                web_ui_url=web_ui_url(ui_address, namespace, info.workflow_id),
            )
        )
    return sorted(views, key=lambda view: view.started_at, reverse=True)


# --- schedule (research R7) ----------------------------------------------------


def project_repeated_failures(
    events: Sequence[AuditEvent],
    names: Mapping[UUID, str],
    *,
    use_case: str,
    threshold: int = 3,
) -> list[RepeatedRepairFailure]:
    """Devices whose scheduled repair has failed ``threshold`` times in a row.

    Walking each device's scheduled repairs newest first: ``failed`` counts,
    ``succeeded`` stops the count, and ``unknown`` or ``skipped`` neither count
    nor stop it. A run that did not attempt the device, because it was clean or
    excluded, says nothing about whether a repair works, so it does not reset.
    """
    attempts: dict[UUID, list[AuditEvent]] = {}
    for event in events:
        device_id = _device_id(event)
        if (
            device_id is not None
            and event.event_type is AuditEventType.DRIFT_RESPONSE
            and event.payload.get("decision") == "remediated"
        ):
            attempts.setdefault(device_id, []).append(event)

    failures = []
    for device_id, records in attempts.items():
        count = 0
        last_failed_at: datetime | None = None
        for record in sorted(records, key=lambda e: e.timestamp, reverse=True):
            result = record.payload.get("result")
            if result == "succeeded":
                break
            if result == "failed":
                count += 1
                last_failed_at = last_failed_at or record.timestamp
        if count >= threshold and last_failed_at is not None:
            failures.append(
                RepeatedRepairFailure(
                    device_id=device_id,
                    name=names.get(device_id, str(device_id)),
                    consecutive_failures=count,
                    last_failed_at=last_failed_at,
                    suggested_command=f"snapl schedule mode report --use-case {use_case}",
                )
            )
    return sorted(failures, key=lambda failure: failure.name)


def latest_pause(events: Sequence[AuditEvent], *, use_case: str) -> tuple[str | None, datetime] | None:
    """Who paused the use case's schedule most recently, and when."""
    pauses = [
        event
        for event in events
        if event.event_type is AuditEventType.SCHEDULE_CHANGED
        and event.target_id == use_case
        and event.payload.get("change") == ScheduleChange.PAUSED
    ]
    if not pauses:
        return None
    latest = max(pauses, key=lambda e: e.timestamp)
    return latest.actor, latest.timestamp


# --- history (research R8) -----------------------------------------------------

MAX_BUCKETS = 2000

_BUCKET_SIZE = {
    Bucket.FIVE_MINUTES: timedelta(minutes=5),
    Bucket.HOUR: timedelta(hours=1),
    Bucket.DAY: timedelta(days=1),
    Bucket.WEEK: timedelta(weeks=1),
}
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# Weeks start on Monday; the epoch was a Thursday.
_WEEK_ORIGIN = datetime(1970, 1, 5, tzinfo=UTC)

_PER_SCAN = {SeriesName.DRIFTED_DEVICES, SeriesName.UNDESIRED_PATHS}


def bucket_starts(start: datetime, end: datetime, bucket: Bucket) -> list[datetime]:
    """UTC-aligned bucket starts covering ``[start, end)``.

    Raises:
        ValueError: the range holds more than 2,000 buckets.
    """
    size = _BUCKET_SIZE[bucket]
    origin = _WEEK_ORIGIN if bucket is Bucket.WEEK else _EPOCH
    first = origin + ((start - origin) // size) * size
    count = -((first - end) // size)
    if count > MAX_BUCKETS:
        raise ValueError(f"{count:,} {bucket.value} buckets requested; at most {MAX_BUCKETS:,} per request")
    return [first + index * size for index in range(count)]


def _is_scan_end(event: AuditEvent) -> bool:
    return event.workflow_type == "ScanDrift" and event.event_type in _RUN_END


def _scan_points(events: Sequence[AuditEvent], name: SeriesName, start: datetime, end: datetime) -> list[Point]:
    """One point per completed scan, at its end, so a spike is not averaged away."""
    undesired: dict[str, int | None] = {}
    if name is SeriesName.UNDESIRED_PATHS:
        for event in events:
            if not _is_scan_record(event) or not event.payload.get("paths"):
                continue
            # A record from before FR-012a makes its whole scan's count unknown.
            count = len(event.payload["undesired_paths"]) if "undesired_paths" in event.payload else None
            previous = undesired.get(event.workflow_id, 0)
            undesired[event.workflow_id] = None if previous is None or count is None else previous + count
    points = []
    for event in events:
        if not (_is_scan_end(event) and event.outcome == "success" and start <= event.timestamp < end):
            continue
        if name is SeriesName.DRIFTED_DEVICES:
            value = event.payload.get("drifted")
        else:
            value = undesired.get(event.workflow_id, 0)
        points.append(Point(t=event.timestamp, value=value))
    return sorted(points, key=lambda point: point.t)


def _counted_outcome(event: AuditEvent, name: SeriesName) -> str | None:
    """``succeeded`` or ``failed`` when the event counts toward the series."""
    if name is SeriesName.DEPLOYS:
        if event.workflow_type == "DeployIntent" and event.event_type is AuditEventType.WORKFLOW_TERMINATED:
            return "succeeded" if event.outcome == "success" else "failed"
        return None
    if (
        event.event_type is AuditEventType.DRIFT_RESPONSE
        and _device_id(event) is not None
        and event.payload.get("decision") == "remediated"
        and event.payload.get("result") in ("succeeded", "failed")
    ):
        return str(event.payload["result"])
    return None


def _counted_points(
    events: Sequence[AuditEvent], name: SeriesName, starts: list[datetime], bucket: Bucket
) -> list[Point]:
    """Counts per bucket by outcome. A bucket where nothing ran, no scan and no
    deploy, has no data: its counts are None, not 0 (US3 scenario 3)."""
    if not starts:
        return []
    first, size = starts[0], _BUCKET_SIZE[bucket]
    counts: dict[int, dict[str, int]] = {}
    active: set[int] = set()
    for event in events:
        index = (event.timestamp - first) // size
        if not 0 <= index < len(starts):
            continue
        outcome = _counted_outcome(event, name)
        if outcome is not None:
            per_bucket = counts.setdefault(index, {"succeeded": 0, "failed": 0})
            per_bucket[outcome] += 1
            active.add(index)
        elif _is_scan_end(event) or _is_deploy_end(event):
            active.add(index)
    empty: dict[str, int | None] = {"succeeded": None, "failed": None}
    return [
        Point(
            t=bucket_start,
            by_outcome=dict(counts.get(index, {"succeeded": 0, "failed": 0})) if index in active else dict(empty),
        )
        for index, bucket_start in enumerate(starts)
    ]


def _markers(events: Sequence[AuditEvent], start: datetime, end: datetime) -> list[Marker]:
    """One per reconcile, and one per scheduled run that remediated."""
    runs = _split_runs(events)
    scheduled = _scheduled_ids(runs, events)
    markers = []
    for run in runs:
        at = run.start.timestamp
        if not start <= at < end:
            continue
        if run.workflow_type == "ReconcileDevices":
            kind: Literal["reconcile", "remediation"] = "reconcile"
        elif run.workflow_type == "DriftResponse" and run.start.payload.get("mode") == "remediate":
            kind = "remediation"
        else:
            continue
        markers.append(Marker(t=at, kind=kind, workflow_id=run.workflow_id, trigger=_trigger(run, scheduled)))
    return sorted(markers, key=lambda marker: marker.t)


def project_series(
    events: Sequence[AuditEvent],
    name: SeriesName,
    start: datetime,
    end: datetime,
    bucket: Bucket,
) -> Series:
    """One history series over ``[start, end)`` (research R8).

    ``drifted_devices`` and ``undesired_paths`` have a point per scan and
    ignore ``bucket``. ``deploys`` and ``repairs`` count per UTC-aligned
    bucket. The range is never clamped to the data: before the first event
    there are simply no points.

    Raises:
        ValueError: ``start >= end``, or more than 2,000 buckets.
    """
    if start >= end:
        raise ValueError("start must be before end")
    starts = bucket_starts(start, end, bucket)
    if name in _PER_SCAN:
        points = _scan_points(events, name, start, end)
    else:
        points = _counted_points(events, name, starts, bucket)
    return Series(
        name=name,
        start=start,
        end=end,
        bucket=None if name in _PER_SCAN else bucket,
        points=points,
        markers=_markers(events, start, end),
        first_event_at=min((event.timestamp for event in events), default=None),
    )
