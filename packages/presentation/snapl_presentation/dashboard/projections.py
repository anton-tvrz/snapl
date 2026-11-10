"""Pure projections from audit events to dashboard views (spec 010 research R4).

The ``AuditLog`` ABC has no "latest per device" query, and adding one would
widen a block contract for one reader (research R3). So the dashboard reads
events and folds them here. These functions touch no I/O, which keeps every
rule below testable from hand-built events.

Events are folded in ``timestamp`` order, never arrival order: a row can be
inserted well after its timestamp (research R3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote
from uuid import UUID

from snapl_orchestrator.models import AuditEventType, ScheduleChange, WorkflowReason
from snapl_presentation.dashboard.models import (
    BlockState,
    BlockStatus,
    Bucket,
    DeployOutcome,
    DeviceLane,
    DeviceView,
    DriftEpisode,
    DriftState,
    DriftTimeline,
    EpisodeClose,
    LinkEnd,
    Marker,
    NafBlock,
    Point,
    RepeatedRepairFailure,
    RunState,
    RunStep,
    RunSteps,
    RunView,
    ScanPoint,
    Series,
    SeriesName,
    Topology,
    TopologyLink,
    TopologyNode,
    Trigger,
    UnlinkedInterface,
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


# --- intent devices and their order (spec 011 research R6) ---------------------


@dataclass(frozen=True)
class IntentDevice:
    """What the dashboard reads of a device in intent: who it is and its role.

    The live Source of Truth supplies ``DesiredState``; an archive supplies only
    this much (spec 011 research R13). The projections take either.
    """

    id: UUID
    name: str
    role: str


def intent_devices(states: Sequence[DesiredState | IntentDevice]) -> list[IntentDevice]:
    return [
        s if isinstance(s, IntentDevice) else IntentDevice(s.device.id, s.device.name, s.device.role) for s in states
    ]


_ROLE_TIERS = {"spine": 0, "leaf": 1}
_DIGITS = re.compile(r"(\d+)")


def tier_key(role: str | None, name: str) -> tuple:
    """Sort key: spines, then leaves, then other roles by name, then no role.

    Inside a tier, names sort with their numbers compared as numbers, so
    ``leaf-2`` comes before ``leaf-10``.
    """
    natural = tuple((0, int(part), "") if part.isdigit() else (1, 0, part) for part in _DIGITS.split(name) if part)
    if role is None:
        return (3, "", natural)
    return (_ROLE_TIERS.get(role, 2), role if role not in _ROLE_TIERS else "", natural)


def events_until(events: Sequence[AuditEvent], at: datetime) -> list[AuditEvent]:
    """The events recorded at or before ``at``, in the order given.

    A filter, not a slice: rows land after their timestamp (010 research R3),
    so the sequence is not in time order.
    """
    return [event for event in events if event.timestamp <= at]


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
    states: Sequence[DesiredState | IntentDevice] | None,
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
        The devices, by role tier and then by name (``tier_key``).
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
        devices = [(device.id, device.name, device.role, True) for device in intent_devices(states)]
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
    return sorted(views, key=lambda view: tier_key(view.role, view.name))


# --- topology (spec 011 research R6) --------------------------------------------


def project_topology(states: Sequence[DesiredState]) -> Topology:
    """The use case's devices and the links their interfaces declare.

    Links exist only in intent: each interface names its peer. A link is one
    pair of ends. Where the two ends do not declare the same pair, each claim
    is shown as it is, with ``agreed`` false, and nothing is corrected.
    """
    devices = {state.device.name for state in states}
    # (end, end), ordered -> the device names that declared exactly this pair.
    declared: dict[tuple[tuple[str, str], tuple[str, str]], set[str]] = {}
    half: list[UnlinkedInterface] = []
    unlinked: list[UnlinkedInterface] = []
    for state in states:
        name = state.device.name
        for interface in state.interfaces:
            if interface.peer_device is None:
                continue
            claim = UnlinkedInterface(
                device=name,
                interface=interface.name,
                peer_device=interface.peer_device,
                peer_interface=interface.peer_interface,
            )
            if interface.peer_device == name or interface.peer_device not in devices:
                unlinked.append(claim)
            elif interface.peer_interface is None:
                half.append(claim)
            else:
                ends = sorted([(name, interface.name), (interface.peer_device, interface.peer_interface)])
                declared.setdefault((ends[0], ends[1]), set()).add(name)

    # A peer named without its interface is a link only if the peer declares it.
    for claim in half:
        end = (claim.device, claim.interface)
        if not any(end in pair and claim.peer_device in {pair[0][0], pair[1][0]} for pair in declared):
            unlinked.append(claim)

    links = []
    for (a, b), declarers in sorted(declared.items()):
        both = declarers == {a[0], b[0]}
        links.append(
            TopologyLink(
                a=LinkEnd(device=a[0], interface=a[1]),
                b=LinkEnd(device=b[0], interface=b[1]),
                declared_by="both" if both else ("a" if a[0] in declarers else "b"),
                agreed=both,
            )
        )
    nodes = sorted(
        (TopologyNode(device_id=s.device.id, name=s.device.name, role=s.device.role) for s in states),
        key=lambda node: tier_key(node.role, node.name),
    )
    return Topology(nodes=nodes, links=links, unlinked=sorted(unlinked, key=lambda u: (u.device, u.interface)))


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


# --- drift timeline (spec 011 research R10) ---------------------------------------

MAX_SCAN_POINTS = 20_000
_DRIFTED = {DriftState.MISSING, DriftState.UNDESIRED, DriftState.MISSING_AND_UNDESIRED, DriftState.DRIFTED}


class TimelineTooLongError(ValueError):
    """The range holds more scan points than one answer carries."""


def _scan_point(event: AuditEvent) -> ScanPoint:
    fields = _scan_fields(event, "")
    return ScanPoint(
        t=event.timestamp,
        state=fields["drift_state"],
        missing_paths=fields.get("missing_paths", []),
        undesired_paths=fields.get("undesired_paths", []),
        unclassified_paths=fields.get("unclassified_paths", []),
        error=fields.get("error"),
        scan_workflow_id=event.workflow_id,
    )


def _close_kind(run: _Run, trigger: Trigger) -> Literal["remediation", "reconcile", "deploy"]:
    if trigger is Trigger.SCHEDULE:
        return "remediation"
    return "reconcile" if run.start.payload.get("parent_workflow_id") else "deploy"


def _episodes(
    device_id: UUID,
    name: str,
    points: Sequence[ScanPoint],
    deploys: Sequence[tuple[datetime, _Run]],
    scheduled: set[str],
) -> list[DriftEpisode]:
    """One device's episodes from its scan points, oldest first."""
    episodes: list[DriftEpisode] = []
    opened: list[ScanPoint] = []
    for point in points:
        if not opened:
            if point.state in _DRIFTED:
                opened = [point]
            continue
        opened.append(point)
        if point.state is not DriftState.IN_SYNC:
            continue
        last_drifted = max(p.t for p in opened if p.state in _DRIFTED)
        closers = [(ended, run) for ended, run in deploys if last_drifted < ended < point.t]
        closed_by = None
        if closers:
            ended, run = max(closers, key=lambda closer: closer[0])
            trigger = _trigger(run, scheduled)
            closed_by = EpisodeClose(
                kind=_close_kind(run, trigger), workflow_id=run.workflow_id, trigger=trigger, ended_at=ended
            )
        episodes.append(_episode(device_id, name, opened, closed_by))
        opened = []
    if opened:
        episodes.append(_episode(device_id, name, opened, None))
    return episodes


