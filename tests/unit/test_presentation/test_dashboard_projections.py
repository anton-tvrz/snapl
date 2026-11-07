"""Projections from audit events to dashboard views (spec 010 T014, research R4).

The projections are pure functions, so every test feeds hand-built events and
asserts the view, without a database. One test reads the live-captured
``audit_demo_arc.sqlite`` (T013), so the projections are also held to what the
workflows actually write (Principle III).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from snapl_intent.models import DesiredState, Device
from snapl_orchestrator.audit.sqlite import SqliteAuditLog
from snapl_orchestrator.models import AuditEvent, AuditEventType, WorkflowReason
from snapl_orchestrator.worker.client import RunningWorkflowInfo
from snapl_presentation.dashboard.models import Bucket, DriftState, RunState, SeriesName, Trigger
from snapl_presentation.dashboard.projections import (
    RunFilters,
    latest_pause,
    project_devices,
    project_repeated_failures,
    project_running,
    project_runs,
    project_series,
)

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 7, 21, 0, tzinfo=UTC)
FIXTURE = Path(__file__).parent / "fixtures" / "audit_demo_arc.sqlite"

SPINE = UUID("00000000-0000-0000-0000-000000000001")
LEAF = UUID("00000000-0000-0000-0000-000000000002")


def _state(device_id: UUID, name: str, role: str) -> DesiredState:
    return DesiredState(
        device=Device(id=device_id, name=name, management_address="10.0.0.1", role=role, use_case="dcfabric")
    )


def _scan(
    device_id: UUID,
    at: datetime,
    *,
    status: str = "clean",
    paths: list[str] | None = None,
    undesired: list[str] | None = None,
    error: str | None = None,
    workflow_id: str = "scan-drift-dcfabric-1",
) -> AuditEvent:
    """A per-device ScanDrift record, shaped like `scan_drift._audit_device`."""
    payload: dict = {"status": status, "drift_items": len(paths or [])}
    if paths:
        payload["paths"] = paths
        if undesired is not None:
            payload["undesired_paths"] = undesired
    if error:
        payload["error"] = error
    return AuditEvent(
        event_id=uuid4(),
        workflow_id=workflow_id,
        workflow_type="ScanDrift",
        target_id=device_id,
        event_type=AuditEventType.ACTIVITY_FAILED if error else AuditEventType.ACTIVITY_COMPLETED,
        activity_name="detect_drift",
        outcome="failure" if error else "success",
        payload=payload,
        timestamp=at,
    )


def _deploy(
    device_id: UUID,
    at: datetime,
    *,
    event_type: AuditEventType = AuditEventType.WORKFLOW_TERMINATED,
    outcome: str | None = "success",
    reason: WorkflowReason | None = WorkflowReason.SUCCEEDED,
    activity_name: str | None = None,
) -> AuditEvent:
    return AuditEvent(
        event_id=uuid4(),
        workflow_id=f"deploy-intent-{device_id}",
        workflow_type="DeployIntent",
        target_id=device_id,
        event_type=event_type,
        activity_name=activity_name,
        outcome=outcome,
        reason=reason,
        timestamp=at,
    )


@pytest.fixture
def states() -> list[DesiredState]:
    return [_state(LEAF, "leaf-01", "leaf"), _state(SPINE, "spine-01", "spine")]


def _by_name(views):
    return {view.name: view for view in views}


# --- drift state ---------------------------------------------------------------


def test_clean_scan_is_in_sync(states):
    views = _by_name(project_devices([_scan(LEAF, T0)], states, {}))

    leaf = views["leaf-01"]
    assert leaf.drift_state is DriftState.IN_SYNC
    assert leaf.last_scanned_at == T0
    assert leaf.last_scan_workflow_id == "scan-drift-dcfabric-1"
    assert leaf.suggested_command is None


def test_forward_only_drift_is_missing(states):
    event = _scan(LEAF, T0, status="drifted", paths=["/a", "/b"], undesired=[])

    leaf = _by_name(project_devices([event], states, {}))["leaf-01"]

    assert leaf.drift_state is DriftState.MISSING
    assert leaf.missing_paths == ["/a", "/b"]
    assert leaf.undesired_paths == []
    assert leaf.suggested_command == "snapl reconcile leaf-01"


def test_undesired_only_drift_has_no_command_and_names_65(states):
    event = _scan(LEAF, T0, status="drifted", paths=["/x"], undesired=["/x"])

    leaf = _by_name(project_devices([event], states, {}))["leaf-01"]

    assert leaf.drift_state is DriftState.UNDESIRED
    assert leaf.undesired_paths == ["/x"]
    assert leaf.missing_paths == []
    assert leaf.suggested_command is None
    assert leaf.note is not None
    assert "#65" in leaf.note


def test_both_directions_is_missing_and_undesired(states):
    event = _scan(LEAF, T0, status="drifted", paths=["/a", "/x", "/b"], undesired=["/x"])

    leaf = _by_name(project_devices([event], states, {}))["leaf-01"]

    assert leaf.drift_state is DriftState.MISSING_AND_UNDESIRED
    assert leaf.missing_paths == ["/a", "/b"]
    assert leaf.undesired_paths == ["/x"]
    assert leaf.suggested_command == "snapl reconcile leaf-01"
    assert leaf.note is not None
    assert "#65" in leaf.note


def test_scan_without_direction_is_drifted(states):
    """A scan recorded before FR-012a has `paths` but no `undesired_paths`."""
    event = _scan(LEAF, T0, status="drifted", paths=["/a"], undesired=None)

    leaf = _by_name(project_devices([event], states, {}))["leaf-01"]

    assert leaf.drift_state is DriftState.DRIFTED
    assert leaf.unclassified_paths == ["/a"]
    assert leaf.missing_paths == []
    assert leaf.undesired_paths == []
    assert leaf.suggested_command == "snapl reconcile leaf-01"


def test_failed_scan_is_error_with_its_message(states):
    event = _scan(LEAF, T0, status="error", error="gNMI dial failed: connection refused")

    leaf = _by_name(project_devices([event], states, {}))["leaf-01"]

    assert leaf.drift_state is DriftState.ERROR
    assert leaf.error == "gNMI dial failed: connection refused"
    assert leaf.last_scanned_at == T0


def test_device_with_no_scan_is_never_scanned(states):
    views = _by_name(project_devices([], states, {}))

    assert views["leaf-01"].drift_state is DriftState.NEVER_SCANNED
    assert views["leaf-01"].last_scanned_at is None


def test_latest_scan_wins_by_timestamp_not_arrival(states):
    newer = _scan(LEAF, T0 + timedelta(minutes=5))
    older = _scan(LEAF, T0, status="drifted", paths=["/a"], undesired=[])

    leaf = _by_name(project_devices([newer, older], states, {}))["leaf-01"]

    assert leaf.drift_state is DriftState.IN_SYNC
    assert leaf.last_scanned_at == T0 + timedelta(minutes=5)


def test_deploy_verification_is_not_a_scan(states):
    """DeployIntent also writes `detect_drift` against the device, with an empty
    payload. It must not overwrite the last scan's state."""
    scan = _scan(LEAF, T0, status="drifted", paths=["/a"], undesired=[])
    verify = _deploy(
        LEAF,
        T0 + timedelta(minutes=1),
        event_type=AuditEventType.ACTIVITY_COMPLETED,
        activity_name="detect_drift",
        reason=None,
    )
    failed_apply = _deploy(
        LEAF,
        T0 + timedelta(minutes=2),
        event_type=AuditEventType.ACTIVITY_FAILED,
        activity_name="apply_config",
        outcome="failure",
        reason=None,
    )

    leaf = _by_name(project_devices([scan, verify, failed_apply], states, {}))["leaf-01"]

    assert leaf.drift_state is DriftState.MISSING
    assert leaf.last_scanned_at == T0


