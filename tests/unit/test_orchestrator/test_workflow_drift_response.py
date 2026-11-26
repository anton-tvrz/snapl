"""Unit tests for DriftResponseWorkflow — one scheduled run (spec 009).

The schedule is only the trigger, so the workflow is started directly with a
``DriftResponseRequest``. The real ScanDrift, ReconcileDevices and
DeployIntent children run against a fake fabric whose devices hold forward and
undesired drift: a deploy clears the forward drift and leaves the undesired
config in place, which is what the live fabric does (#121).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pydantic_core  # noqa: F401 — pre-import to keep workflow sandbox quiet
import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError, WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from snapl_collector.models import CollectResult
from snapl_executor.models import ApplyResult
from snapl_observability.models import DriftItem, DriftReport, DriftStatus
from snapl_orchestrator.activities import Activities, set_activities
from snapl_orchestrator.activities.audit import record_audit_event
from snapl_orchestrator.activities.collector import collect_drift_state
from snapl_orchestrator.activities.executor import apply_config
from snapl_orchestrator.activities.intent import fetch_desired_state, fetch_devices_for_use_case
from snapl_orchestrator.activities.observability import detect_drift
from snapl_orchestrator.audit.memory import InMemoryAuditLog
from snapl_orchestrator.exceptions import AuditLogError
from snapl_orchestrator.models import (
    AuditEventType,
    DriftResponseMode,
    DriftResponseRequest,
    ExclusionReason,
    StandDownRequest,
    WorkflowReason,
)
from snapl_orchestrator.worker.sandbox import build_workflow_runner
from snapl_orchestrator.workflows.deploy_intent import DeployIntentWorkflow
from snapl_orchestrator.workflows.drift_response import DriftResponseWorkflow
from snapl_orchestrator.workflows.reconcile_devices import ReconcileDevicesWorkflow
from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow

from .quiet import SlowAfterCancel, until_quiet, where_it_stopped

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).parent / "fixtures"

TASK_QUEUE = "test-drift-response"
USE_CASE = "dcfabric"
SCHEDULE_ID = "drift-response-dcfabric"
RUN_ID = "drift-response-dcfabric-2026-10-28T03:00:00Z"
ACTOR = f"schedule:{SCHEDULE_ID}"

FORWARD = DriftItem(
    path="/interface[name=ethernet-1/1]/admin-state",
    desired="enable",
    actual="disable",
    entity_kind="Interface",
)
UNDESIRED = DriftItem(
    path="/interface[name=ethernet-1/7]/description",
    desired=None,
    actual="hand-added",
    entity_kind="Interface",
    undesired=True,
)


def _now() -> datetime:
    return datetime.now(tz=UTC)


class Fabric:
    """A fake fabric: per-device drift items, an apply that heals forward drift.

    ``unreachable`` devices fail to collect, so the scan reports them ERROR.
    ``rejecting`` devices fail their apply. When ``gate`` is set, every apply
    sets ``apply_started`` and then waits for the gate, holding the repair
    between apply and verify.
    """

    def __init__(self, states: list) -> None:
        self.states = states
        self.by_id = {s.device.id: s for s in states}
        self.drift: dict = {s.device.id: [] for s in states}
        self.unreachable: set = set()
        self.rejecting: set = set()
        self.applied: list = []
        self.gate: asyncio.Event | None = None
        self.apply_started = asyncio.Event()

    def activities(self, audit: InMemoryAuditLog) -> Activities:
        async def _get_desired(*, use_case=None, device_id=None):
            if use_case is not None:
                return self.states
            return [self.by_id[device_id]] if device_id in self.by_id else []

        async def _apply(desired):
            self.applied.append(desired.device.id)
            if self.gate is not None:
                self.apply_started.set()
                await self.gate.wait()
            if desired.device.id in self.rejecting:
                return ApplyResult(
                    device_id=desired.device.id,
                    device_name=desired.device.name,
                    success=False,
                    payload={},
                    error="rejected",
                    duration_ms=5,
                )
            self.drift[desired.device.id] = [i for i in self.drift[desired.device.id] if i.undesired]
            return ApplyResult(
                device_id=desired.device.id,
                device_name=desired.device.name,
                success=True,
                payload={"/interface": []},
                duration_ms=5,
            )

        async def _collect(device, _paths=None):
            if device.id in self.unreachable:
                raise ConnectionError("device unreachable")
            return CollectResult(
                device_id=device.id,
                device_name=device.name,
                success=True,
                data={"/interface": []},
                paths=["/interface"],
            )

        def _detect(desired, _collected) -> DriftReport:
            items = self.drift[desired.device.id]
            return DriftReport(
                device_id=desired.device.id,
                device_name=desired.device.name,
                status=DriftStatus.DRIFTED if items else DriftStatus.CLEAN,
                items=list(items),
                timestamp=_now(),
            )

        intent_store = MagicMock()
        intent_store.get_desired_state = AsyncMock(side_effect=_get_desired)
        executor = MagicMock()
        executor.apply = AsyncMock(side_effect=_apply)
        collector = MagicMock()
        collector.collect = AsyncMock(side_effect=_collect)
        collector.get_running_config = AsyncMock(side_effect=_collect)
        observer = MagicMock()
        observer.detect_drift = AsyncMock(side_effect=_detect)
        observer.emit_event = AsyncMock()
        return Activities(
            intent_store=intent_store,
            executor=executor,
            collector=collector,
            observer=observer,
            audit_log=audit,
        )


class RefusingAuditLog(InMemoryAuditLog):
    """Refuses one workflow type's terminal record, as a locked or full disk would."""

    def __init__(self, workflow_type: str) -> None:
        super().__init__()
        self._workflow_type = workflow_type

    async def append(self, event) -> None:
        if event.workflow_type == self._workflow_type and event.event_type is AuditEventType.WORKFLOW_TERMINATED:
            raise AuditLogError("database is locked")
        await super().append(event)


