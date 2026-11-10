"""Lanes and drift episodes (spec 011 T011, research R10).

An episode runs from the scan that first found a device not in sync to the
scan that first found it in sync again. What closed it is a deploy the audit
trail records against that device in between, never a guess from timing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from snapl_orchestrator.models import AuditEvent, AuditEventType, WorkflowReason
from snapl_presentation.dashboard.models import DriftEpisode, DriftState, Trigger
from snapl_presentation.dashboard.projections import IntentDevice, TimelineTooLongError, project_timeline
from tests.unit.test_presentation.dashboard_fakes import ARC, SCAN_3_END, arc_events, arc_states

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 7, 21, 0, tzinfo=UTC)
LEAF = UUID("00000000-0000-0000-0000-000000000002")
SPINE = UUID("00000000-0000-0000-0000-000000000001")
DEVICES = [IntentDevice(LEAF, "leaf-01", "leaf"), IntentDevice(SPINE, "spine-01", "spine")]
NAMES = {LEAF: "leaf-01", SPINE: "spine-01"}


def _at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _scan(
    minutes: float,
    *,
    device: UUID = LEAF,
    paths: list[str] | None = None,
    undesired: list[str] | None = None,
    error: str | None = None,
    classified: bool = True,
) -> AuditEvent:
    """A per-device ScanDrift record, shaped like ``scan_drift._audit_device``."""
    payload: dict = {"status": "drifted" if paths else "clean", "drift_items": len(paths or [])}
    if paths:
        payload["paths"] = paths
        if classified:
            payload["undesired_paths"] = undesired or []
    if error:
        payload["error"] = error
    return AuditEvent(
        event_id=uuid4(),
        workflow_id=f"scan-drift-dcfabric-{minutes}",
        workflow_type="ScanDrift",
        target_id=device,
        event_type=AuditEventType.ACTIVITY_FAILED if error else AuditEventType.ACTIVITY_COMPLETED,
        activity_name="detect_drift",
        outcome="failure" if error else "success",
        payload=payload,
        timestamp=_at(minutes),
    )


def _deploy(
    start: float,
    end: float,
    *,
    device: UUID = LEAF,
    success: bool = True,
    parent: str | None = None,
    record_parent: bool = True,
) -> list[AuditEvent]:
    """One DeployIntent run: its start and its end, as ``deploy_intent.py`` writes them."""
    common = {"workflow_id": f"deploy-intent-{device}", "workflow_type": "DeployIntent", "target_id": device}
    return [
        AuditEvent(
            event_id=uuid4(),
            event_type=AuditEventType.WORKFLOW_STARTED,
            payload={"parent_workflow_id": parent} if record_parent else {},
            timestamp=_at(start),
            **common,
        ),
        AuditEvent(
            event_id=uuid4(),
            event_type=AuditEventType.WORKFLOW_TERMINATED,
            outcome="success" if success else "failure",
            reason=WorkflowReason.SUCCEEDED if success else WorkflowReason.APPLY_FAILED,
            timestamp=_at(end),
            **common,
        ),
    ]


def _run_start(workflow_id: str, workflow_type: str, minutes: float, payload: dict | None = None) -> AuditEvent:
    return AuditEvent(
        event_id=uuid4(),
        workflow_id=workflow_id,
        workflow_type=workflow_type,
        target_id="dcfabric",
        event_type=AuditEventType.WORKFLOW_STARTED,
        payload=payload or {},
        timestamp=_at(minutes),
    )


def _timeline(events: list[AuditEvent], start: float = 0, end: float = 120, devices=DEVICES):
    return project_timeline(events, devices, NAMES, _at(start), _at(end))


DRIFT = {"paths": ["/a"]}


# --- the captured demo arc ---------------------------------------------------------


async def test_demo_arc_lanes_and_episodes():
    states = arc_states()
    names = {s.device.id: s.device.name for s in states}

    timeline = project_timeline(await arc_events(), states, names, SCAN_3_END - timedelta(hours=1), SCAN_3_END)

    assert [lane.name for lane in timeline.lanes] == list(ARC)
    assert all(len(lane.points) == 3 and lane.in_intent for lane in timeline.lanes)
    lanes = {lane.name: [point.state for point in lane.points] for lane in timeline.lanes}
    assert lanes["spine-01"] == [DriftState.MISSING, DriftState.MISSING, DriftState.IN_SYNC]
    assert lanes["leaf-01"] == [DriftState.UNDESIRED] * 3
    assert lanes["leaf-03"] == [DriftState.IN_SYNC] * 3
    spine_points = timeline.lanes[0].points
    assert spine_points[0].missing_paths == ["/interface[name=ethernet-1/1]/enabled"]
    assert spine_points[0].scan_workflow_id.startswith("scan-drift-dcfabric-")

    by_device = {episode.name: episode for episode in timeline.episodes}
    assert set(by_device) == {"spine-01", "leaf-01"}
    spine = by_device["spine-01"]
    assert (spine.opened_at, spine.closed_at) == (spine_points[0].t, spine_points[2].t)
    assert spine.duration_s == int((spine_points[2].t - spine_points[0].t).total_seconds()) == 5
    assert (spine.scans, spine.states) == (3, [DriftState.MISSING])
    # The operator's `snapl reconcile --drifted` redeployed it between scans 2 and 3.
    assert spine.closed_by is not None
    assert spine.closed_by.kind == "reconcile"
    assert spine.closed_by.trigger is Trigger.OPERATOR
    assert spine.closed_by.workflow_id == f"deploy-intent-{ARC['spine-01'][1]}"
    assert spine_points[1].t < spine.closed_by.ended_at < spine_points[2].t

    # Undesired config is not removed (#65): leaf-01's episode is still open.
    leaf = by_device["leaf-01"]
    assert (leaf.closed_at, leaf.duration_s, leaf.closed_by) == (None, None, None)
    assert (leaf.scans, leaf.states) == (3, [DriftState.UNDESIRED])

    assert timeline.latest_closed == spine
    assert timeline.open_count == 1
    assert [marker.kind for marker in timeline.markers] == ["reconcile"]
    assert timeline.first_event_at == datetime(2026, 11, 7, 21, 18, 56, 168004, tzinfo=UTC)


# --- episode rules (data-model.md) -------------------------------------------------


def test_a_device_never_drifted_has_a_lane_and_no_episode():
    timeline = _timeline([_scan(1), _scan(6)])

    assert [(lane.name, len(lane.points)) for lane in timeline.lanes] == [("spine-01", 0), ("leaf-01", 2)]
    assert timeline.episodes == []
    assert (timeline.latest_closed, timeline.open_count) == (None, 0)


def test_repaired_then_drifted_again_is_two_episodes():
    events = [_scan(1, **DRIFT), *_deploy(2, 3), _scan(6), _scan(11, **DRIFT), _scan(16, **DRIFT)]

    first, second = _timeline(events).episodes

    assert (first.opened_at, first.closed_at, first.scans) == (_at(1), _at(6), 2)
    assert (second.opened_at, second.closed_at, second.scans) == (_at(11), None, 2)


def test_an_error_scan_inside_an_episode_is_counted_and_leaves_it_open():
    events = [_scan(1, **DRIFT), _scan(6, error="gNMI timeout"), _scan(11, **DRIFT), *_deploy(12, 13), _scan(16)]

    (episode,) = _timeline(events).episodes

    assert (episode.opened_at, episode.closed_at, episode.scans) == (_at(1), _at(16), 4)
    assert episode.states == [DriftState.MISSING, DriftState.ERROR]
    assert episode.closed_by is not None


def test_an_error_scan_alone_opens_no_episode():
    timeline = _timeline([_scan(1), _scan(6, error="gNMI timeout"), _scan(11)])

    assert timeline.episodes == []
    assert [point.state for point in timeline.lanes[1].points] == [
        DriftState.IN_SYNC,
        DriftState.ERROR,
        DriftState.IN_SYNC,
    ]
    assert timeline.lanes[1].points[1].error == "gNMI timeout"


def test_an_episode_keeps_each_state_it_passed_through_once():
    events = [
        _scan(1, paths=["/a"]),
        _scan(6, paths=["/a", "/b"], undesired=["/b"]),
        _scan(11, paths=["/b"], undesired=["/b"]),
        _scan(16, paths=["/b"], undesired=["/b"]),
    ]

    (episode,) = _timeline(events).episodes

    assert episode.states == [DriftState.MISSING, DriftState.MISSING_AND_UNDESIRED, DriftState.UNDESIRED]


def test_clean_again_with_no_deploy_in_between_is_closed_by_nothing_known():
    """Fixed by hand, or intent changed: nothing recorded says which."""
    (episode,) = _timeline([_scan(1, **DRIFT), _scan(6)]).episodes

    assert episode.closed_at == _at(6)
    assert episode.closed_by is None


def test_a_failed_deploy_is_not_what_closed_it():
    (episode,) = _timeline([_scan(1, **DRIFT), *_deploy(2, 3, success=False), _scan(6)]).episodes

    assert episode.closed_by is None


def test_a_deploy_of_another_device_is_not_what_closed_it():
    (episode,) = _timeline([_scan(1, **DRIFT), *_deploy(2, 3, device=SPINE), _scan(6)]).episodes

    assert episode.closed_by is None


def test_a_deploy_that_ended_before_the_last_drifted_scan_is_not_what_closed_it():
    events = [_scan(1, **DRIFT), *_deploy(2, 3), _scan(6, **DRIFT), _scan(11)]

    (episode,) = _timeline(events).episodes

    # The scan at minute 6 still saw the drift, so the deploy at minute 3 did not repair it.
    assert episode.closed_by is None


def test_the_latest_of_several_deploys_is_what_closed_it():
    events = [_scan(1, **DRIFT), *_deploy(2, 2.5, success=False), *_deploy(3, 4), _scan(6)]

    (episode,) = _timeline(events).episodes

    assert episode.closed_by.ended_at == _at(4)


def test_an_operators_direct_deploy_is_kind_deploy():
    (episode,) = _timeline([_scan(1, **DRIFT), *_deploy(2, 3, parent=None), _scan(6)]).episodes

    assert (episode.closed_by.kind, episode.closed_by.trigger) == ("deploy", Trigger.OPERATOR)


def test_an_operators_reconcile_is_kind_reconcile():
    events = [
        _scan(1, **DRIFT),
        _run_start("reconcile-abc", "ReconcileDevices", 2),
        *_deploy(2, 3, parent="reconcile-abc"),
        _scan(6),
    ]

    (episode,) = _timeline(events).episodes

    assert (episode.closed_by.kind, episode.closed_by.trigger) == ("reconcile", Trigger.OPERATOR)


def test_a_scheduled_repair_is_kind_remediation():
    events = [
        _scan(1, **DRIFT),
        _run_start("drift-response-dcfabric-1", "DriftResponse", 2, {"mode": "remediate"}),
        *_deploy(2, 3, parent="drift-response-dcfabric-1-reconcile"),
        _scan(6),
    ]

    timeline = _timeline(events)

    assert (timeline.episodes[0].closed_by.kind, timeline.episodes[0].closed_by.trigger) == (
        "remediation",
        Trigger.SCHEDULE,
    )
    assert [marker.kind for marker in timeline.markers] == ["remediation"]


def test_a_deploy_recorded_before_parents_were_recorded_is_a_deploy_of_unknown_trigger():
    (episode,) = _timeline([_scan(1, **DRIFT), *_deploy(2, 3, record_parent=False), _scan(6)]).episodes

    assert (episode.closed_by.kind, episode.closed_by.trigger) == ("deploy", Trigger.UNKNOWN)


def test_a_scan_recorded_before_directions_were_recorded_is_drifted_and_unclassified():
    timeline = _timeline([_scan(1, paths=["/a"], classified=False)])

    point = timeline.lanes[1].points[0]
    assert (point.state, point.unclassified_paths, point.missing_paths) == (DriftState.DRIFTED, ["/a"], [])
    assert timeline.episodes[0].states == [DriftState.DRIFTED]


# --- the range ---------------------------------------------------------------------


def test_lanes_hold_only_the_ranges_points_but_episodes_that_overlap_it_are_whole():
    events = [_scan(1, **DRIFT), _scan(6, **DRIFT), *_deploy(7, 8), _scan(11), _scan(16)]

    timeline = _timeline(events, start=10, end=20)

    assert [point.t for point in timeline.lanes[1].points] == [_at(11), _at(16)]
    (episode,) = timeline.episodes
    assert (episode.opened_at, episode.closed_at, episode.scans) == (_at(1), _at(11), 3)


def test_an_episode_closed_before_the_range_is_left_out_but_can_be_the_latest_closed():
    events = [_scan(1, **DRIFT), *_deploy(2, 3), _scan(6), _scan(31)]

    timeline = _timeline(events, start=30, end=40)

    assert timeline.episodes == []
    assert timeline.latest_closed is not None
    assert timeline.latest_closed.closed_at == _at(6)
    assert timeline.open_count == 0


def test_nothing_after_the_ranges_end_is_used():
    events = [_scan(1, **DRIFT), *_deploy(2, 3), _scan(6)]

    timeline = _timeline(events, start=0, end=4)

    # As of minute 4 the closing scan had not happened.
    (episode,) = timeline.episodes
    assert (episode.closed_at, episode.closed_by) == (None, None)
    assert (timeline.latest_closed, timeline.open_count) == (None, 1)


def test_markers_are_those_inside_the_range():
    events = [_run_start("reconcile-1", "ReconcileDevices", 5), _run_start("reconcile-2", "ReconcileDevices", 50)]

    assert [m.workflow_id for m in _timeline(events, start=0, end=10).markers] == ["reconcile-1"]


def test_start_must_be_before_end():
    with pytest.raises(ValueError, match="start must be before end"):
        _timeline([], start=5, end=5)


def test_too_many_scan_points_is_refused_not_truncated():
    events = [_scan(minute) for minute in range(12)]

    with pytest.raises(TimelineTooLongError, match=r"12 scan points.*at most 10") as raised:
        project_timeline(events, DEVICES, NAMES, _at(0), _at(60), max_points=10)

    assert isinstance(raised.value, ValueError)
    assert len(project_timeline(events, DEVICES, NAMES, _at(0), _at(60), max_points=12).lanes[1].points) == 12


# --- which devices get a lane ------------------------------------------------------


def test_a_device_no_longer_in_intent_keeps_its_lane():
    gone = uuid4()

    timeline = project_timeline([_scan(1, device=gone, **DRIFT)], DEVICES, {**NAMES, gone: "leaf-09"}, _at(0), _at(10))

    assert [(lane.name, lane.in_intent) for lane in timeline.lanes] == [
        ("spine-01", True),
        ("leaf-01", True),
        ("leaf-09", False),
    ]
    assert timeline.episodes[0].name == "leaf-09"


def test_a_device_with_no_known_name_is_named_by_its_id():
    gone = uuid4()

    timeline = project_timeline([_scan(1, device=gone)], DEVICES, NAMES, _at(0), _at(10))

    assert timeline.lanes[-1].name == str(gone)


def test_without_the_source_of_truth_lanes_come_from_the_trail():
    timeline = project_timeline([_scan(1), _scan(1, device=SPINE)], None, {LEAF: "leaf-01"}, _at(0), _at(10))

    # No roles to order by, so by name; a device is "in intent" only if its name is known.
    assert [(lane.name, lane.in_intent) for lane in timeline.lanes] == [
        (str(SPINE), False),
        ("leaf-01", True),
    ]


# --- model rules -------------------------------------------------------------------


def _episode(**overrides) -> DriftEpisode:
    fields = {
        "device_id": LEAF,
        "name": "leaf-01",
        "opened_at": _at(1),
        "closed_at": None,
        "duration_s": None,
        "states": [DriftState.MISSING],
        "scans": 1,
        "closed_by": None,
    }
    return DriftEpisode(**{**fields, **overrides})


def test_an_open_episode_has_no_duration_and_nothing_that_closed_it():
    with pytest.raises(ValidationError, match="duration_s"):
        _episode(duration_s=5)
    with pytest.raises(ValidationError, match="closed_by"):
        _episode(closed_by={"kind": "deploy", "workflow_id": "d", "trigger": "operator", "ended_at": _at(2)})


def test_a_closed_episode_has_its_duration():
    with pytest.raises(ValidationError, match="duration_s"):
        _episode(closed_at=_at(6))
    assert _episode(closed_at=_at(6), duration_s=300).duration_s == 300
