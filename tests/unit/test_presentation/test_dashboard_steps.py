"""The steps one run recorded, by NAF block (spec 011 T012, research R15).

The successful shapes come from the captured demo arc, and a deploy that failed
at apply comes from a capture too (tasks.md T057, fixtures/README.md). The other
failure paths are NOT captured: they are built from what ``deploy_intent.py``
writes on each one (read 2026-11-10). The capture matched that reading, so they
stay built the same way.

What the workflow records, and so what this projection can say:
- one ``ACTIVITY_COMPLETED`` after each step that succeeded, in order;
- nothing when a step starts, and no ``ACTIVITY_FAILED`` when one fails;
- a failed run's end, with a reason code. ``collect_failed`` covers both a
  failed collect and a failed drift check, so the reason alone does not name
  the block. The block that failed is the first one with no record.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from snapl_orchestrator.models import AuditEvent, AuditEventType, WorkflowReason
from snapl_presentation.dashboard.models import BlockStatus, NafBlock, RunState, RunView, Trigger
from snapl_presentation.dashboard.projections import STEP_BLOCKS, project_steps, run_events, scan_of_response
from tests.unit.test_presentation.dashboard_fakes import ARC, arc_events, failed_deploy_events

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 7, 21, 0, tzinfo=UTC)
LEAF = UUID("00000000-0000-0000-0000-000000000002")
WF = f"deploy-intent-{LEAF}"
STEPS = ["fetch_desired_state", "apply_config", "collect_drift_state", "detect_drift"]
LOOP = [
    NafBlock.INTENT,
    NafBlock.EXECUTOR,
    NafBlock.COLLECTOR,
    NafBlock.OBSERVABILITY,
    NafBlock.ORCHESTRATOR,
    NafBlock.PRESENTATION,
]


def _event(seconds: float, event_type: AuditEventType, **kwargs):
    return AuditEvent(
        event_id=uuid4(),
        workflow_id=kwargs.pop("workflow_id", WF),
        workflow_type=kwargs.pop("workflow_type", "DeployIntent"),
        target_id=kwargs.pop("target_id", LEAF),
        event_type=event_type,
        timestamp=T0 + timedelta(seconds=seconds),
        **kwargs,
    )


def _deploy(done: int, *, reason: WorkflowReason | None, payload: dict | None = None) -> list[AuditEvent]:
    """A DeployIntent run with its first ``done`` steps recorded, ended with ``reason`` (None: still open)."""
    at = 0.0
    events = [_event(at, AuditEventType.WORKFLOW_STARTED, payload={"parent_workflow_id": None})]
    events += [
        _event(at + index + 1, AuditEventType.ACTIVITY_COMPLETED, activity_name=name, outcome="success")
        for index, name in enumerate(STEPS[:done])
    ]
    if reason is WorkflowReason.CANCELLED:
        events.append(_event(at + 9, AuditEventType.WORKFLOW_CANCELLED, reason=reason))
    elif reason is not None:
        success = reason is WorkflowReason.SUCCEEDED
        events.append(
            _event(
                at + 9,
                AuditEventType.WORKFLOW_TERMINATED,
                outcome="success" if success else "failure",
                reason=reason,
                payload=payload or {},
            )
        )
    return events


def _run(events: list[AuditEvent], state: RunState, workflow_type: str = "DeployIntent") -> RunView:
    return RunView(
        workflow_id=events[0].workflow_id,
        workflow_type=workflow_type,
        target="leaf-01",
        started_at=events[0].timestamp,
        state=state,
        trigger=Trigger.OPERATOR,
        web_ui_url="http://localhost:18034/x",
    )


def _statuses(steps) -> list[BlockStatus]:
    assert [block.block for block in steps.blocks] == LOOP
    return [block.status for block in steps.blocks]


DONE, FAILED, NOT_REACHED = BlockStatus.DONE, BlockStatus.FAILED, BlockStatus.NOT_REACHED
TAIL = [BlockStatus.DONE, BlockStatus.SURFACE]  # Orchestrator, Presentation


# --- the captured demo arc ---------------------------------------------------------


async def test_a_captured_deploy_has_four_steps_done_in_loop_order():
    events = await arc_events()
    device = ARC["spine-01"][1]
    start = min(e.timestamp for e in events if e.workflow_id == f"deploy-intent-{device}")
    of_run = run_events(events, f"deploy-intent-{device}", start)

    steps = project_steps(_run(of_run, RunState.SUCCEEDED), of_run)

    assert steps.recorded is True
    assert [(step.name, step.block, step.outcome) for step in steps.steps] == [
        ("fetch_desired_state", NafBlock.INTENT, "done"),
        ("apply_config", NafBlock.EXECUTOR, "done"),
        ("collect_drift_state", NafBlock.COLLECTOR, "done"),
        ("detect_drift", NafBlock.OBSERVABILITY, "done"),
    ]
    assert _statuses(steps) == [DONE, DONE, DONE, DONE, *TAIL]
    times = [block.t for block in steps.blocks[:4]]
    assert times == sorted(times) == [step.t for step in steps.steps]
    assert steps.blocks[4].t == start
    assert steps.blocks[5].t is None


async def test_step_blocks_covers_exactly_what_deploys_and_scans_record():
    """A step a workflow starts recording must be given a block here."""
    recorded = {e.activity_name for e in await arc_events() if e.workflow_type == "DeployIntent" and e.activity_name}

    assert recorded == set(STEPS)
    # Spec 015 FR-016: a scan records reading intent for the use case too.
    assert set(STEP_BLOCKS) == {*STEPS, "fetch_devices_for_use_case"}


async def test_run_events_picks_one_run_of_a_reused_workflow_id():
    events = await arc_events()
    workflow_id = f"deploy-intent-{ARC['spine-01'][1]}"  # deployed twice in the arc
    starts = sorted(
        e.timestamp for e in events if e.workflow_id == workflow_id and e.event_type is AuditEventType.WORKFLOW_STARTED
    )

    first, second = run_events(events, workflow_id, starts[0]), run_events(events, workflow_id, starts[1])

    assert (len(first), len(second)) == (6, 6)
    assert first[-1].timestamp < second[0].timestamp
    assert run_events(events, workflow_id, starts[0] + timedelta(microseconds=1)) == []
    assert run_events(events, "no-such-workflow", starts[0]) == []


# --- a failed deploy, captured ---------------------------------------------------------


async def test_a_captured_deploy_that_failed_at_apply_fails_the_executor():
    """leaf-04 paused in the lab, then ``snapl deploy leaf-04``: what the workflow really wrote."""
    events = await failed_deploy_events()
    assert [(e.event_type, e.activity_name, e.reason) for e in events] == [
        (AuditEventType.WORKFLOW_STARTED, None, None),
        (AuditEventType.ACTIVITY_COMPLETED, "fetch_desired_state", None),
        (AuditEventType.WORKFLOW_TERMINATED, None, WorkflowReason.APPLY_FAILED),
    ]

    steps = project_steps(_run(events, RunState.FAILED), events)

    assert _statuses(steps) == [DONE, FAILED, NOT_REACHED, NOT_REACHED, *TAIL]
    assert steps.blocks[1].reason == "apply_failed: apply reported success=False"
    assert steps.blocks[1].t == events[-1].timestamp
    assert steps.blocks[0].t == events[1].timestamp
    assert all(block.reason is None for block in steps.blocks if block.status is not FAILED)


# --- the other failed deploys (built from deploy_intent.py, not captured) ---------------


@pytest.mark.parametrize(
    ("done", "reason", "expected"),
    [
        (0, WorkflowReason.INTENT_UNAVAILABLE, [FAILED, NOT_REACHED, NOT_REACHED, NOT_REACHED]),
        (0, WorkflowReason.DEVICE_NOT_FOUND, [FAILED, NOT_REACHED, NOT_REACHED, NOT_REACHED]),
        (2, WorkflowReason.COLLECT_FAILED, [DONE, DONE, FAILED, NOT_REACHED]),
        # The drift check itself failed: the workflow ends it with collect_failed
        # too, after the collect step was recorded.
        (3, WorkflowReason.COLLECT_FAILED, [DONE, DONE, DONE, FAILED]),
    ],
)
def test_a_failed_deploy_fails_at_the_first_step_with_no_record(done, reason, expected):
    events = _deploy(done, reason=reason, payload={"detail": "boom\nsecond line"})

    steps = project_steps(_run(events, RunState.FAILED), events)

    assert _statuses(steps) == [*expected, *TAIL]
    failed = next(block for block in steps.blocks if block.status is FAILED)
    assert failed.reason == f"{reason.value}: boom"
    assert failed.t == events[-1].timestamp
    assert all(block.reason is None for block in steps.blocks if block.status is not FAILED)


def test_drift_left_after_a_deploy_fails_observability_though_its_step_ran():
    events = _deploy(4, reason=WorkflowReason.VERIFICATION_FAILED, payload={"drift_paths": ["/a", "/b"]})

    steps = project_steps(_run(events, RunState.FAILED), events)

    assert _statuses(steps) == [DONE, DONE, DONE, FAILED, *TAIL]
    assert steps.blocks[3].reason == "verification_failed: 2 path(s) still differ"


def test_a_cancelled_deploy_blames_no_block():
    events = _deploy(2, reason=WorkflowReason.CANCELLED)

    steps = project_steps(_run(events, RunState.CANCELLED), events)

    assert _statuses(steps) == [DONE, DONE, NOT_REACHED, NOT_REACHED, *TAIL]


def test_a_deploy_whose_audit_write_failed_blames_no_block():
    events = _deploy(1, reason=WorkflowReason.AUDIT_FAILED)

    steps = project_steps(_run(events, RunState.FAILED), events)

    assert _statuses(steps)[:4] == [DONE, NOT_REACHED, NOT_REACHED, NOT_REACHED]


def test_a_deploy_in_progress_marks_only_what_is_recorded_and_nothing_as_in_progress():
    events = _deploy(2, reason=None)

    steps = project_steps(_run(events, RunState.RUNNING), events)

    assert _statuses(steps) == [DONE, DONE, NOT_REACHED, NOT_REACHED, *TAIL]


# --- other run types -----------------------------------------------------------------


@pytest.mark.parametrize("workflow_type", ["ScanDrift", "ReconcileDevices", "DriftResponse"])
def test_a_run_type_that_records_no_steps_one_by_one_says_so(workflow_type: str):
    of_type = {"workflow_id": "wf-1", "workflow_type": workflow_type}
    events = [
        _event(0, AuditEventType.WORKFLOW_STARTED, **of_type, target_id="dcfabric"),
        _event(1, AuditEventType.ACTIVITY_COMPLETED, **of_type, activity_name="detect_drift", outcome="success"),
    ]

    steps = project_steps(_run(events, RunState.SUCCEEDED, workflow_type), events)

    assert steps.recorded is False
    assert _statuses(steps) == [BlockStatus.NOT_RECORDED] * 4 + TAIL
    # What was recorded is still listed, with its block.
    assert [(step.name, step.block) for step in steps.steps] == [("detect_drift", NafBlock.OBSERVABILITY)]


def test_an_unknown_step_is_returned_with_no_block_never_dropped():
    events = _deploy(1, reason=None)
    events.append(_event(5, AuditEventType.ACTIVITY_COMPLETED, activity_name="render_config", outcome="success"))

    steps = project_steps(_run(events, RunState.RUNNING), events)

    assert [(step.name, step.block) for step in steps.steps] == [
        ("fetch_desired_state", NafBlock.INTENT),
        ("render_config", None),
    ]


def test_a_recorded_step_failure_is_a_failed_step():
    """No workflow writes this for a deploy today; a scan writes it per device."""
    scan = {"workflow_id": "scan-1", "workflow_type": "ScanDrift"}
    events = [
        _event(0, AuditEventType.WORKFLOW_STARTED, **scan, target_id="dcfabric"),
        _event(
            1,
            AuditEventType.ACTIVITY_FAILED,
            **scan,
            activity_name="detect_drift",
            outcome="failure",
            payload={"error": "gNMI timeout\ntrace"},
        ),
    ]

    (step,) = project_steps(_run(events, RunState.SUCCEEDED, "ScanDrift"), events).steps

    assert (step.outcome, step.reason) == ("failed", "gNMI timeout")


# --- a scan's steps (spec 015 US4, T040) ---------------------------------------------

SCAN = {"workflow_id": "scan-1", "workflow_type": "ScanDrift"}
SPINE = UUID("00000000-0000-0000-0000-000000000001")


def _scan(*, collect_fails: UUID | None = None, intent_fails: bool = False, of: dict | None = None, at: float = 0):
    """A ScanDrift run as `scan_drift.py` records it since spec 015 (FR-016)."""
    of = of or SCAN
    events = [_event(at, AuditEventType.WORKFLOW_STARTED, **of, target_id="dcfabric")]
    if intent_fails:
        return [
            *events,
            _event(
                at + 1,
                AuditEventType.ACTIVITY_FAILED,
                **of,
                target_id="dcfabric",
                activity_name="fetch_devices_for_use_case",
                outcome="failure",
                payload={"error": "intent fetch failed: Infrahub unreachable"},
            ),
        ]
    events.append(
        _event(
            at + 1,
            AuditEventType.ACTIVITY_COMPLETED,
            **of,
            target_id="dcfabric",
            activity_name="fetch_devices_for_use_case",
            outcome="success",
            payload={"devices": 2},
        )
    )
    for offset, device in ((2, SPINE), (3, LEAF)):
        failed = device == collect_fails
        error = {"error": "collect failed: leaf-01 unreachable"}
        events.append(
            _event(
                at + offset,
                AuditEventType.ACTIVITY_FAILED if failed else AuditEventType.ACTIVITY_COMPLETED,
                **of,
                target_id=device,
                activity_name="collect_drift_state",
                outcome="failure" if failed else "success",
                payload=error if failed else {},
            )
        )
        events.append(
            _event(
                at + offset + 2,
                AuditEventType.ACTIVITY_FAILED if failed else AuditEventType.ACTIVITY_COMPLETED,
                **of,
                target_id=device,
                activity_name="detect_drift",
                outcome="failure" if failed else "success",
                payload={"status": "error", "drift_items": 0, **error}
                if failed
                else {"status": "clean", "drift_items": 0},
            )
        )
    events.append(
        _event(
            at + 9,
            AuditEventType.WORKFLOW_TERMINATED,
            **of,
            target_id="dcfabric",
            outcome="success",
            reason=WorkflowReason.SUCCEEDED,
        )
    )
    return events


def _blocks(steps) -> dict:
    return {block.block: block for block in steps.blocks}


def test_step_blocks_names_a_block_for_each_step_a_scan_records():
    assert STEP_BLOCKS["fetch_devices_for_use_case"] is NafBlock.INTENT
    assert STEP_BLOCKS["collect_drift_state"] is NafBlock.COLLECTOR


def test_a_scan_that_records_its_steps_shows_intent_collector_and_observability_done():
    """Acceptance 1: done, each with the time of its block's last step; Executor not reached."""
    events = _scan()

    steps = project_steps(_run(events, RunState.SUCCEEDED, "ScanDrift"), events)

    assert steps.recorded is True
    assert _statuses(steps) == [DONE, NOT_REACHED, DONE, DONE, *TAIL]
    blocks = _blocks(steps)
    assert blocks[NafBlock.INTENT].t == T0 + timedelta(seconds=1)
    assert blocks[NafBlock.COLLECTOR].t == T0 + timedelta(seconds=3)
    assert blocks[NafBlock.OBSERVABILITY].t == T0 + timedelta(seconds=5)