def teardown_function() -> None:
    import snapl_orchestrator.activities as a

    a._activities = None


# What the run asked the schedule to do. The real activity changes a Temporal
# schedule through the worker's client (tested in test_activity_schedule.py);
# here a stand-in with the same name records the request.
STAND_DOWNS: list[StandDownRequest] = []


@activity.defn(name="stand_down_schedule")
async def _recording_stand_down(request: StandDownRequest) -> bool:
    STAND_DOWNS.append(request)
    return True


@pytest.fixture(autouse=True)
def _no_stand_downs_yet() -> None:
    STAND_DOWNS.clear()


_WORKFLOWS = [DriftResponseWorkflow, ScanDriftWorkflow, ReconcileDevicesWorkflow, DeployIntentWorkflow]
_ACTIVITIES = [
    fetch_devices_for_use_case,
    fetch_desired_state,
    apply_config,
    collect_drift_state,
    detect_drift,
    record_audit_event,
    _recording_stand_down,
]


def _request(mode: DriftResponseMode) -> DriftResponseRequest:
    return DriftResponseRequest(use_case_id=USE_CASE, mode=mode, schedule_id=SCHEDULE_ID)


async def _run(deps: Activities, mode: DriftResponseMode):
    set_activities(deps)
    async with (
        await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env,
        Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=_WORKFLOWS,
            workflow_runner=build_workflow_runner(),
            activities=_ACTIVITIES,
        ),
    ):
        return await env.client.execute_workflow(
            DriftResponseWorkflow.run, _request(mode), id=RUN_ID, task_queue=TASK_QUEUE
        )


async def _response_events(audit: InMemoryAuditLog, *, target=None):
    events = await audit.query_by_workflow(RUN_ID)
    return [
        e for e in events if e.event_type is AuditEventType.DRIFT_RESPONSE and (target is None or e.target_id == target)
    ]


@pytest.fixture
def fabric(make_desired) -> Fabric:
    return Fabric([make_desired(f"spine-0{i}") for i in range(1, 4)])


# ---------------------------------------------------------------------------
# Report (US1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_run_scans_and_writes_nothing(fabric: Fabric) -> None:
    drifted = fabric.states[0].device.id
    fabric.drift[drifted] = [FORWARD]
    audit = InMemoryAuditLog()

    result = await _run(fabric.activities(audit), DriftResponseMode.REPORT)

    assert result.mode is DriftResponseMode.REPORT
    assert result.workflow_id == RUN_ID
    assert result.schedule_id == SCHEDULE_ID
    assert result.scan.workflow_id == f"{RUN_ID}-scan"
    assert (result.scan.total, result.scan.drifted) == (3, 1)
    assert result.remediated == []
    assert result.excluded == {}
    assert result.reconcile is None
    assert fabric.applied == []
    assert fabric.drift[drifted] == [FORWARD]