def _episode(device_id: UUID, name: str, points: Sequence[ScanPoint], closed_by: EpisodeClose | None) -> DriftEpisode:
    closed = points[-1].t if points[-1].state is DriftState.IN_SYNC else None
    return DriftEpisode(
        device_id=device_id,
        name=name,
        opened_at=points[0].t,
        closed_at=closed,
        duration_s=int((closed - points[0].t).total_seconds()) if closed else None,
        # The closing scan's `in_sync` is how it ended, not a state it was in.
        states=list(dict.fromkeys(point.state for point in points if point.state is not DriftState.IN_SYNC)),
        scans=len(points),
        closed_by=closed_by,
    )


def project_timeline(
    events: Sequence[AuditEvent],
    states: Sequence[DesiredState | IntentDevice] | None,
    names: Mapping[UUID, str],
    start: datetime,
    end: datetime,
    *,
    max_points: int = MAX_SCAN_POINTS,
) -> DriftTimeline:
    """One lane of scan points per device over ``[start, end]``, and its drift episodes.

    Nothing recorded after ``end`` is used, so the answer is the timeline as of
    ``end``. An episode opens at the first scan that finds a device drifted and
    closes at the first later scan that finds it in sync. A scan that could not
    collect the device does neither. What closed an episode is the latest
    successful deploy of that device between its last drifted scan and its
    closing scan: only a deploy that targeted the device is evidence.

    Args:
        states: the devices in intent, or None when the Source of Truth could
            not be read. Then lanes are the devices the trail mentions.
        names: device names from the last successful SoT read.

    Raises:
        ValueError: ``start >= end``.
        TimelineTooLongError: more than ``max_points`` scan points in the range.
    """
    if start >= end:
        raise ValueError("start must be before end")
    known = events_until(events, end)
    by_device: dict[UUID, list[ScanPoint]] = {}
    for event in sorted(known, key=lambda e: e.timestamp):
        device_id = _device_id(event)
        if device_id is not None and _is_scan_record(event):
            by_device.setdefault(device_id, []).append(_scan_point(event))

    in_range = sum(1 for points in by_device.values() for point in points if point.t >= start)
    if in_range > max_points:
        raise TimelineTooLongError(
            f"the range holds {in_range} scan points; one answer carries at most {max_points}. Ask for a shorter range."
        )

    runs = _split_runs(known)
    scheduled = _scheduled_ids(runs, known)
    deploys: dict[UUID, list[tuple[datetime, _Run]]] = {}
    for run in runs:
        target = run.start.target_id
        succeeded = (
            run.end is not None
            and run.end.event_type is AuditEventType.WORKFLOW_TERMINATED
            and run.end.outcome == "success"
        )
        if run.workflow_type == "DeployIntent" and isinstance(target, UUID) and run.end is not None and succeeded:
            deploys.setdefault(target, []).append((run.end.timestamp, run))

    devices: list[tuple[UUID, str, bool]]
    if states is not None:
        intent = sorted(intent_devices(states), key=lambda device: tier_key(device.role, device.name))
        devices = [(device.id, device.name, True) for device in intent]
        in_intent = {device.id for device in intent}
        gone = sorted((names.get(d, str(d)), d) for d in by_device if d not in in_intent)
        devices += [(device_id, name, False) for name, device_id in gone]
    else:
        seen = sorted((names.get(d, str(d)), d) for d in by_device)
        devices = [(device_id, name, device_id in names) for name, device_id in seen]

    lanes = []
    episodes: list[DriftEpisode] = []
    for device_id, name, known_in_intent in devices:
        points = by_device.get(device_id, [])
        lanes.append(
            DeviceLane(
                device_id=device_id,
                name=name,
                in_intent=known_in_intent,
                points=[point for point in points if point.t >= start],
            )
        )
        episodes += _episodes(device_id, name, points, deploys.get(device_id, []), scheduled)

    episodes.sort(key=lambda episode: episode.opened_at)
    closed = [episode for episode in episodes if episode.closed_at is not None]
    return DriftTimeline(
        start=start,
        end=end,
        lanes=lanes,
        episodes=[e for e in episodes if e.closed_at is None or e.closed_at >= start],
        markers=_markers(known, start, end),
        latest_closed=max(closed, key=lambda e: e.closed_at or start, default=None),
        open_count=sum(1 for episode in episodes if episode.closed_at is None),
        first_event_at=min((event.timestamp for event in events), default=None),
    )