def test_a_scan_that_could_not_collect_a_device_fails_the_collector_with_its_reason():
    """Acceptance 2. The device's failed drift check follows from its collect: Observability ran for the rest."""
    events = _scan(collect_fails=LEAF)

    steps = project_steps(_run(events, RunState.SUCCEEDED, "ScanDrift"), events)

    assert _statuses(steps) == [DONE, NOT_REACHED, FAILED, DONE, *TAIL]
    collector = _blocks(steps)[NafBlock.COLLECTOR]
    assert collector.reason == "collect failed: leaf-01 unreachable"
    assert collector.t == T0 + timedelta(seconds=3)
    assert _blocks(steps)[NafBlock.OBSERVABILITY].t == T0 + timedelta(seconds=4)


def test_a_scan_whose_drift_check_failed_on_its_own_fails_observability():
    events = _scan()
    check = next(e for e in events if e.activity_name == "detect_drift" and e.target_id == LEAF)
    events[events.index(check)] = check.model_copy(
        update={"event_type": AuditEventType.ACTIVITY_FAILED, "outcome": "failure", "payload": {"error": "boom"}}
    )

    steps = project_steps(_run(events, RunState.SUCCEEDED, "ScanDrift"), events)

    assert _statuses(steps) == [DONE, NOT_REACHED, DONE, FAILED, *TAIL]
    assert _blocks(steps)[NafBlock.OBSERVABILITY].reason == "boom"