@pytest.mark.asyncio
async def test_report_run_is_attributed_to_the_schedule(fabric: Fabric) -> None:
    fabric.drift[fabric.states[1].device.id] = [FORWARD]
    audit = InMemoryAuditLog()

    await _run(fabric.activities(audit), DriftResponseMode.REPORT)

    events = await audit.query_by_workflow(RUN_ID)
    assert {e.workflow_type for e in events} == {"DriftResponse"}
    assert {e.actor for e in events} == {ACTOR}
    types = [e.event_type for e in events]
    assert types[0] is AuditEventType.WORKFLOW_STARTED
    assert events[0].payload == {"mode": "report"}
    assert types[-1] is AuditEventType.WORKFLOW_TERMINATED
    assert events[-1].reason is WorkflowReason.SUCCEEDED

    [summary] = await _response_events(audit)
    assert summary.target_id == USE_CASE
    assert summary.payload == {
        "mode": "report",
        "scan_workflow_id": f"{RUN_ID}-scan",
        "clean": 2,
        "drifted": 1,
        "errored": 0,
        "remediated": 0,
        "excluded": {},
        "reconcile_workflow_id": None,
    }


@pytest.mark.asyncio
async def test_report_run_starts_no_reconcile_even_with_eligible_drift(fabric: Fabric) -> None:
    for state in fabric.states:
        fabric.drift[state.device.id] = [FORWARD]
    audit = InMemoryAuditLog()

    await _run(fabric.activities(audit), DriftResponseMode.REPORT)

    reconcile_events = await audit.query_by_workflow(f"{RUN_ID}-reconcile")
    assert reconcile_events == []
    assert fabric.applied == []


@pytest.mark.asyncio
async def test_a_failed_scan_is_recorded_and_the_run_fails(fabric: Fabric) -> None:
    audit = InMemoryAuditLog()
    deps = fabric.activities(audit)
    deps.intent_store.get_desired_state = AsyncMock(side_effect=ConnectionError("sot unreachable"))

    with pytest.raises(WorkflowFailureError):
        await _run(deps, DriftResponseMode.REPORT)

    events = await audit.query_by_workflow(RUN_ID)
    terminal = events[-1]
    assert terminal.event_type is AuditEventType.WORKFLOW_TERMINATED
    assert terminal.outcome == "failure"
    assert terminal.reason is WorkflowReason.INTENT_UNAVAILABLE
    assert terminal.actor == ACTOR


@pytest.mark.asyncio
async def test_a_cancelled_run_records_cancellation(make_desired) -> None:
    audit = InMemoryAuditLog()
    started = asyncio.Event()

    async def _blocking(*, use_case=None, device_id=None):
        started.set()
        await asyncio.sleep(30)  # released by cancellation / worker shutdown
        return []

    intent_store = MagicMock()
    intent_store.get_desired_state = AsyncMock(side_effect=_blocking)
    set_activities(
        Activities(
            intent_store=intent_store,
            executor=MagicMock(),
            collector=MagicMock(),
            observer=MagicMock(),
            audit_log=audit,
        )
    )

    async with (
        await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env,
        Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=_WORKFLOWS,
            workflow_runner=build_workflow_runner(),
            activities=_ACTIVITIES,
        ),
    ):
        handle = await env.client.start_workflow(
            DriftResponseWorkflow.run, _request(DriftResponseMode.REPORT), id=RUN_ID, task_queue=TASK_QUEUE
        )
        await asyncio.wait_for(started.wait(), timeout=10)
        await handle.cancel()
        with pytest.raises(WorkflowFailureError):
            await handle.result()

    types = [e.event_type for e in await audit.query_by_workflow(RUN_ID)]
    assert AuditEventType.WORKFLOW_CANCELLED in types
    assert AuditEventType.DRIFT_RESPONSE not in types


@pytest.mark.asyncio
async def test_a_scan_cancelled_on_its_own_fails_the_run(make_desired) -> None:
    """Cancelling only the scan child (Web UI, CLI) returns an empty scan.

    That must not be recorded as a successful run over zero devices.
    """
    audit = InMemoryAuditLog()
    started = asyncio.Event()

    async def _blocking(*, use_case=None, device_id=None):
        started.set()
        await asyncio.sleep(30)  # released by cancellation / worker shutdown
        return []

    intent_store = MagicMock()
    intent_store.get_desired_state = AsyncMock(side_effect=_blocking)
    set_activities(
        Activities(
            intent_store=intent_store,
            executor=MagicMock(),
            collector=MagicMock(),
            observer=MagicMock(),
            audit_log=audit,
        )
    )

    async with (
        await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env,
        Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=_WORKFLOWS,
            workflow_runner=build_workflow_runner(),
            activities=_ACTIVITIES,
        ),
    ):
        handle = await env.client.start_workflow(
            DriftResponseWorkflow.run, _request(DriftResponseMode.REMEDIATE), id=RUN_ID, task_queue=TASK_QUEUE
        )
        await asyncio.wait_for(started.wait(), timeout=10)
        await env.client.get_workflow_handle(f"{RUN_ID}-scan").cancel()
        with pytest.raises(WorkflowFailureError):
            await handle.result()

    events = await audit.query_by_workflow(RUN_ID)
    assert AuditEventType.DRIFT_RESPONSE not in [e.event_type for e in events]
    terminal = events[-1]
    assert terminal.event_type is AuditEventType.WORKFLOW_TERMINATED
    assert terminal.outcome == "failure"
    assert terminal.reason is WorkflowReason.CANCELLED
    assert f"{RUN_ID}-scan" in terminal.payload["error"]