# --- run steps (spec 011 research R15) --------------------------------------------

# The one place a recorded step is given its NAF block (FR-026).
STEP_BLOCKS: dict[str, NafBlock] = {
    "fetch_desired_state": NafBlock.INTENT,
    "apply_config": NafBlock.EXECUTOR,
    "collect_drift_state": NafBlock.COLLECTOR,
    "detect_drift": NafBlock.OBSERVABILITY,
}
_LOOP_STEPS = [NafBlock.INTENT, NafBlock.EXECUTOR, NafBlock.COLLECTOR, NafBlock.OBSERVABILITY]
# Run types whose steps are recorded one by one, in loop order.
_STEPWISE = {"DeployIntent"}
# Reasons that mean a step failed. `collect_failed` covers a failed collect and
# a failed drift check alike, so the reason does not name the block: the block
# that failed is the first one with no record.
_STEP_FAILURES = {
    WorkflowReason.INTENT_UNAVAILABLE,
    WorkflowReason.DEVICE_NOT_FOUND,
    WorkflowReason.APPLY_FAILED,
    WorkflowReason.COLLECT_FAILED,
}


def run_events(events: Sequence[AuditEvent], workflow_id: str, started_at: datetime) -> list[AuditEvent]:
    """The events of the one run of ``workflow_id`` that started at ``started_at``.

    A workflow id can hold many runs (``deploy-intent-{device}``), so a run is
    the events from its start up to the next start under the same id.
    """
    of_id = sorted((e for e in events if e.workflow_id == workflow_id), key=lambda e: e.timestamp)
    collected: list[AuditEvent] = []
    for event in of_id:
        if event.event_type is AuditEventType.WORKFLOW_STARTED:
            if collected:
                break
            if event.timestamp == started_at:
                collected.append(event)
        elif collected:
            collected.append(event)
            if event.event_type in _RUN_END:
                break
    return collected