# --- last deploy ---------------------------------------------------------------


def test_last_deploy_is_the_latest_terminal_event(states):
    events = [
        _deploy(LEAF, T0, outcome="success", reason=WorkflowReason.SUCCEEDED),
        _deploy(LEAF, T0 + timedelta(minutes=3), outcome="failure", reason=WorkflowReason.APPLY_FAILED),
        _deploy(LEAF, T0 + timedelta(minutes=1), event_type=AuditEventType.WORKFLOW_STARTED, outcome=None, reason=None),
    ]

    leaf = _by_name(project_devices(events, states, {}))["leaf-01"]

    assert leaf.last_deploy is not None
    assert leaf.last_deploy.success is False
    assert leaf.last_deploy.reason is WorkflowReason.APPLY_FAILED
    assert leaf.last_deploy.ended_at == T0 + timedelta(minutes=3)
    assert leaf.last_deploy.workflow_id == f"deploy-intent-{LEAF}"


def test_cancelled_deploy_counts_and_carries_no_reason(states):
    event = _deploy(LEAF, T0, event_type=AuditEventType.WORKFLOW_CANCELLED, outcome="cancelled", reason=None)

    leaf = _by_name(project_devices([event], states, {}))["leaf-01"]

    assert leaf.last_deploy is not None
    assert leaf.last_deploy.success is False
    assert leaf.last_deploy.reason is None


def test_no_deploy_leaves_last_deploy_empty(states):
    assert _by_name(project_devices([], states, {}))["leaf-01"].last_deploy is None


# --- membership and order ------------------------------------------------------


def test_device_only_in_the_audit_trail_is_excluded(states):
    gone = uuid4()
    events = [_scan(gone, T0, status="drifted", paths=["/a"], undesired=[])]

    views = project_devices(events, states, {gone: "leaf-99"})

    assert {view.name for view in views} == {"spine-01", "leaf-01"}
    assert all(view.in_intent for view in views)