# ---------------------------------------------------------------------------
# Remediate (US3)
# ---------------------------------------------------------------------------


@pytest.fixture
def mixed(make_desired) -> Fabric:
    """forward-01 has forward drift, both-02 has forward and undesired,
    undesired-03 has only undesired config, unreachable-04 can't be read,
    clean-05 is clean."""
    fabric = Fabric([make_desired(n) for n in ("forward-01", "both-02", "undesired-03", "unreachable-04", "clean-05")])
    ids = [s.device.id for s in fabric.states]
    fabric.drift[ids[0]] = [FORWARD]
    fabric.drift[ids[1]] = [FORWARD, UNDESIRED]
    fabric.drift[ids[2]] = [UNDESIRED]
    fabric.unreachable.add(ids[3])
    return fabric


@pytest.mark.asyncio
async def test_remediate_reconciles_only_forward_drift_in_scan_order(mixed: Fabric) -> None:
    ids = [s.device.id for s in mixed.states]
    audit = InMemoryAuditLog()

    result = await _run(mixed.activities(audit), DriftResponseMode.REMEDIATE)

    assert result.mode is DriftResponseMode.REMEDIATE
    assert result.remediated == [ids[0], ids[1]]
    assert result.excluded == {ids[2]: ExclusionReason.UNDESIRED_ONLY, ids[3]: ExclusionReason.ERRORED}
    assert result.reconcile.workflow_id == f"{RUN_ID}-reconcile"
    assert (result.reconcile.succeeded, result.reconcile.failed) == (2, 0)
    assert sorted(mixed.applied, key=str) == sorted([ids[0], ids[1]], key=str)
    # A deploy cannot remove config intent never asked for, and none was attempted (VIII.5).
    assert mixed.drift[ids[1]] == [UNDESIRED]
    assert mixed.drift[ids[2]] == [UNDESIRED]


@pytest.mark.asyncio
async def test_remediate_records_each_decision_against_the_device(mixed: Fabric) -> None:
    ids = [s.device.id for s in mixed.states]
    audit = InMemoryAuditLog()

    await _run(mixed.activities(audit), DriftResponseMode.REMEDIATE)

    [forward] = await _response_events(audit, target=ids[0])
    assert forward.actor == ACTOR
    assert forward.payload["decision"] == "remediated"
    assert forward.payload["scan_workflow_id"] == f"{RUN_ID}-scan"
    assert forward.payload["reconcile_workflow_id"] == f"{RUN_ID}-reconcile"
    assert forward.payload["result"] == "succeeded"
    assert forward.payload["paths"] == [FORWARD.path]

    [undesired] = await _response_events(audit, target=ids[2])
    assert undesired.payload["decision"] == "undesired_only"
    assert undesired.payload["paths"] == [UNDESIRED.path]
    assert "reconcile_workflow_id" not in undesired.payload

    [errored] = await _response_events(audit, target=ids[3])
    assert errored.payload["decision"] == "errored"
    assert errored.payload["error"].startswith("collect failed")

    assert await _response_events(audit, target=ids[4]) == []


@pytest.mark.asyncio
async def test_remediate_use_case_summary_counts_decisions(mixed: Fabric) -> None:
    audit = InMemoryAuditLog()

    await _run(mixed.activities(audit), DriftResponseMode.REMEDIATE)

    [summary] = await _response_events(audit, target=USE_CASE)
    assert summary.payload["mode"] == "remediate"
    assert summary.payload["remediated"] == 2
    assert summary.payload["excluded"] == {"undesired_only": 1, "errored": 1}
    assert summary.payload["reconcile_workflow_id"] == f"{RUN_ID}-reconcile"


