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
from snapl_presentation.dashboard.projections import STEP_BLOCKS, project_steps, run_events
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


async def test_step_blocks_covers_exactly_what_deploys_record():
    """A step the workflow starts recording must be given a block here."""
    recorded = {e.activity_name for e in await arc_events() if e.workflow_type == "DeployIntent" and e.activity_name}

    assert set(STEP_BLOCKS) == recorded == set(STEPS)


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
