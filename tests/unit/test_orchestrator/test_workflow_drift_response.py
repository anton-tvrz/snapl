"""Unit tests for DriftResponseWorkflow — one scheduled run (spec 009).

The schedule is only the trigger, so the workflow is started directly with a
``DriftResponseRequest``. The real ScanDrift, ReconcileDevices and
DeployIntent children run against a fake fabric whose devices hold forward and
undesired drift: a deploy clears the forward drift and leaves the undesired
config in place, which is what the live fabric does (#121).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pydantic_core  # noqa: F401 — pre-import to keep workflow sandbox quiet
import pytest
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

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
from snapl_orchestrator.models import (
    AuditEventType,
    DriftResponseMode,
    DriftResponseRequest,
    WorkflowReason,
)
from snapl_orchestrator.worker.sandbox import build_workflow_runner
from snapl_orchestrator.workflows.deploy_intent import DeployIntentWorkflow
from snapl_orchestrator.workflows.drift_response import DriftResponseWorkflow
from snapl_orchestrator.workflows.reconcile_devices import ReconcileDevicesWorkflow
from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow

pytestmark = pytest.mark.unit

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
    ``rejecting`` devices fail their apply.
    """

    def __init__(self, states: list) -> None:
        self.states = states
        self.by_id = {s.device.id: s for s in states}
        self.drift: dict = {s.device.id: [] for s in states}
        self.unreachable: set = set()
        self.rejecting: set = set()
        self.applied: list = []

    def activities(self, audit: InMemoryAuditLog) -> Activities:
        async def _get_desired(*, use_case=None, device_id=None):
            if use_case is not None:
                return self.states
            return [self.by_id[device_id]] if device_id in self.by_id else []

        async def _apply(desired):
            self.applied.append(desired.device.id)
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


def teardown_function() -> None:
    import snapl_orchestrator.activities as a

    a._activities = None


_WORKFLOWS = [DriftResponseWorkflow, ScanDriftWorkflow, ReconcileDevicesWorkflow, DeployIntentWorkflow]
_ACTIVITIES = [
    fetch_devices_for_use_case,
    fetch_desired_state,
    apply_config,
    collect_drift_state,
    detect_drift,
    record_audit_event,
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