@pytest.mark.asyncio
async def test_remediate_with_nothing_eligible_starts_no_reconcile(make_desired) -> None:
    fabric = Fabric([make_desired("undesired-01"), make_desired("clean-02")])
    fabric.drift[fabric.states[0].device.id] = [UNDESIRED]
    audit = InMemoryAuditLog()

    result = await _run(fabric.activities(audit), DriftResponseMode.REMEDIATE)

    assert result.remediated == []
    assert result.reconcile is None
    assert fabric.applied == []
    assert await audit.query_by_workflow(f"{RUN_ID}-reconcile") == []


@pytest.mark.asyncio
async def test_a_failed_repair_is_reported_and_the_run_still_succeeds(make_desired) -> None:
    # Two clean devices keep the drift at half, under the blast-radius guard.
    fabric = Fabric([make_desired(n) for n in ("rejecting-01", "forward-02", "clean-03", "clean-04")])
    rejecting, forward = (s.device.id for s in fabric.states[:2])
    fabric.drift[rejecting] = [FORWARD]
    fabric.drift[forward] = [FORWARD]
    fabric.rejecting.add(rejecting)
    audit = InMemoryAuditLog()

    result = await _run(fabric.activities(audit), DriftResponseMode.REMEDIATE)

    assert (result.reconcile.succeeded, result.reconcile.failed) == (1, 1)
    [failed] = await _response_events(audit, target=rejecting)
    assert failed.payload["result"] == "failed"
    terminal = (await audit.query_by_workflow(RUN_ID))[-1]
    assert terminal.event_type is AuditEventType.WORKFLOW_TERMINATED
    assert terminal.reason is WorkflowReason.SUCCEEDED


@pytest.mark.asyncio
async def test_a_failed_repair_stands_the_schedule_down(make_desired) -> None:
    """The next run must not try the same repair again (#136, FR-018). The
    reason names the run and the device, so the schedule's note explains itself."""
    # Two clean devices keep the drift at half, under the blast-radius guard.
    fabric = Fabric([make_desired(n) for n in ("rejecting-01", "forward-02", "clean-03", "clean-04")])
    rejecting, forward = (s.device.id for s in fabric.states[:2])
    fabric.drift[rejecting] = [FORWARD]
    fabric.drift[forward] = [FORWARD]
    fabric.rejecting.add(rejecting)

    result = await _run(fabric.activities(InMemoryAuditLog()), DriftResponseMode.REMEDIATE)

    [request] = STAND_DOWNS
    assert request.use_case_id == USE_CASE
    assert RUN_ID in request.reason
    assert "rejecting-01" in request.reason
    assert "forward-02" not in request.reason
    assert result.stood_down is True


@pytest.mark.asyncio
async def test_a_repair_that_succeeds_leaves_the_schedule_in_remediate(make_desired) -> None:
    fabric = Fabric([make_desired("forward-01")])
    fabric.drift[fabric.states[0].device.id] = [FORWARD]

    result = await _run(fabric.activities(InMemoryAuditLog()), DriftResponseMode.REMEDIATE)

    assert (result.reconcile.succeeded, result.reconcile.failed) == (1, 0)
    assert STAND_DOWNS == []
    assert result.stood_down is False


@pytest.mark.asyncio
async def test_a_report_run_never_stands_down(make_desired) -> None:
    fabric = Fabric([make_desired("forward-01")])
    fabric.drift[fabric.states[0].device.id] = [FORWARD]

    result = await _run(fabric.activities(InMemoryAuditLog()), DriftResponseMode.REPORT)

    assert STAND_DOWNS == []
    assert result.stood_down is False


@pytest.mark.asyncio
async def test_a_reconcile_that_fails_as_a_whole_stands_the_schedule_down_too(mixed: Fabric) -> None:
    """Its deploys may have written and nothing reported per device. Retrying
    that every interval is the same loop #136 stops for one failed device."""
    with pytest.raises(WorkflowFailureError):
        await _run(mixed.activities(RefusingAuditLog("ReconcileDevices")), DriftResponseMode.REMEDIATE)

    [request] = STAND_DOWNS
    assert request.use_case_id == USE_CASE
    assert RUN_ID in request.reason
    assert "did not complete" in request.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("widened", [False, True], ids=["as-is", "race-widened"])
