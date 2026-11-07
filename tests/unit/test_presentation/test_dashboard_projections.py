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
from snapl_presentation.dashboard.models import DriftState
from snapl_presentation.dashboard.projections import project_devices

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