def test_spines_sort_first_then_by_name():
    states = [
        _state(uuid4(), "leaf-02", "leaf"),
        _state(uuid4(), "spine-02", "spine"),
        _state(uuid4(), "leaf-01", "leaf"),
        _state(uuid4(), "spine-01", "spine"),
    ]

    assert [view.name for view in project_devices([], states, {})] == ["spine-01", "spine-02", "leaf-01", "leaf-02"]


def test_roles_come_from_intent(states):
    views = _by_name(project_devices([], states, {}))

    assert views["spine-01"].role == "spine"
    assert views["leaf-01"].role == "leaf"


# --- degraded view (SoT unavailable, research R13) -----------------------------


def test_degraded_view_lists_devices_seen_in_the_audit_trail():
    unknown = uuid4()
    events = [
        _scan(LEAF, T0, status="drifted", paths=["/a"], undesired=[]),
        _deploy(SPINE, T0),
        _scan(unknown, T0),
    ]

    views = project_devices(events, None, {LEAF: "leaf-01", SPINE: "spine-01"})

    by_id = {view.device_id: view for view in views}
    assert set(by_id) == {LEAF, SPINE, unknown}
    assert all(view.role is None for view in views)
    assert by_id[LEAF].name == "leaf-01"
    assert by_id[LEAF].drift_state is DriftState.MISSING
    assert by_id[LEAF].in_intent is True
    # A device the SoT never named in this process: its name is its id, and
    # nothing says it is in intent.
    assert by_id[unknown].name == str(unknown)
    assert by_id[unknown].in_intent is False


def test_degraded_view_ignores_use_case_level_events():
    """Workflow-level events are keyed on the use case, not a device."""
    started = AuditEvent(
        event_id=uuid4(),
        workflow_id="scan-drift-dcfabric-1",
        workflow_type="ScanDrift",
        target_id="dcfabric",
        event_type=AuditEventType.WORKFLOW_STARTED,
        timestamp=T0,
    )

    assert project_devices([started], None, {}) == []


# --- the live capture (T013) ---------------------------------------------------

# The six devices of the capture, ids from the deploys in the order the README
# lists them (spine-01, spine-02, leaf-01 to leaf-04).
_ARC = {
    "spine-01": ("spine", "18e29d6d-e0c5-0ba7-32d1-c510fc3ef077"),
    "spine-02": ("spine", "18e29d6e-1959-3451-32dc-c51d9065aa58"),
    "leaf-01": ("leaf", "18e29d6e-246e-5cd1-32d2-c51d13d4de9e"),
    "leaf-02": ("leaf", "18e29d6e-2e5b-c496-32da-c510ade043a0"),
    "leaf-03": ("leaf", "18e29d6e-3794-c788-32d5-c519c5f18e3a"),
    "leaf-04": ("leaf", "18e29d6e-422b-a401-32d9-c5162961ffbe"),
}


async def test_live_demo_arc_ends_with_only_leaf_01_drifted():
    log = SqliteAuditLog(database_url=str(FIXTURE), read_only=True)
    await log.initialize()
    events = await log.query_by_time_range(datetime(1970, 1, 1, tzinfo=UTC), datetime(9999, 1, 1, tzinfo=UTC))
    await log.close()
    states = [_state(UUID(device_id), name, role) for name, (role, device_id) in _ARC.items()]

    views = _by_name(project_devices(events, states, {}))

    assert list(views) == ["spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04"]
    leaf = views["leaf-01"]
    # Reconcile does not remove undesired config (#65), so the last scan still sees it.
    assert leaf.drift_state is DriftState.UNDESIRED
    assert leaf.undesired_paths == ["/interface[name=ethernet-1/7]/description"]
    assert leaf.suggested_command is None
    # spine-01's missing path was repaired by the reconcile; the third scan is clean.
    for name in ("spine-01", "spine-02", "leaf-02", "leaf-03", "leaf-04"):
        assert views[name].drift_state is DriftState.IN_SYNC, name
    # Every device's last deploy succeeded; spine-01 and leaf-01 were redeployed by the reconcile.
    assert all(view.last_deploy is not None and view.last_deploy.success for view in views.values())
    assert views["spine-01"].last_deploy.workflow_id == f"deploy-intent-{_ARC['spine-01'][1]}"
    assert views["spine-01"].last_deploy.ended_at.minute == 20


# =============================================================================
# Runs (T024, research R5)
# =============================================================================

UI = "http://localhost:18034"
NOW = T0 + timedelta(hours=1)


def _wf(
    workflow_id: str,
    workflow_type: str,
    event_type: AuditEventType,
    at: datetime,
    *,
    target=None,
    outcome: str | None = None,
    reason: WorkflowReason | None = None,
    payload: dict | None = None,
    actor: str | None = None,
) -> AuditEvent:
    return AuditEvent(
        event_id=uuid4(),
        workflow_id=workflow_id,
        workflow_type=workflow_type,
        target_id=target,
        event_type=event_type,
        outcome=outcome,
        reason=reason,
        payload=payload or {},
        timestamp=at,
        actor=actor,
    )