async def test_an_operator_cancel_during_the_repair_does_not_stand_down(mixed: Fabric, widened: bool) -> None:
    """A cancel is the operator stopping the run, not a failed repair.

    The repair deploys forward-01 and both-02 side by side, so when one apply
    has started the other deploy may still be running its earlier steps.
    So the cancel waits until the repair is quiet: each deploy holds only its
    apply, and every run's last workflow task is answered (#375).

    ``race-widened`` makes the race certain under a cancel sent as soon as
    one apply starts. both-02's intent fetch returns 1.2 s late, and every
    workflow task after the cancel takes 0.5 s, so the cancel reaches
    both-02's deploy (through the run and the reconcile) while that fetch
    completes. The time-skipping server then refuses the cancel's workflow
    task (``ACTIVITY_UNKNOWN``) and never times it out."""
    deps = mixed.activities(InMemoryAuditLog())
    applying = asyncio.Event()

    async def _blocking_apply(_desired):
        applying.set()
        await asyncio.sleep(30)  # released by cancellation / worker shutdown

    deps.executor.apply = AsyncMock(side_effect=_blocking_apply)
    if widened:
        late_id = mixed.states[1].device.id
        get_desired = deps.intent_store.get_desired_state.side_effect

        async def _late_for_both_02(*, use_case=None, device_id=None):
            if device_id == late_id:
                await asyncio.sleep(1.2)
            return await get_desired(use_case=use_case, device_id=device_id)

        deps.intent_store.get_desired_state = AsyncMock(side_effect=_late_for_both_02)
    set_activities(deps)
    executor = SlowAfterCancel(0.5) if widened else None
    async with (
        await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env,
        Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=_WORKFLOWS,
            workflow_runner=build_workflow_runner(),
            activities=_ACTIVITIES,
            workflow_task_executor=executor,
        ),
    ):
        handle = await env.client.start_workflow(
            DriftResponseWorkflow.run, _request(DriftResponseMode.REMEDIATE), id=RUN_ID, task_queue=TASK_QUEUE
        )
        await asyncio.wait_for(applying.wait(), timeout=10)
        reconcile = env.client.get_workflow_handle(f"{RUN_ID}-reconcile")
        deploys = [env.client.get_workflow_handle(f"deploy-intent-{s.device.id}") for s in mixed.states[:2]]
        runs = [handle, reconcile, *deploys]
        await until_quiet([(handle, []), (reconcile, []), *((deploy, ["apply_config"]) for deploy in deploys)])
        if executor is not None:
            executor.slow = True
            # By then the cancel has reached both deploys; the rest of the run
            # needn't crawl.
            asyncio.get_running_loop().call_later(2.5, setattr, executor, "slow", False)
        await handle.cancel()
        try:
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(handle.result(), timeout=20)
        except TimeoutError:
            stopped = " | ".join([f"{run.id}: {await where_it_stopped(run)}" for run in runs])
            raise AssertionError(f"the run did not finish within 20 s after its cancel: {stopped}") from None

    if executor is not None:
        executor.shutdown(wait=False)
    assert STAND_DOWNS == []


@pytest.mark.asyncio
async def test_a_failed_reconcile_records_each_decision_and_fails_the_run(mixed: Fabric) -> None:
    """The deploys ran (and wrote) before ReconcileDevices itself failed.

    The run must not blame the Source of Truth, and every device it decided
    about still needs its record (FR-020).
    """
    ids = [s.device.id for s in mixed.states]
    audit = RefusingAuditLog("ReconcileDevices")

    with pytest.raises(WorkflowFailureError):
        await _run(mixed.activities(audit), DriftResponseMode.REMEDIATE)

    assert sorted(mixed.applied, key=str) == sorted([ids[0], ids[1]], key=str)
    for device_id in (ids[0], ids[1]):
        [record] = await _response_events(audit, target=device_id)
        assert record.payload["decision"] == "remediated"
        assert record.payload["reconcile_workflow_id"] == f"{RUN_ID}-reconcile"
        # Reconcile failed before it reported per-device results.
        assert record.payload["result"] == "unknown"
    [undesired] = await _response_events(audit, target=ids[2])
    assert undesired.payload["decision"] == "undesired_only"
    [summary] = await _response_events(audit, target=USE_CASE)
    assert summary.payload["remediated"] == 2
    assert summary.payload["reconcile_workflow_id"] == f"{RUN_ID}-reconcile"

    terminal = (await audit.query_by_workflow(RUN_ID))[-1]
    assert terminal.event_type is AuditEventType.WORKFLOW_TERMINATED
    assert terminal.outcome == "failure"
    assert terminal.reason is WorkflowReason.REMEDIATION_FAILED