def test_a_scan_that_could_not_read_intent_fails_intent_and_reaches_nothing_else():
    events = _scan(intent_fails=True)

    steps = project_steps(_run(events, RunState.FAILED, "ScanDrift"), events)

    assert _statuses(steps) == [FAILED, NOT_REACHED, NOT_REACHED, NOT_REACHED, *TAIL]
    assert _blocks(steps)[NafBlock.INTENT].reason == "intent fetch failed: Infrahub unreachable"


def test_a_scan_recorded_before_spec_015_still_says_its_steps_are_not_recorded():
    """Acceptance 4 (FR-018): no intent step, so the drift checks alone do not make it recorded."""
    events = [e for e in _scan() if e.activity_name in {None, "detect_drift"}]

    steps = project_steps(_run(events, RunState.SUCCEEDED, "ScanDrift"), events)

    assert steps.recorded is False
    assert _statuses(steps) == [BlockStatus.NOT_RECORDED] * 4 + TAIL


# --- a drift response's steps: its scan's, and its repairs (T040, FR-017) -------------

RESPONSE = {"workflow_id": "drift-response-1", "workflow_type": "DriftResponse"}


def _response(*, remediated: dict[UUID, str] | None = None, scan: list[AuditEvent] | None = None):
    """A DriftResponse run, its scan child (`<id>-scan`) and its per-device decisions."""
    own = [_event(0, AuditEventType.WORKFLOW_STARTED, **RESPONSE, target_id="dcfabric", payload={"mode": "remediate"})]
    child = scan if scan is not None else _scan(of={**SCAN, "workflow_id": "drift-response-1-scan"}, at=0.5)
    for index, (device, result) in enumerate((remediated or {}).items()):
        own.append(
            _event(
                20 + index,
                AuditEventType.DRIFT_RESPONSE,
                **RESPONSE,
                target_id=device,
                payload={
                    "decision": "remediated",
                    "result": result,
                    "reconcile_workflow_id": "drift-response-1-reconcile",
                },
            )
        )
    own.append(
        _event(
            30,
            AuditEventType.WORKFLOW_TERMINATED,
            **RESPONSE,
            target_id="dcfabric",
            outcome="success",
            reason=WorkflowReason.SUCCEEDED,
        )
    )
    return own, child