def _run(workflow_id, workflow_type, start, end=None, *, target=None, ok=True, start_payload=None, actor=None):
    """A run's start and, optionally, its end."""
    events = [
        _wf(
            workflow_id,
            workflow_type,
            AuditEventType.WORKFLOW_STARTED,
            start,
            target=target,
            payload=start_payload,
            actor=actor,
        )
    ]
    if end is not None:
        events.append(
            _wf(
                workflow_id,
                workflow_type,
                AuditEventType.WORKFLOW_TERMINATED,
                end,
                target=target,
                outcome="success" if ok else "failure",
                reason=WorkflowReason.SUCCEEDED if ok else WorkflowReason.APPLY_FAILED,
                payload=None if ok else {"detail": "apply rejected by device"},
                actor=actor,
            )
        )
    return events


def _runs(events, running_ids=(), **kwargs):
    return project_runs(
        events,
        set(running_ids) if running_ids is not None else None,
        NOW,
        names={LEAF: "leaf-01", SPINE: "spine-01"},
        ui_address=UI,
        namespace="default",
        **kwargs,
    )


DEPLOY_LEAF = f"deploy-intent-{LEAF}"
SCHEDULED = "drift-response-dcfabric-2026-11-07T21:00:00Z"


def test_two_runs_under_one_workflow_id_are_two_views():
    events = [
        *_run(
            DEPLOY_LEAF,
            "DeployIntent",
            T0,
            T0 + timedelta(seconds=4),
            target=LEAF,
            start_payload={"parent_workflow_id": None},
        ),
        *_run(
            DEPLOY_LEAF,
            "DeployIntent",
            T0 + timedelta(minutes=10),
            T0 + timedelta(minutes=10, seconds=3),
            target=LEAF,
            ok=False,
            start_payload={"parent_workflow_id": None},
        ),
    ]

    runs = _runs(events)

    assert [(r.workflow_id, r.started_at) for r in runs] == [
        (DEPLOY_LEAF, T0 + timedelta(minutes=10)),
        (DEPLOY_LEAF, T0),
    ]
    newest, oldest = runs
    assert newest.state is RunState.FAILED
    assert newest.reason == "apply_failed: apply rejected by device"
    assert newest.duration_s == 3
    assert oldest.state is RunState.SUCCEEDED
    assert oldest.reason is None
    assert oldest.target == "leaf-01"


def test_open_run_is_running_only_if_temporal_lists_it():
    events = _run("scan-drift-dcfabric-1", "ScanDrift", T0, target="dcfabric")

    [running] = _runs(events, running_ids={"scan-drift-dcfabric-1"})
    [unfinished] = _runs(events, running_ids=set())

    assert running.state is RunState.RUNNING
    assert running.duration_s == 3600
    assert running.ended_at is None
    assert unfinished.state is RunState.UNFINISHED
    assert unfinished.duration_s is None


def test_a_new_start_closes_an_open_run_as_unfinished():
    """A worker that died mid-deploy leaves a start with no end. The next run
    under the same id must not swallow it, and only the latest can be running."""
    events = [
        *_run(DEPLOY_LEAF, "DeployIntent", T0, target=LEAF, start_payload={"parent_workflow_id": None}),
        *_run(
            DEPLOY_LEAF,
            "DeployIntent",
            T0 + timedelta(minutes=5),
            target=LEAF,
            start_payload={"parent_workflow_id": None},
        ),
    ]

    newest, oldest = _runs(events, running_ids={DEPLOY_LEAF})

    assert newest.state is RunState.RUNNING
    assert oldest.state is RunState.UNFINISHED


def test_cancelled_run():
    events = [
        *_run(DEPLOY_LEAF, "DeployIntent", T0, target=LEAF, start_payload={"parent_workflow_id": None}),
        _wf(
            DEPLOY_LEAF,
            "DeployIntent",
            AuditEventType.WORKFLOW_CANCELLED,
            T0 + timedelta(seconds=2),
            target=LEAF,
            outcome="cancelled",
        ),
    ]

    [run] = _runs(events)

    assert run.state is RunState.CANCELLED
    assert run.ended_at == T0 + timedelta(seconds=2)


def test_events_before_their_start_row_are_ignored():
    """A late row whose start has not arrived yet does not invent a run."""
    events = [
        _wf(
            DEPLOY_LEAF,
            "DeployIntent",
            AuditEventType.WORKFLOW_TERMINATED,
            T0,
            target=LEAF,
            outcome="success",
            reason=WorkflowReason.SUCCEEDED,
        ),
    ]

    assert _runs(events) == []


def test_unknown_temporal_state_leaves_open_runs_unfinished():
    events = _run("scan-drift-dcfabric-1", "ScanDrift", T0, target="dcfabric")

    [run] = _runs(events, running_ids=None)

    assert run.state is RunState.UNFINISHED


# --- triggers ------------------------------------------------------------------