@pytest.mark.asyncio
async def test_a_run_timeout_mid_repair_lets_the_repair_finish_and_record(make_desired) -> None:
    """#144: the schedule's RUN_TIMEOUT closes the run server-side, mid-repair.

    The reconcile child is abandoned rather than terminated, so its deploy
    verifies and every child writes its own terminal record. A later run that
    finds the same device still drifted skips it: its deploy is still in flight.

    Runs on a real dev server: the time-skipping test server doesn't apply a
    parent close policy when a run times out, so there the repair survives
    either way and the test can't fail.
    """
    fabric = Fabric([make_desired("forward-01")])
    device_id = fabric.states[0].device.id
    fabric.drift[device_id] = [FORWARD]
    fabric.gate = asyncio.Event()
    audit = InMemoryAuditLog()
    set_activities(fabric.activities(audit))
    later_run = f"{RUN_ID}-later"

    async with (
        await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter) as env,
        Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=_WORKFLOWS,
            workflow_runner=build_workflow_runner(),
            activities=_ACTIVITIES,
        ),
    ):
        handle = await env.client.start_workflow(
            DriftResponseWorkflow.run,
            _request(DriftResponseMode.REMEDIATE),
            id=RUN_ID,
            task_queue=TASK_QUEUE,
            # Stands in for RUN_TIMEOUT; well inside apply's 60s start-to-close.
            execution_timeout=timedelta(seconds=10),
        )
        await asyncio.wait_for(fabric.apply_started.wait(), timeout=10)
        await asyncio.sleep(14)
        with pytest.raises(WorkflowFailureError):
            await handle.result()

        # The device is still drifted (its apply is held), so the next run
        # selects it again, and its reconcile skips the in-flight deploy. If the
        # repair was killed instead, this run's own deploy blocks on the gate.
        later = await asyncio.wait_for(
            env.client.execute_workflow(
                DriftResponseWorkflow.run, _request(DriftResponseMode.REMEDIATE), id=later_run, task_queue=TASK_QUEUE
            ),
            timeout=30,
        )
        assert later.remediated == [device_id]
        assert (later.reconcile.skipped, later.reconcile.failed) == (1, 0)
        assert fabric.applied == [device_id]

        fabric.gate.set()
        repair = await env.client.get_workflow_handle_for(ReconcileDevicesWorkflow.run, f"{RUN_ID}-reconcile").result()

    assert (repair.succeeded, repair.failed, repair.skipped) == (1, 0, 0)
    assert fabric.drift[device_id] == []
    for workflow_id in (f"{RUN_ID}-reconcile", f"deploy-intent-{device_id}"):
        terminal = [
            e for e in await audit.query_by_workflow(workflow_id) if e.event_type is AuditEventType.WORKFLOW_TERMINATED
        ]
        assert [(e.outcome, e.reason) for e in terminal] == [("success", WorkflowReason.SUCCEEDED)]
    # The timed-out run wrote no DRIFT_RESPONSE: workflow code never ran again.
    assert await _response_events(audit) == []


# ---------------------------------------------------------------------------
# The blast-radius guard (spec 018 US3, #136)
# ---------------------------------------------------------------------------


def _guard_fabric(make_desired, *, forward: int, undesired: int = 0, unreachable: int = 0, clean: int = 0) -> Fabric:
    """A fabric of ``forward`` devices with forward drift, then the others in order."""
    names = (
        [f"forward-{i:02}" for i in range(1, forward + 1)]
        + [f"undesired-{i:02}" for i in range(1, undesired + 1)]
        + [f"unreachable-{i:02}" for i in range(1, unreachable + 1)]
        + [f"clean-{i:02}" for i in range(1, clean + 1)]
    )
    fabric = Fabric([make_desired(name) for name in names])
    for state in fabric.states:
        if state.device.name.startswith("forward-"):
            fabric.drift[state.device.id] = [FORWARD]
        elif state.device.name.startswith("undesired-"):
            fabric.drift[state.device.id] = [UNDESIRED]
        elif state.device.name.startswith("unreachable-"):
            fabric.unreachable.add(state.device.id)
    return fabric


def _forward_ids(fabric: Fabric) -> list:
    return [s.device.id for s in fabric.states if s.device.name.startswith("forward-")]


@pytest.mark.asyncio
async def test_remediate_repairs_nothing_when_more_than_half_drift_and_stands_down(make_desired) -> None:
    """A bad intent edit drifts the whole fabric at once; the run must not push it everywhere."""
    fabric = _guard_fabric(make_desired, forward=3, clean=1)
    forward = _forward_ids(fabric)

    result = await _run(fabric.activities(InMemoryAuditLog()), DriftResponseMode.REMEDIATE)

    assert fabric.applied == []
    assert result.reconcile is None
    assert result.remediated == []
    assert result.excluded == dict.fromkeys(forward, ExclusionReason.BLAST_RADIUS)
    assert result.stood_down is True
    [request] = STAND_DOWNS
    assert request.use_case_id == USE_CASE
    assert RUN_ID in request.reason
    assert "3 of 4" in request.reason
    assert "more than half" in request.reason