def test_a_drift_response_that_found_nothing_shows_its_scan_s_blocks_and_no_executor():
    """Acceptance 3."""
    own, child = _response()

    steps = project_steps(_run(own, RunState.SUCCEEDED, "DriftResponse"), own, scan_events=child)

    assert steps.recorded is True
    assert _statuses(steps) == [DONE, NOT_REACHED, DONE, DONE, *TAIL]
    assert [step.name for step in steps.steps] == [
        "fetch_devices_for_use_case",
        "collect_drift_state",
        "collect_drift_state",
        "detect_drift",
        "detect_drift",
    ]


def test_a_drift_response_that_repaired_shows_the_executor_done_at_its_last_successful_repair():
    """A device the reconcile skipped (its deploy was in flight) is neither done nor failed here."""
    own, child = _response(remediated={SPINE: "succeeded", LEAF: "skipped"})

    steps = project_steps(_run(own, RunState.SUCCEEDED, "DriftResponse"), own, scan_events=child)

    assert _statuses(steps) == [DONE, DONE, DONE, DONE, *TAIL]
    assert _blocks(steps)[NafBlock.EXECUTOR].t == T0 + timedelta(seconds=20)


@pytest.mark.parametrize("result", ["failed", "unknown"])
def test_a_drift_response_whose_repair_failed_fails_the_executor(result: str):
    own, child = _response(remediated={SPINE: "succeeded", LEAF: result})

    steps = project_steps(_run(own, RunState.SUCCEEDED, "DriftResponse"), own, scan_events=child)

    assert _statuses(steps) == [DONE, FAILED, DONE, DONE, *TAIL]
    assert _blocks(steps)[NafBlock.EXECUTOR].reason == "the repair did not succeed on 1 device"


def test_a_drift_response_whose_scan_predates_spec_015_is_not_recorded():
    old_scan = [
        e
        for e in _scan(of={**SCAN, "workflow_id": "drift-response-1-scan"})
        if e.activity_name != "fetch_devices_for_use_case"
    ]
    own, child = _response(scan=old_scan)

    steps = project_steps(_run(own, RunState.SUCCEEDED, "DriftResponse"), own, scan_events=child)

    assert steps.recorded is False
    assert _statuses(steps) == [BlockStatus.NOT_RECORDED] * 4 + TAIL


def test_a_drift_response_s_scan_is_found_by_its_child_id_and_start():
    own, child = _response()
    later = _scan(of={**SCAN, "workflow_id": "drift-response-1-scan"}, at=100)
    trail = [*own, *child, *later, *_scan()]

    assert scan_of_response(trail, "drift-response-1", own[0].timestamp) == sorted(child, key=lambda e: e.timestamp)
    assert scan_of_response(trail, "no-such-run", own[0].timestamp) == []