def _scheduled_arc(*, reconcile_parent_payload=True):
    """A scheduled remediate run: DriftResponse, its scan and reconcile children,
    and the deploy the reconcile started."""
    actor = "schedule:drift-response-dcfabric"
    return [
        *_run(
            SCHEDULED,
            "DriftResponse",
            T0,
            T0 + timedelta(seconds=30),
            target="dcfabric",
            actor=actor,
            start_payload={"mode": "remediate"},
        ),
        *_run(
            f"{SCHEDULED}-scan", "ScanDrift", T0 + timedelta(seconds=1), T0 + timedelta(seconds=10), target="dcfabric"
        ),
        *_run(f"{SCHEDULED}-reconcile", "ReconcileDevices", T0 + timedelta(seconds=11), T0 + timedelta(seconds=25)),
        *_run(
            DEPLOY_LEAF,
            "DeployIntent",
            T0 + timedelta(seconds=12),
            T0 + timedelta(seconds=20),
            target=LEAF,
            start_payload={"parent_workflow_id": f"{SCHEDULED}-reconcile"},
        ),
    ]


def test_a_scheduled_response_and_everything_it_started_is_schedule():
    runs = {r.workflow_id: r for r in _runs(_scheduled_arc())}

    assert {wf: r.trigger for wf, r in runs.items()} == {
        SCHEDULED: Trigger.SCHEDULE,
        f"{SCHEDULED}-scan": Trigger.SCHEDULE,
        f"{SCHEDULED}-reconcile": Trigger.SCHEDULE,
        DEPLOY_LEAF: Trigger.SCHEDULE,
    }
    assert runs[SCHEDULED].actor == "schedule:drift-response-dcfabric"
    assert runs[f"{SCHEDULED}-scan"].actor is None


def test_a_deploy_from_an_operator_reconcile_is_operator():
    events = [
        *_run("reconcile-1", "ReconcileDevices", T0, T0 + timedelta(seconds=20)),
        *_run(
            DEPLOY_LEAF,
            "DeployIntent",
            T0 + timedelta(seconds=1),
            T0 + timedelta(seconds=5),
            target=LEAF,
            start_payload={"parent_workflow_id": "reconcile-1"},
        ),
    ]

    runs = {r.workflow_id: r.trigger for r in _runs(events)}

    assert runs == {"reconcile-1": Trigger.OPERATOR, DEPLOY_LEAF: Trigger.OPERATOR}


def test_an_operator_deploy_is_operator():
    events = _run(
        DEPLOY_LEAF,
        "DeployIntent",
        T0,
        T0 + timedelta(seconds=4),
        target=LEAF,
        start_payload={"parent_workflow_id": None},
    )

    assert _runs(events)[0].trigger is Trigger.OPERATOR


def test_a_deploy_recorded_before_fr_016a_is_unknown():
    events = _run(DEPLOY_LEAF, "DeployIntent", T0, T0 + timedelta(seconds=4), target=LEAF)

    assert _runs(events)[0].trigger is Trigger.UNKNOWN


def test_a_deploy_whose_scheduled_parent_is_not_in_the_trail_is_unknown():
    """A `{wf}-reconcile` parent names a scheduled run by its form, but the run
    itself is not in the trail to confirm it."""
    events = _run(
        DEPLOY_LEAF,
        "DeployIntent",
        T0,
        T0 + timedelta(seconds=4),
        target=LEAF,
        start_payload={"parent_workflow_id": "drift-response-x-2026-01-01T00:00:00Z-reconcile"},
    )

    assert _runs(events)[0].trigger is Trigger.UNKNOWN


def test_operator_deploy_inside_a_scheduled_reconcile_window_stays_operator():
    """Regression for the timing rule R5 rejected: only the parent id is evidence."""
    spine_deploy = f"deploy-intent-{SPINE}"
    events = [
        *_scheduled_arc(),
        *_run(
            spine_deploy,
            "DeployIntent",
            T0 + timedelta(seconds=13),
            T0 + timedelta(seconds=18),
            target=SPINE,
            start_payload={"parent_workflow_id": None},
        ),
    ]

    runs = {r.workflow_id: r.trigger for r in _runs(events)}

    assert runs[spine_deploy] is Trigger.OPERATOR


def test_a_cli_scan_is_operator_with_no_invented_actor():
    events = _run("scan-drift-dcfabric-1", "ScanDrift", T0, T0 + timedelta(seconds=9), target="dcfabric")

    [run] = _runs(events)

    assert run.trigger is Trigger.OPERATOR
    assert run.actor is None
    assert run.target == "dcfabric"


# --- limits, filters and links -------------------------------------------------


def test_limit_keeps_the_newest():
    events = [
        e
        for minute in range(5)
        for e in _run(
            f"scan-{minute}",
            "ScanDrift",
            T0 + timedelta(minutes=minute),
            T0 + timedelta(minutes=minute, seconds=5),
            target="dcfabric",
        )
    ]

    assert [r.workflow_id for r in _runs(events, limit=2)] == ["scan-4", "scan-3"]


