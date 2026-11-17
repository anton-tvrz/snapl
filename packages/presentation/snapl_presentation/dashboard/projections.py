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
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import compress, repeat
from operator import attrgetter, eq, is_, itemgetter, le, methodcaller
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote
from uuid import UUID

from snapl_orchestrator.models import AuditEventType, ScheduleChange, WorkflowReason
from snapl_presentation.dashboard.models import (
    BlockState,
    BlockStatus,
    Bucket,
    DeployOutcome,
    DeviceDriftSummary,
    DeviceLane,
    DeviceView,
    DriftEpisode,
    DriftState,
    DriftSummary,
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
    from collections.abc import Collection, Iterable, Mapping, Sequence

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


# Filters that run for every event are written as `compress` over `map`s of
# C functions: under coverage, a comprehension costs a traced step per event,
# and CI measures coverage (#287). `operator.eq` rather than `"x".__eq__`, which
# answers NotImplemented, a true value, for None.
_BY_TIME = attrgetter("timestamp")
_ACTIVITY = attrgetter("activity_name")
_WORKFLOW_TYPE = attrgetter("workflow_type")
_WORKFLOW_ID = attrgetter("workflow_id")
_EVENT_TYPE = attrgetter("event_type")
_TARGET = attrgetter("target_id")
_PAYLOAD = attrgetter("payload")


def _where(events: Iterable[AuditEvent], keep: Iterable[object]) -> list[AuditEvent]:
    return list(compress(events, keep))


def _flow(events: Sequence[AuditEvent]) -> list[AuditEvent]:
    """The workflow-level events: those of no activity. Step records are most of the trail."""
    return _where(events, map(is_, map(_ACTIVITY, events), repeat(None)))


def events_until(events: Sequence[AuditEvent], at: datetime) -> list[AuditEvent]:
    """The events recorded at or before ``at``, in the order given.

    A filter, not a slice: rows land after their timestamp (010 research R3),
    so the sequence is not in time order.
    """
    return _where(events, map(le, map(_BY_TIME, events), repeat(at)))


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


def _split_runs(events: Sequence[AuditEvent], *, ordered: bool = False) -> list[_Run]:
    """One run per WORKFLOW_STARTED, closed by the next terminal event of its id.

    ``deploy-intent-{device}`` holds many runs. A run whose worker died never
    records its end; the next start under its id closes it as unfinished. An
    event whose start row has not arrived yet is left out until it does: rows
    land after their timestamp (research R3), and inventing a start time would
    be a guess.
    """
    runs: list[_Run] = []
    open_runs: dict[str, _Run] = {}
    for event in events if ordered else sorted(events, key=_BY_TIME):
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
    for event in _where(events, map(eq, map(_WORKFLOW_TYPE, events), repeat("DriftResponse"))):
        if event.event_type is AuditEventType.DRIFT_RESPONSE:
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
# Series whose points are not buckets: one per scan, or one per closed episode.
_UNBUCKETED = _PER_SCAN | {SeriesName.IN_SYNC_DEVICES, SeriesName.OPEN_DRIFTS, SeriesName.REPAIR_DURATION}


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


def _scan_points(
    events: Sequence[AuditEvent], flow: Sequence[AuditEvent], name: SeriesName, start: datetime, end: datetime
) -> list[Point]:
    """One point per completed scan, at its end, so a spike is not averaged away.

    ``flow`` is the workflow-level events. Filters that run for every event are
    kept on one line: under coverage each line costs a step per event.
    """
    ends = [e for e in flow if e.event_type in _RUN_END and e.workflow_type == "ScanDrift"]
    ends = sorted([e for e in ends if e.outcome == "success" and start <= e.timestamp < end], key=_BY_TIME)
    undesired: dict[str, int | None] = {}
    if name is SeriesName.UNDESIRED_PATHS:
        wanted = {e.workflow_id for e in ends}
        drifted = _where(events, map(wanted.__contains__, map(_WORKFLOW_ID, events)))
        drifted = [e for e in drifted if e.activity_name and e.payload.get("paths")]
        for event in drifted:
            if not _is_scan_record(event):
                continue
            # A record from before FR-012a makes its whole scan's count unknown.
            count = len(event.payload["undesired_paths"]) if "undesired_paths" in event.payload else None
            previous = undesired.get(event.workflow_id, 0)
            undesired[event.workflow_id] = None if previous is None or count is None else previous + count
    points = []
    for event in ends:
        if name is SeriesName.DRIFTED_DEVICES:
            value = event.payload.get("drifted")
        else:
            value = undesired.get(event.workflow_id, 0)
        points.append(Point(t=event.timestamp, value=value))
    return points


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


_COUNTED_TYPES = _RUN_END | _DEPLOY_TERMINAL | {AuditEventType.DRIFT_RESPONSE}


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
    # Only these types can count (`_counted_outcome`) or end a scan or a deploy.
    for event in _where(events, map(_COUNTED_TYPES.__contains__, map(_EVENT_TYPE, events))):
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


def _markers_of(runs: Sequence[_Run], scheduled: set[str], start: datetime, end: datetime) -> list[Marker]:
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


def _intent_ids(states: Sequence[DesiredState | IntentDevice] | None, names: Mapping[UUID, str]) -> set[UUID] | None:
    """The devices in intent, by `project_devices`' rule; None when nothing says.

    With the Source of Truth down, the devices it last named are in intent. A
    server that has never read it knows no intent at all, and then every device
    the trail mentions counts rather than none.
    """
    if states is not None:
        return {device.id for device in intent_devices(states)}
    return set(names) or None


def _in_sync_points(
    events: Sequence[AuditEvent], flow: Sequence[AuditEvent], intent: set[UUID] | None, start: datetime, end: datetime
) -> list[Point]:
    """Per successful scan, the devices in intent its own records found in sync.

    A scan's records are paired with its end in time order, so a workflow id
    used twice is two scans. A record with an error is not in sync. Written for
    few steps per event, like the timeline: events are filtered in bulk and
    only the scans that end in the range are walked.
    """
    # The same tests as `_is_scan_end` and `_is_scan_record`, on one line each.
    ends = [e for e in flow if e.event_type in _RUN_END and e.workflow_type == "ScanDrift" and e.timestamp < end]
    wanted = {e.workflow_id for e in ends if e.timestamp >= start}
    ends = [e for e in ends if e.workflow_id in wanted]
    records = _where(events, map(wanted.__contains__, map(_WORKFLOW_ID, events)))
    records = [e for e in records if e.activity_name == "detect_drift"]
    pending: dict[str, set[UUID]] = {}
    points = []
    for event in sorted([*records, *ends], key=_BY_TIME):
        if event.activity_name is None:
            found = pending.pop(event.workflow_id, set())
            if event.outcome == "success" and event.timestamp >= start:
                points.append(Point(t=event.timestamp, value=len(found if intent is None else found & intent)))
            continue
        device = event.target_id
        if event.workflow_type != "ScanDrift" or event.event_type not in _SCAN_RECORDS or not isinstance(device, UUID):
            continue
        in_sync = pending.setdefault(event.workflow_id, set())
        if event.event_type is AuditEventType.ACTIVITY_COMPLETED and not event.payload.get("paths"):
            in_sync.add(device)
        else:
            in_sync.discard(device)
    return points


def _successful_scan_ends(flow: Sequence[AuditEvent], start: datetime, end: datetime) -> list[datetime]:
    """When each successful scan in ``[start, end)`` ended, from the workflow-level events."""
    ends = [e for e in flow if e.event_type in _RUN_END and e.workflow_type == "ScanDrift"]
    return sorted(e.timestamp for e in ends if e.outcome == "success" and start <= e.timestamp < end)


def _open_drift_points(
    events: Sequence[AuditEvent], flow: Sequence[AuditEvent], intent: set[UUID] | None, start: datetime, end: datetime
) -> list[Point]:
    """Per successful scan, the drift episodes open after it on devices in intent.

    An episode that closed before ``start`` is open at no point in the range,
    so only the later ones are read.
    """
    by_device = _scans_by_device(events, until=end)
    spans = [
        span
        for device_id, of_device in by_device.items()
        if intent is None or device_id in intent
        for span in _episode_spans(of_device, since=start)
    ]
    opened = sorted(scans[0].timestamp for scans, _ in spans)
    closed = sorted(scans[-1].timestamp for scans, is_closed in spans if is_closed)
    return [
        Point(t=t, value=bisect_right(opened, t) - bisect_right(closed, t))
        for t in _successful_scan_ends(flow, start, end)
    ]


def _repair_points(events: Sequence[AuditEvent], start: datetime, end: datetime) -> list[Point]:
    """One point per episode closed in the range, at its close, scan to scan.

    Like the timeline's episodes, a device that has left intent keeps its
    repairs: they happened.
    """
    by_device = _scans_by_device(events, until=end)
    spans = [span for of_device in by_device.values() for span in _episode_spans(of_device, since=start)]
    closes = sorted((scans[-1].timestamp, scans[0].timestamp) for scans, is_closed in spans if is_closed)
    return [Point(t=t, value=int((t - opened).total_seconds())) for t, opened in closes if start <= t < end]


def _run_outcome(run: _Run) -> str:
    """``succeeded``, ``failed`` or ``running``.

    The history has no view of Temporal, so a run with no recorded end is
    ``running``, unless a later start under its id shows it never finished. A
    cancelled run and one that never finished did not succeed: ``failed``.
    """
    state = _state(run, None)
    if state is RunState.SUCCEEDED:
        return "succeeded"
    if run.end is None and not run.superseded:
        return "running"
    return "failed"


def _run_points(runs: Sequence[_Run], scheduled: set[str], starts: list[datetime], bucket: Bucket) -> list[Point]:
    """Runs by the bucket of their start, keyed ``<trigger>.<outcome>``.

    A bucket where no run started has no data: its value is None and its
    counts are empty.
    """
    if not starts:
        return []
    first, size = starts[0], _BUCKET_SIZE[bucket]
    counts: dict[int, dict[str, int]] = {}
    for run in runs:
        index = (run.start.timestamp - first) // size
        if 0 <= index < len(starts):
            key = f"{_trigger(run, scheduled).value}.{_run_outcome(run)}"
            per_bucket = counts.setdefault(index, {})
            per_bucket[key] = per_bucket.get(key, 0) + 1
    points = []
    for index, bucket_start in enumerate(starts):
        in_bucket = counts.get(index)
        if in_bucket is None:
            points.append(Point(t=bucket_start, value=None, by_outcome={}))
        else:
            points.append(Point(t=bucket_start, value=sum(in_bucket.values()), by_outcome=dict(in_bucket)))
    return points


def project_series(
    events: Sequence[AuditEvent],
    name: SeriesName,
    start: datetime,
    end: datetime,
    bucket: Bucket,
    *,
    states: Sequence[DesiredState | IntentDevice] | None = None,
    names: Mapping[UUID, str] | None = None,
) -> Series:
    """One history series over ``[start, end)`` (research R8; spec 015 R5).

    ``drifted_devices``, ``undesired_paths``, ``in_sync_devices`` and
    ``open_drifts`` have a point per successful scan, and
    ``repair_duration`` one per drift episode closed in the range; they ignore
    ``bucket``. ``deploys``, ``repairs`` and ``runs`` count per UTC-aligned
    bucket. The range is never clamped to the data: before the first event
    there are simply no points.

    Args:
        states: the devices in intent, for ``in_sync_devices`` and
            ``open_drifts``; None when the Source of Truth could not be read.
        names: device names from the last successful SoT read, which stand
            for intent when ``states`` is None.

    Raises:
        ValueError: ``start >= end``, or more than 2,000 buckets.
    """
    if start >= end:
        raise ValueError("start must be before end")
    starts = bucket_starts(start, end, bucket)
    # Step records neither start nor end a run, and they are most of the trail:
    # runs, markers and scan ends are read from workflow-level events only.
    flow = _flow(events)
    runs = _split_runs(flow)
    scheduled = _scheduled_ids(runs, flow)
    if name in _PER_SCAN:
        points = _scan_points(events, flow, name, start, end)
    elif name is SeriesName.IN_SYNC_DEVICES:
        points = _in_sync_points(events, flow, _intent_ids(states, names or {}), start, end)
    elif name is SeriesName.OPEN_DRIFTS:
        points = _open_drift_points(events, flow, _intent_ids(states, names or {}), start, end)
    elif name is SeriesName.REPAIR_DURATION:
        points = _repair_points(events, start, end)
    elif name is SeriesName.RUNS:
        points = _run_points(runs, scheduled, starts, bucket)
    else:
        points = _counted_points(events, name, starts, bucket)
    return Series(
        name=name,
        start=start,
        end=end,
        bucket=None if name in _UNBUCKETED else bucket,
        points=points,
        markers=_markers_of(runs, scheduled, start, end),
        first_event_at=min(map(_BY_TIME, events), default=None),
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


# The whole trail is folded here on every request, so this section is written
# for few steps per event (SC-013): events are filtered and sorted in bulk, a
# device's scans are reduced to one character each, and a regular expression
# finds the episodes. Models are built only for what the answer returns.
_SCAN_RECORDS = {AuditEventType.ACTIVITY_COMPLETED, AuditEventType.ACTIVITY_FAILED}
# From the first drifted scan to the first clean scan after it, both included.
# An error scan in between stays inside; an episode still open has no clean scan.
_EPISODE = re.compile("d[de]*c?")
_TARGET_INT = attrgetter("target_id.int")
# `_scan_code` by (failed, has paths).
_CODES = {(True, True): "e", (True, False): "e", (False, True): "d", (False, False): "c"}
_PATHS = methodcaller("get", "paths")


def _scan_code(event: AuditEvent) -> str:
    """One character per scan: e(rror), c(lean) or d(rifted), as `_scan_fields` decides them."""
    if event.event_type is AuditEventType.ACTIVITY_FAILED:
        return "e"
    return "d" if event.payload.get("paths") else "c"


def _scans_by_device(events: Sequence[AuditEvent], until: datetime | None = None) -> dict[UUID, list[AuditEvent]]:
    """Each device's scan records, in time order, up to ``until`` when given.

    The same test as `_is_scan_record`, in bulk. Devices are told apart by
    their UUID's integer: hashing a UUID runs Python code for every scan.
    """
    scans = _where(events, map(eq, map(_ACTIVITY, events), repeat("detect_drift")))
    scans = _where(scans, map(eq, map(_WORKFLOW_TYPE, scans), repeat("ScanDrift")))
    scans = _where(scans, map(_SCAN_RECORDS.__contains__, map(_EVENT_TYPE, scans)))
    scans = _where(scans, map(isinstance, map(_TARGET, scans), repeat(UUID)))
    if until is not None:
        scans = _where(scans, map(le, map(_BY_TIME, scans), repeat(until)))
    scans.sort(key=_BY_TIME)
    ids = list(map(_TARGET_INT, scans))
    # One pass per device, in the order of its first scan: a fabric has few.
    return {
        scans[ids.index(device)].target_id: _where(scans, map(eq, ids, repeat(device)))  # type: ignore[misc]
        for device in dict.fromkeys(ids)
    }


def _episode_spans(
    of_device: Sequence[AuditEvent], since: datetime | None = None
) -> list[tuple[Sequence[AuditEvent], bool]]:
    """Each drift episode in one device's scans: its scans, and whether it closed.

    The one definition of an episode: the timeline and the history both use it.
    With ``since``, episodes that closed before it are skipped without being
    read. An episode never spans a clean scan, which either closes it or lies
    outside every episode, so the search starts after the last clean scan
    before ``since``.
    """
    first = 0
    if since is not None:
        first = bisect_left(of_device, since, key=_BY_TIME)
        while first and _scan_code(of_device[first - 1]) != "c":
            first -= 1
    tail = of_device[first:]
    # `_scan_code` in bulk: it runs for every scan.
    failed = map(is_, map(_EVENT_TYPE, tail), repeat(AuditEventType.ACTIVITY_FAILED))
    codes = "".join(map(_CODES.__getitem__, zip(failed, map(bool, map(_PATHS, map(_PAYLOAD, tail))), strict=True)))
    return [(tail[m.start() : m.end()], codes[m.end() - 1] == "c") for m in _EPISODE.finditer(codes)]


def _scan_state(event: AuditEvent) -> DriftState:
    state: DriftState = _scan_fields(event, "")["drift_state"]
    return state


_ENDED = itemgetter(0)


def _episode(
    device_id: UUID,
    name: str,
    scans: Sequence[AuditEvent],
    deploys: Sequence[tuple[datetime, _Run]],
    scheduled: set[str],
) -> DriftEpisode:
    """One episode from its scans: the opening one through the closing one, or the latest if open.

    ``deploys`` are in order of their end.
    """
    states = [_scan_state(scan) for scan in scans]
    opened_at = scans[0].timestamp
    closed = scans[-1].timestamp if states[-1] is DriftState.IN_SYNC else None
    closed_by = None
    if closed is not None:
        last_drifted = max(scan.timestamp for scan, state in zip(scans, states, strict=True) if state in _DRIFTED)
        closers = deploys[bisect_right(deploys, last_drifted, key=_ENDED) : bisect_left(deploys, closed, key=_ENDED)]
        if closers:
            # The latest end, and the first deploy to end then.
            ended, run = closers[bisect_left(closers, closers[-1][0], key=_ENDED)]
            trigger = _trigger(run, scheduled)
            closed_by = EpisodeClose(
                kind=_close_kind(run, trigger), workflow_id=run.workflow_id, trigger=trigger, ended_at=ended
            )
    return DriftEpisode(
        device_id=device_id,
        name=name,
        opened_at=opened_at,
        closed_at=closed,
        duration_s=int((closed - opened_at).total_seconds()) if closed else None,
        # The closing scan's `in_sync` is how it ended, not a state it was in.
        states=list(dict.fromkeys(state for state in states if state is not DriftState.IN_SYNC)),
        scans=len(scans),
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
    by_device = _scans_by_device(known)

    # Each device's scans are in time order, so the range's points are a tail.
    first_in_range = {d: bisect_left(of_device, start, key=_BY_TIME) for d, of_device in by_device.items()}
    in_range = sum(len(of_device) - first_in_range[d] for d, of_device in by_device.items())
    if in_range > max_points:
        raise TimelineTooLongError(
            f"the range holds {in_range} scan points; one answer carries at most {max_points}. Ask for a shorter range."
        )

    # Runs are split from workflow-level events only: step records neither
    # start nor end a run, and they are most of the trail.
    flow = sorted(_flow(known), key=_BY_TIME)
    runs = _split_runs(flow, ordered=True)
    scheduled = _scheduled_ids(runs, flow)
    deploys: dict[UUID, list[tuple[datetime, _Run]]] = {}
    for run in runs:
        if run.workflow_type != "DeployIntent" or run.end is None:
            continue
        target = run.start.target_id
        succeeded = run.end.event_type is AuditEventType.WORKFLOW_TERMINATED and run.end.outcome == "success"
        if succeeded and isinstance(target, UUID):
            deploys.setdefault(target, []).append((run.end.timestamp, run))
    for of_target in deploys.values():
        of_target.sort(key=_ENDED)

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
    # (opened at, closed at or None, device id, name, the episode's scans), for
    # every episode in the trail up to `end`.
    spans: list[tuple[datetime, datetime | None, UUID, str, list[AuditEvent]]] = []
    for device_id, name, known_in_intent in devices:
        of_device = by_device.get(device_id, [])
        lanes.append(
            DeviceLane(
                device_id=device_id,
                name=name,
                in_intent=known_in_intent,
                points=[_scan_point(scan) for scan in of_device[first_in_range.get(device_id, 0) :]],
            )
        )
        for span, is_closed in _episode_spans(of_device):
            spans.append((span[0].timestamp, span[-1].timestamp if is_closed else None, device_id, name, list(span)))

    def build(span: tuple[datetime, datetime | None, UUID, str, list[AuditEvent]]) -> DriftEpisode:
        _, _, device_id, name, of_episode = span
        return _episode(device_id, name, of_episode, deploys.get(device_id, []), scheduled)

    spans.sort(key=itemgetter(0))
    closed_spans = [span for span in spans if span[1] is not None]
    latest = max(closed_spans, key=itemgetter(1), default=None)
    return DriftTimeline(
        start=start,
        end=end,
        lanes=lanes,
        episodes=[build(span) for span in spans if span[1] is None or span[1] >= start],
        markers=_markers_of(runs, scheduled, start, end),
        latest_closed=build(latest) if latest is not None else None,
        open_count=len(spans) - len(closed_spans),
        first_event_at=min(map(_BY_TIME, events), default=None),
    )


def _directions(scan: AuditEvent) -> tuple[int, int | None]:
    """A scan's missing and undesired path counts; undesired is None before the split (FR-012a)."""
    paths = scan.payload.get("paths") or []
    if "undesired_paths" not in scan.payload:
        return 0, None if paths else 0
    undesired = set(scan.payload["undesired_paths"])
    return sum(1 for path in paths if path not in undesired), len(undesired)


def project_drift_summary(
    events: Sequence[AuditEvent],
    states: Sequence[DesiredState | IntentDevice] | None,
    names: Mapping[UUID, str],
    start: datetime,
    end: datetime,
    *,
    max_points: int = MAX_SCAN_POINTS,
) -> DriftSummary:
    """Each device's drift episodes over ``[start, end]``, as the timeline finds them (spec 015 FR-006).

    An episode counts when it overlaps the range. Its drifted time is clipped
    to the range and runs scan to scan: an episode still open at ``end`` counts
    up to its latest scan. Devices in intent follow `project_timeline`.

    Raises:
        ValueError: ``start >= end``.
        TimelineTooLongError: more than ``max_points`` scan points in the range,
            the timeline's own limit.
    """
    if start >= end:
        raise ValueError("start must be before end")
    by_device = _scans_by_device(events, until=end)
    in_range = sum(len(of) - bisect_left(of, start, key=_BY_TIME) for of in by_device.values())
    if in_range > max_points:
        raise TimelineTooLongError(
            f"the range holds {in_range} scan points; one answer carries at most {max_points}. Ask for a shorter range."
        )
    if states is not None:
        intent = {device.id: device.name for device in intent_devices(states)}
    else:
        intent = {d: name for d, name in names.items() if d in by_device}
    rows = []
    for device_id, of_device in by_device.items():
        episodes = opened = drifted = 0
        for span, is_closed in _episode_spans(of_device, since=start):
            last = span[-1].timestamp
            if is_closed and last < start:
                continue
            episodes += 1
            opened += 0 if is_closed else 1
            drifted += max(0, int((min(last, end) - max(span[0].timestamp, start)).total_seconds()))
        if not episodes:
            continue
        counts = [_directions(scan) for scan in of_device[bisect_left(of_device, start, key=_BY_TIME) :]]
        undesired = [count for _, count in counts]
        rows.append(
            DeviceDriftSummary(
                device_id=device_id,
                name=intent.get(device_id) or names.get(device_id, str(device_id)),
                in_intent=device_id in intent,
                episodes=episodes,
                open=opened,
                drifted_s=drifted,
                most_missing=max((missing for missing, _ in counts), default=0),
                most_undesired=None if None in undesired else max((u for u in undesired if u is not None), default=0),
            )
        )
    rows.sort(key=lambda row: (-row.episodes, -row.drifted_s, row.name))
    return DriftSummary(start=start, end=end, devices=rows)


# --- run steps (spec 011 research R15) --------------------------------------------

# The one place a recorded step is given its NAF block (FR-026).
STEP_BLOCKS: dict[str, NafBlock] = {
    "fetch_desired_state": NafBlock.INTENT,
    # A scan reads intent once for the use case (spec 015 FR-016).
    "fetch_devices_for_use_case": NafBlock.INTENT,
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


def scan_of_response(events: Sequence[AuditEvent], workflow_id: str, started_at: datetime) -> list[AuditEvent]:
    """The events of the scan a DriftResponse run started, its child ``<id>-scan`` (spec 015 FR-017).

    The child's id is fixed by the parent's, so the scan is that id's first
    run that started at or after the parent did.
    """
    child = f"{workflow_id}-scan"
    start = min(
        (
            e.timestamp
            for e in events
            if e.workflow_id == child and e.event_type is AuditEventType.WORKFLOW_STARTED and e.timestamp >= started_at
        ),
        default=None,
    )
    return run_events(events, child, start) if start is not None else []


_STEP_EVENTS = {AuditEventType.ACTIVITY_COMPLETED, AuditEventType.ACTIVITY_FAILED}
# A scan that records its steps reads intent first; one recorded before spec 015
# wrote only its drift checks, and stays "not recorded" (FR-018).
_SCAN_INTENT_STEP = "fetch_devices_for_use_case"
_SCAN_BLOCKS = (NafBlock.INTENT, NafBlock.COLLECTOR, NafBlock.OBSERVABILITY)


def _records_scan_steps(scan: Sequence[AuditEvent]) -> bool:
    return any(e.activity_name == _SCAN_INTENT_STEP and e.event_type in _STEP_EVENTS for e in scan)


def _step_reason(event: AuditEvent) -> str | None:
    return first_line(str(event.payload.get("error") or "")) or None


def _scan_blocks(scan: Sequence[AuditEvent]) -> dict[NafBlock, BlockState]:
    """Intent, Collector and Observability from a scan's recorded steps (spec 015 FR-017).

    A block is failed by its first failed step, with that step's reason, and
    otherwise done at its last step. A device whose collect failed also records
    its drift check as failed: that failure is the Collector's, so it does not
    fail Observability, which still ran for the other devices. A block with no
    step was not reached.
    """
    ordered = sorted((e for e in scan if e.event_type in _STEP_EVENTS and e.activity_name), key=lambda e: e.timestamp)
    not_collected = {
        e.target_id
        for e in ordered
        if e.activity_name == "collect_drift_state" and e.event_type is AuditEventType.ACTIVITY_FAILED
    }
    blocks: dict[NafBlock, BlockState] = {}
    for block in _SCAN_BLOCKS:
        of_block = [e for e in ordered if STEP_BLOCKS.get(e.activity_name or "") is block]
        if block is NafBlock.OBSERVABILITY:
            of_block = [e for e in of_block if e.target_id not in not_collected]
        failed = next((e for e in of_block if e.event_type is AuditEventType.ACTIVITY_FAILED), None)
        if failed is not None:
            blocks[block] = BlockState(
                block=block, status=BlockStatus.FAILED, reason=_step_reason(failed), t=failed.timestamp
            )
        elif of_block:
            blocks[block] = BlockState(block=block, status=BlockStatus.DONE, t=of_block[-1].timestamp)
        else:
            blocks[block] = BlockState(block=block, status=BlockStatus.NOT_REACHED)
    return blocks


def _repair_block(events: Sequence[AuditEvent]) -> BlockState:
    """The Executor in a DriftResponse: what its per-device decisions say the repair did."""
    repairs = sorted(
        (
            e
            for e in events
            if e.event_type is AuditEventType.DRIFT_RESPONSE and e.payload.get("decision") == "remediated"
        ),
        key=lambda e: e.timestamp,
    )
    # `unknown`: the reconcile failed as a whole, so its result for the device is not known to be a success.
    failed = [e for e in repairs if e.payload.get("result") in {"failed", "unknown"}]
    if failed:
        count = len(failed)
        reason = f"the repair did not succeed on {count} device{'s' if count != 1 else ''}"
        return BlockState(block=NafBlock.EXECUTOR, status=BlockStatus.FAILED, reason=reason, t=failed[0].timestamp)
    succeeded = [e for e in repairs if e.payload.get("result") == "succeeded"]
    if succeeded:
        return BlockState(block=NafBlock.EXECUTOR, status=BlockStatus.DONE, t=succeeded[-1].timestamp)
    return BlockState(block=NafBlock.EXECUTOR, status=BlockStatus.NOT_REACHED)


def project_steps(run: RunView, events: Sequence[AuditEvent], scan_events: Sequence[AuditEvent] = ()) -> RunSteps:
    """What each NAF block did in one run, as far as the audit trail records it.

    Only a recorded step marks a block done. Nothing records a step starting,
    so no block is ever "in progress". A failed deploy records its end with a
    reason and no failed step: the block that failed is the first with no
    record, or Observability when its check ran and found drift.

    A scan records its own steps since spec 015 (FR-016); a drift response
    shows its scan's blocks and, as the Executor, its repairs (FR-017). A scan
    recorded before then is still not recorded (FR-018).

    Args:
        events: the run's own events (``run_events``).
        scan_events: for a DriftResponse, its scan's events (``scan_of_response``).
    """
    ordered = sorted(events, key=lambda e: e.timestamp)
    if run.workflow_type == "DriftResponse":
        # The strip lists the scan's steps: the response itself records decisions, not steps.
        ordered = sorted([*ordered, *scan_events], key=lambda e: e.timestamp)
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
    scan = {"ScanDrift": events, "DriftResponse": scan_events}.get(run.workflow_type)
    if scan is not None and _records_scan_steps(scan):
        blocks = _scan_blocks(scan)
        blocks[NafBlock.EXECUTOR] = (
            _repair_block(events)
            if run.workflow_type == "DriftResponse"
            else BlockState(block=NafBlock.EXECUTOR, status=BlockStatus.NOT_REACHED)
        )
        return RunSteps(run=run, recorded=True, steps=steps, blocks=[*(blocks[b] for b in _LOOP_STEPS), *tail])
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