@pytest.mark.asyncio
async def test_the_guard_is_recorded_against_each_device_and_the_use_case(make_desired) -> None:
    fabric = _guard_fabric(make_desired, forward=3, clean=1)
    audit = InMemoryAuditLog()

    await _run(fabric.activities(audit), DriftResponseMode.REMEDIATE)

    for device_id in _forward_ids(fabric):
        [record] = await _response_events(audit, target=device_id)
        assert record.payload["decision"] == "blast_radius"
    [summary] = await _response_events(audit, target=USE_CASE)
    assert summary.payload["remediated"] == 0
    assert summary.payload["excluded"] == {"blast_radius": 3}
    assert summary.payload["reconcile_workflow_id"] is None
    [request] = STAND_DOWNS
    assert summary.payload["guard"] == request.reason
    terminal = (await audit.query_by_workflow(RUN_ID))[-1]
    assert (terminal.event_type, terminal.outcome) == (AuditEventType.WORKFLOW_TERMINATED, "success")


@pytest.mark.asyncio
async def test_remediate_repairs_when_exactly_half_drift(make_desired) -> None:
    fabric = _guard_fabric(make_desired, forward=2, clean=2)

    result = await _run(fabric.activities(InMemoryAuditLog()), DriftResponseMode.REMEDIATE)

    assert sorted(fabric.applied, key=str) == sorted(_forward_ids(fabric), key=str)
    assert result.remediated == _forward_ids(fabric)
    assert STAND_DOWNS == []
    assert result.stood_down is False


@pytest.mark.asyncio
async def test_remediate_repairs_a_use_case_of_one_drifted_device(make_desired) -> None:
    """One device is not a blast radius."""
    fabric = _guard_fabric(make_desired, forward=1)

    result = await _run(fabric.activities(InMemoryAuditLog()), DriftResponseMode.REMEDIATE)

    assert fabric.applied == _forward_ids(fabric)
    assert result.remediated == _forward_ids(fabric)
    assert STAND_DOWNS == []


@pytest.mark.asyncio
async def test_a_report_run_has_no_guard(make_desired) -> None:
    fabric = _guard_fabric(make_desired, forward=4)
    audit = InMemoryAuditLog()

    result = await _run(fabric.activities(audit), DriftResponseMode.REPORT)

    assert result.excluded == {}
    assert result.stood_down is False
    assert STAND_DOWNS == []
    [summary] = await _response_events(audit, target=USE_CASE)
    assert "guard" not in summary.payload


@pytest.mark.asyncio
async def test_an_undesired_only_device_does_not_count_towards_the_guard(make_desired) -> None:
    """The scan reports four devices drifted, but only two have forward drift."""
    fabric = _guard_fabric(make_desired, forward=2, undesired=2)

    result = await _run(fabric.activities(InMemoryAuditLog()), DriftResponseMode.REMEDIATE)

    assert result.scan.drifted == 4
    assert sorted(fabric.applied, key=str) == sorted(_forward_ids(fabric), key=str)
    assert STAND_DOWNS == []


@pytest.mark.asyncio
async def test_an_errored_device_counts_in_the_guards_total(make_desired) -> None:
    """The denominator is every device the scan covered: 2 of 3 is more than half."""
    fabric = _guard_fabric(make_desired, forward=2, unreachable=1)

    result = await _run(fabric.activities(InMemoryAuditLog()), DriftResponseMode.REMEDIATE)

    assert fabric.applied == []
    [request] = STAND_DOWNS
    assert "2 of 3" in request.reason
    assert result.excluded[fabric.states[2].device.id] == ExclusionReason.ERRORED


@pytest.mark.asyncio
async def test_a_run_from_before_the_guard_replays_unchanged() -> None:
    """A run in flight when the guard shipped replays as it ran (FR-004).

    The history is a remediate run that repaired 3 of 4 devices, recorded from
    the code before the guard (main at 4e90b1d) by running this file's fabric.
    The guard would stand that run down instead, so without its patch id this
    replay fails as nondeterministic.
    """
    history = WorkflowHistory.from_json(RUN_ID, (_FIXTURES / "drift_response_before_the_guard.json").read_text())

    await Replayer(
        workflows=_WORKFLOWS,
        data_converter=pydantic_data_converter,
        workflow_runner=build_workflow_runner(),
    ).replay_workflow(history)