def test_filters_by_workflow_type_and_device():
    events = _scheduled_arc()

    deploys = _runs(events, filters=RunFilters(workflow_types=frozenset({"DeployIntent"})))
    leaf = _runs(events, filters=RunFilters(device="leaf-01"))
    spine = _runs(events, filters=RunFilters(device="spine-01"))

    assert [r.workflow_id for r in deploys] == [DEPLOY_LEAF]
    assert [r.workflow_id for r in leaf] == [DEPLOY_LEAF]
    assert spine == []


def test_web_ui_link_opens_the_workflow_id():
    events = _run(SCHEDULED, "DriftResponse", T0, target="dcfabric", actor="schedule:drift-response-dcfabric")

    [run] = _runs(events)

    assert run.web_ui_url == f"{UI}/namespaces/default/workflows/drift-response-dcfabric-2026-11-07T21%3A00%3A00Z"


# --- running, from Temporal ----------------------------------------------------


def test_running_view_prefers_the_audit_run_and_keeps_temporal_only_ones():
    events = _run(SCHEDULED, "DriftResponse", T0, target="dcfabric", actor="schedule:drift-response-dcfabric")
    runs = _runs(events, running_ids={SCHEDULED, "scan-drift-dcfabric-9"})
    infos = [
        RunningWorkflowInfo(workflow_id=SCHEDULED, workflow_type="DriftResponse", start_time=T0, task_queue="q"),
        RunningWorkflowInfo(
            workflow_id="scan-drift-dcfabric-9",
            workflow_type="ScanDrift",
            start_time=NOW - timedelta(seconds=7),
            task_queue="q",
        ),
    ]

    running = project_running(infos, runs, NOW, ui_address=UI, namespace="default")

    assert [r.workflow_id for r in running] == ["scan-drift-dcfabric-9", SCHEDULED]
    assert all(r.state is RunState.RUNNING for r in running)
    assert running[1].trigger is Trigger.SCHEDULE
    # Not in the trail yet: what started it is not known.
    assert running[0].trigger is Trigger.UNKNOWN
    assert running[0].duration_s == 7


# =============================================================================
# Repeated failed repairs (T025, research R7)
# =============================================================================


def _response(device_id, at, *, decision="remediated", result="failed"):
    payload = {"decision": decision, "scan_workflow_id": "s", "paths": ["/a"]}
    if decision == "remediated":
        payload |= {"reconcile_workflow_id": "r", "result": result}
    return _wf(
        SCHEDULED,
        "DriftResponse",
        AuditEventType.DRIFT_RESPONSE,
        at,
        target=device_id,
        payload=payload,
        actor="schedule:drift-response-dcfabric",
    )


def _failures(events):
    return project_repeated_failures(events, {LEAF: "leaf-01", SPINE: "spine-01"}, use_case="dcfabric")


def test_three_consecutive_failed_repairs_are_reported():
    events = [_response(LEAF, T0 + timedelta(minutes=5 * i)) for i in range(3)]

    [failure] = _failures(events)

    assert failure.device_id == LEAF
    assert failure.name == "leaf-01"
    assert failure.consecutive_failures == 3
    assert failure.last_failed_at == T0 + timedelta(minutes=10)
    assert failure.suggested_command == "snapl schedule mode report --use-case dcfabric"


def test_a_success_resets_the_count():
    events = [
        _response(LEAF, T0),
        _response(LEAF, T0 + timedelta(minutes=5)),
        _response(LEAF, T0 + timedelta(minutes=10), result="succeeded"),
        _response(LEAF, T0 + timedelta(minutes=15)),
        _response(LEAF, T0 + timedelta(minutes=20)),
    ]

    assert _failures(events) == []


def test_unknown_and_skipped_neither_count_nor_reset():
    events = [
        _response(LEAF, T0),
        _response(LEAF, T0 + timedelta(minutes=5), result="unknown"),
        _response(LEAF, T0 + timedelta(minutes=10)),
        _response(LEAF, T0 + timedelta(minutes=15), result="skipped"),
        _response(LEAF, T0 + timedelta(minutes=20)),
    ]

    [failure] = _failures(events)

    assert failure.consecutive_failures == 3


def test_a_run_that_did_not_attempt_the_device_does_not_reset():
    events = [
        _response(LEAF, T0),
        _response(LEAF, T0 + timedelta(minutes=5)),
        _response(LEAF, T0 + timedelta(minutes=10), decision="undesired_only"),
        _response(LEAF, T0 + timedelta(minutes=15)),
    ]

    [failure] = _failures(events)

    assert failure.consecutive_failures == 3
    assert failure.last_failed_at == T0 + timedelta(minutes=15)


def test_devices_under_the_threshold_are_omitted():
    events = [_response(LEAF, T0), _response(LEAF, T0 + timedelta(minutes=5)), _response(SPINE, T0)]

    assert _failures(events) == []


def test_use_case_level_responses_are_not_device_repairs():
    events = [
        _wf(
            SCHEDULED,
            "DriftResponse",
            AuditEventType.DRIFT_RESPONSE,
            T0 + timedelta(minutes=i),
            target="dcfabric",
            payload={"mode": "remediate", "remediated": 1},
            actor="schedule:drift-response-dcfabric",
        )
        for i in range(3)
    ]

    assert _failures(events) == []