def _failure_text(end: AuditEvent) -> str | None:
    paths = end.payload.get("drift_paths")
    if paths:
        return f"{end.reason.value if end.reason else 'failed'}: {len(paths)} path(s) still differ"
    return _end_reason(end)


def project_steps(run: RunView, events: Sequence[AuditEvent]) -> RunSteps:
    """What each NAF block did in one run, as far as the audit trail records it.

    Only a recorded step marks a block done. Nothing records a step starting,
    so no block is ever "in progress". A failed deploy records its end with a
    reason and no failed step: the block that failed is the first with no
    record, or Observability when its check ran and found drift.

    Args:
        events: the run's own events (``run_events``).
    """
    ordered = sorted(events, key=lambda e: e.timestamp)
    steps = [
        RunStep(
            name=event.activity_name,
            block=STEP_BLOCKS.get(event.activity_name),
            outcome="failed" if event.event_type is AuditEventType.ACTIVITY_FAILED else "done",
            reason=(first_line(str(event.payload.get("error") or "")) or None)
            if event.event_type is AuditEventType.ACTIVITY_FAILED
            else None,
            t=event.timestamp,
        )
        for event in ordered
        if event.activity_name
        and event.event_type in {AuditEventType.ACTIVITY_COMPLETED, AuditEventType.ACTIVITY_FAILED}
    ]
    start = next((e for e in ordered if e.event_type is AuditEventType.WORKFLOW_STARTED), None)
    tail = [
        BlockState(block=NafBlock.ORCHESTRATOR, status=BlockStatus.DONE, t=start.timestamp if start else None),
        BlockState(block=NafBlock.PRESENTATION, status=BlockStatus.SURFACE),
    ]
    if run.workflow_type not in _STEPWISE:
        loop = [BlockState(block=block, status=BlockStatus.NOT_RECORDED) for block in _LOOP_STEPS]
        return RunSteps(run=run, recorded=False, steps=steps, blocks=[*loop, *tail])

    done = {step.block: step.t for step in steps if step.block is not None and step.outcome == "done"}
    end = next((e for e in ordered if e.event_type is AuditEventType.WORKFLOW_TERMINATED), None)
    failed: NafBlock | None = None
    if end is not None and end.outcome != "success":
        if end.reason is WorkflowReason.VERIFICATION_FAILED:
            failed = NafBlock.OBSERVABILITY
        elif end.reason in _STEP_FAILURES:
            failed = next((block for block in _LOOP_STEPS if block not in done), None)
    loop = []
    for block in _LOOP_STEPS:
        if block is failed and end is not None:
            loop.append(BlockState(block=block, status=BlockStatus.FAILED, reason=_failure_text(end), t=end.timestamp))
        elif block in done:
            loop.append(BlockState(block=block, status=BlockStatus.DONE, t=done[block]))
        else:
            loop.append(BlockState(block=block, status=BlockStatus.NOT_REACHED))
    return RunSteps(run=run, recorded=True, steps=steps, blocks=[*loop, *tail])