# =============================================================================
# Schedule pause details
# =============================================================================


def _schedule_change(at, change, *, actor="anton", target="dcfabric"):
    return _wf(
        "drift-response-dcfabric",
        "DriftSchedule",
        AuditEventType.SCHEDULE_CHANGED,
        at,
        target=target,
        payload={"change": change},
        actor=actor,
    )


def test_latest_pause_for_the_use_case():
    events = [
        _schedule_change(T0, "paused", actor="alice"),
        _schedule_change(T0 + timedelta(minutes=1), "resumed", actor="bob"),
        _schedule_change(T0 + timedelta(minutes=2), "paused", actor="carol"),
        _schedule_change(T0 + timedelta(minutes=3), "paused", actor="dave", target="campus"),
    ]

    assert latest_pause(events, use_case="dcfabric") == ("carol", T0 + timedelta(minutes=2))


def test_no_pause_recorded():
    assert latest_pause([], use_case="dcfabric") is None


# =============================================================================
# History series (T033, research R8)
# =============================================================================

H0 = datetime(2026, 5, 1, tzinfo=UTC)
# Six months of history with a 30-day gap in the middle.
GAP_START = H0 + timedelta(days=60)
GAP_END = GAP_START + timedelta(days=30)


def _scan_run(workflow_id: str, at: datetime, *, drifted: dict[UUID, list[str]], clean: int = 1, ok: bool = True):
    """A ScanDrift run: start, one record per drifted device, and its end."""
    events = [_wf(workflow_id, "ScanDrift", AuditEventType.WORKFLOW_STARTED, at, target="dcfabric")]
    for device_id, undesired in drifted.items():
        events.append(
            _scan(
                device_id,
                at + timedelta(seconds=1),
                status="drifted",
                paths=["/a", *undesired],
                undesired=undesired,
                workflow_id=workflow_id,
            )
        )
    end_payload = {"total": len(drifted) + clean, "clean": clean, "drifted": len(drifted), "errored": 0}
    events.append(
        _wf(
            workflow_id,
            "ScanDrift",
            AuditEventType.WORKFLOW_TERMINATED,
            at + timedelta(seconds=2),
            target="dcfabric",
            outcome="success" if ok else "failure",
            reason=WorkflowReason.SUCCEEDED if ok else WorkflowReason.INTENT_UNAVAILABLE,
            payload=end_payload if ok else {"error": "SoT down"},
        )
    )
    return events


def _history():
    """Daily scans for six months, none in the gap, with one drift spike."""
    events = []
    day = H0
    while day < H0 + timedelta(days=180):
        if not GAP_START <= day < GAP_END:
            drifted = {LEAF: ["/x", "/y"], SPINE: []} if day == H0 + timedelta(days=10) else {}
            events += _scan_run(f"scan-{day:%Y%m%d}", day + timedelta(hours=3), drifted=drifted)
        day += timedelta(days=1)
    return events


def test_drifted_devices_is_one_point_per_scan_with_no_points_in_the_gap():
    events = _history()

    series = project_series(events, SeriesName.DRIFTED_DEVICES, H0, H0 + timedelta(days=180), Bucket.DAY)

    assert series.bucket is None
    assert len(series.points) == 150
    assert not [p for p in series.points if GAP_START <= p.t < GAP_END]
    spike = [p for p in series.points if p.value]
    assert [(p.t, p.value) for p in spike] == [(H0 + timedelta(days=10, hours=3, seconds=2), 2)]
    assert series.first_event_at == H0 + timedelta(hours=3)


def test_undesired_paths_counts_the_scans_undesired_paths():
    events = _history()

    series = project_series(
        events, SeriesName.UNDESIRED_PATHS, H0 + timedelta(days=9), H0 + timedelta(days=12), Bucket.HOUR
    )

    assert [p.value for p in series.points] == [0, 2, 0]


def test_undesired_paths_is_unknown_for_a_scan_recorded_before_fr_012a():
    events = _scan_run("scan-old", H0, drifted={})
    events.append(
        _scan(LEAF, H0 + timedelta(seconds=1), status="drifted", paths=["/a"], undesired=None, workflow_id="scan-old")
    )

    [point] = project_series(events, SeriesName.UNDESIRED_PATHS, H0, H0 + timedelta(days=1), Bucket.HOUR).points

    assert point.value is None


def test_a_failed_scan_is_not_a_point():
    events = _scan_run("scan-failed", H0, drifted={}, ok=False)

    assert project_series(events, SeriesName.DRIFTED_DEVICES, H0, H0 + timedelta(days=1), Bucket.HOUR).points == []


def _deploy_end(at: datetime, *, ok: bool = True, device: UUID = LEAF):
    return _run(
        f"deploy-intent-{device}",
        "DeployIntent",
        at - timedelta(seconds=3),
        at,
        target=device,
        ok=ok,
        start_payload={"parent_workflow_id": None},
    )


def test_deploys_are_counted_per_bucket_by_outcome_with_null_for_empty_buckets():
    start = H0
    events = [
        *_deploy_end(start + timedelta(minutes=10)),
        *_deploy_end(start + timedelta(minutes=20), ok=False),
        *_deploy_end(start + timedelta(minutes=30)),
        *_scan_run("scan-2", start + timedelta(hours=2, minutes=5), drifted={}),
    ]

    series = project_series(events, SeriesName.DEPLOYS, start, start + timedelta(hours=3), Bucket.HOUR)

    assert series.bucket is Bucket.HOUR
    assert [(p.t, p.by_outcome) for p in series.points] == [
        (start, {"succeeded": 2, "failed": 1}),
        # Nothing ran: no data, not zero.
        (start + timedelta(hours=1), {"succeeded": None, "failed": None}),
        # A scan ran and no deploy did: a real zero.
        (start + timedelta(hours=2), {"succeeded": 0, "failed": 0}),
    ]


def test_repairs_are_counted_per_bucket_by_result():
    start = H0
    events = [
        *_scan_run("scan-r", start + timedelta(minutes=1), drifted={LEAF: []}),
        _response(LEAF, start + timedelta(minutes=2), result="succeeded"),
        _response(SPINE, start + timedelta(minutes=2), result="failed"),
        _response(LEAF, start + timedelta(minutes=3), result="unknown"),
        _response(LEAF, start + timedelta(minutes=4), decision="undesired_only"),
    ]

    [point] = project_series(events, SeriesName.REPAIRS, start, start + timedelta(hours=1), Bucket.HOUR).points

    assert point.by_outcome == {"succeeded": 1, "failed": 1}


def test_markers_for_reconciles_and_remediating_responses():
    events = [
        *_run("reconcile-1", "ReconcileDevices", H0 + timedelta(minutes=5), H0 + timedelta(minutes=6)),
        *_run(
            SCHEDULED,
            "DriftResponse",
            H0 + timedelta(minutes=10),
            H0 + timedelta(minutes=11),
            target="dcfabric",
            actor="schedule:drift-response-dcfabric",
            start_payload={"mode": "remediate"},
        ),
        *_run(
            "drift-response-report",
            "DriftResponse",
            H0 + timedelta(minutes=15),
            H0 + timedelta(minutes=16),
            target="dcfabric",
            actor="schedule:drift-response-dcfabric",
            start_payload={"mode": "report"},
        ),
        # Outside the range.
        *_run("reconcile-2", "ReconcileDevices", H0 + timedelta(days=2), H0 + timedelta(days=2, minutes=1)),
    ]

    series = project_series(events, SeriesName.DEPLOYS, H0, H0 + timedelta(hours=1), Bucket.HOUR)

    assert [(m.kind, m.workflow_id, m.trigger) for m in series.markers] == [
        ("reconcile", "reconcile-1", Trigger.OPERATOR),
        ("remediation", SCHEDULED, Trigger.SCHEDULE),
    ]
    assert series.markers[0].t == H0 + timedelta(minutes=5)


def test_bucket_edges_are_utc_aligned():
    start = datetime(2026, 5, 6, 10, 37, tzinfo=UTC)  # a Wednesday
    events = _deploy_end(start + timedelta(minutes=1))

    hourly = project_series(events, SeriesName.DEPLOYS, start, start + timedelta(hours=2), Bucket.HOUR)
    five = project_series(events, SeriesName.DEPLOYS, start, start + timedelta(minutes=10), Bucket.FIVE_MINUTES)
    daily = project_series(events, SeriesName.DEPLOYS, start, start + timedelta(days=1), Bucket.DAY)
    weekly = project_series(events, SeriesName.DEPLOYS, start, start + timedelta(days=1), Bucket.WEEK)

    assert hourly.points[0].t == datetime(2026, 5, 6, 10, tzinfo=UTC)
    assert five.points[0].t == datetime(2026, 5, 6, 10, 35, tzinfo=UTC)
    assert daily.points[0].t == datetime(2026, 5, 6, tzinfo=UTC)
    # Weeks start on Monday.
    assert weekly.points[0].t == datetime(2026, 5, 4, tzinfo=UTC)
    assert [p.t for p in hourly.points] == [datetime(2026, 5, 6, h, tzinfo=UTC) for h in (10, 11, 12)]


def test_more_than_2000_buckets_is_refused():
    with pytest.raises(ValueError, match="2,000"):
        project_series([], SeriesName.DEPLOYS, H0, H0 + timedelta(minutes=5 * 2001), Bucket.FIVE_MINUTES)
    assert project_series([], SeriesName.DEPLOYS, H0, H0 + timedelta(minutes=5 * 2000), Bucket.FIVE_MINUTES)


def test_empty_trail_has_no_first_event():
    series = project_series([], SeriesName.DRIFTED_DEVICES, H0, H0 + timedelta(hours=1), Bucket.HOUR)

    assert series.first_event_at is None
    assert series.points == []
