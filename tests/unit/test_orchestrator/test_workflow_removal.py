"""PlanRemoval, the read-only removal preview (spec 013 T016).

The workflow runs on Temporal's time-skipping test server with the blocks
mocked behind the real activities, as the other workflow tests do. Its contract
is contracts/orchestrator-removal.md.
"""

from __future__ import annotations

import uuid as _uuid  # noqa: TC003 — used at runtime by _preview
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pydantic_core  # noqa: F401 — pre-import to keep workflow sandbox quiet
import pytest
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from snapl_collector.models import CollectResult
from snapl_executor.models import RemoveResult
from snapl_intent.exceptions import IntentConnectionError
from snapl_observability.models import DriftReport, DriftStatus
from snapl_observability.structural.removal import plan_removal as structural_plan
from snapl_orchestrator.activities import Activities, set_activities
from snapl_orchestrator.activities.audit import record_audit_event
from snapl_orchestrator.activities.collector import collect_drift_state
from snapl_orchestrator.activities.executor import dry_run_remove
from snapl_orchestrator.activities.intent import fetch_desired_state
from snapl_orchestrator.activities.observability import detect_drift, plan_removal
from snapl_orchestrator.audit.memory import InMemoryAuditLog
from snapl_orchestrator.models import AuditEventType, WorkflowReason
from snapl_orchestrator.worker.sandbox import build_workflow_runner
from snapl_orchestrator.workflows.removal import PlanRemovalWorkflow
from tests.unit.test_observability.removal_reports import PORT, forward, undesired

pytestmark = pytest.mark.unit

TASK_QUEUE = "test-removal"


def teardown_function() -> None:
    import snapl_orchestrator.activities as a

    a._activities = None


def _collect(device, *, success: bool = True) -> CollectResult:
    return CollectResult(
        device_id=device.id,
        device_name=device.name,
        success=success,
        data={} if not success else {"/interface": []},
        paths=["/interface"],
        error=None if success else "timeout",
    )


def _report(device, *items) -> DriftReport:
    return DriftReport(
        device_id=device.id,
        device_name=device.name,
        status=DriftStatus.DRIFTED if items else DriftStatus.CLEAN,
        items=list(items),
        timestamp=datetime(2026, 11, 24, 14, 5, 12, tzinfo=UTC),
    )


def _error_report(device) -> DriftReport:
    return DriftReport(
        device_id=device.id,
        device_name=device.name,
        status=DriftStatus.ERROR,
        items=[],
        error="gNMI timeout",
        timestamp=datetime(2026, 11, 24, 14, 5, 12, tzinfo=UTC),
    )


def _drifted(device) -> DriftReport:
    return _report(
        device,
        forward("/interface[name=ethernet-1/1]/description", "uplink", None),
        undesired(f"{PORT}/description", "c-probe", "interface"),
        undesired(f"{PORT}/enabled", True, "interface"),
        undesired("/routing-policy/policy[name=c-probe]/default_action", "reject", "routing_policy"),
    )


class _Calls:
    """The blocks behind the activities, recording the order they were called in."""

    def __init__(self, desired, report: DriftReport, *, dry_run_refuses: bool = False) -> None:
        self.order: list[str] = []
        device = desired.device

        async def _get_desired_state(**_):
            self.order.append("intent")
            return [desired]

        async def _collect_state(*_):
            self.order.append("collect")
            return _collect(device)

        async def _detect(*_):
            self.order.append("detect")
            return report

        def _plan(source):
            self.order.append("plan_removal")
            return structural_plan(source)

        async def _dry_run(dev, paths):
            self.order.append("dry_run_remove")
            if dry_run_refuses:
                return RemoveResult(
                    device_id=dev.id,
                    device_name=dev.name,
                    success=False,
                    paths=paths,
                    dry_run=True,
                    refused={paths[0]: "protected"},
                    error=f"refused 1 path: {paths[0]} (protected)",
                )
            return RemoveResult(device_id=dev.id, device_name=dev.name, success=True, paths=paths, dry_run=True)

        self.intent_store = MagicMock()
        self.intent_store.get_desired_state = AsyncMock(side_effect=_get_desired_state)
        self.collector = MagicMock()
        self.collector.collect = AsyncMock(side_effect=_collect_state)
        self.observer = MagicMock()
        self.observer.detect_drift = AsyncMock(side_effect=_detect)
        self.observer.emit_event = AsyncMock()
        self.observer.plan_removal = MagicMock(side_effect=_plan)
        self.executor = MagicMock()
        self.executor.dry_run_remove = AsyncMock(side_effect=_dry_run)
        self.audit = InMemoryAuditLog()

    def activities(self) -> Activities:
        return Activities(
            intent_store=self.intent_store,
            executor=self.executor,
            collector=self.collector,
            observer=self.observer,
            audit_log=self.audit,
        )


async def _preview(calls: _Calls, device_id: _uuid.UUID, workflow_id: str | None = None):
    set_activities(calls.activities())
    async with (
        await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env,
        Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[PlanRemovalWorkflow],
            workflow_runner=build_workflow_runner(),
            activities=[
                fetch_desired_state,
                collect_drift_state,
                detect_drift,
                plan_removal,
                dry_run_remove,
                record_audit_event,
            ],
        ),
    ):
        return await env.client.execute_workflow(
            PlanRemovalWorkflow.run,
            device_id,
            id=workflow_id or f"plan-removal-{device_id}-test",
            task_queue=TASK_QUEUE,
        )


async def _events(calls: _Calls, device_id):
    return await calls.audit.query_by_workflow(f"plan-removal-{device_id}-test")


def _failure(exc_info) -> ApplicationError:
    cause = exc_info.value.cause
    assert isinstance(cause, ApplicationError)
    return cause


class TestPlanRemoval:
    async def test_scans_plans_and_dry_runs_in_order(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        calls = _Calls(dcfabric_desired_state, _drifted(device))

        plan = await _preview(calls, device.id)

        assert calls.order == ["intent", "collect", "detect", "plan_removal", "dry_run_remove"]
        assert plan.delete_paths == [PORT]
        assert [r.reason for r in plan.refused] == ["not_removable"]
        assert plan.forward_drift == ["/interface[name=ethernet-1/1]/description"]
        calls.executor.dry_run_remove.assert_awaited_once()
        assert calls.executor.dry_run_remove.await_args.args[1] == [PORT]

    async def test_an_empty_plan_skips_the_dry_run(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        calls = _Calls(dcfabric_desired_state, _report(device))

        plan = await _preview(calls, device.id)

        assert plan.is_empty
        assert calls.order == ["intent", "collect", "detect", "plan_removal"]

    async def test_never_writes(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        calls = _Calls(dcfabric_desired_state, _drifted(device))

        await _preview(calls, device.id)

        assert not [name for name, *_ in calls.executor.method_calls if name != "dry_run_remove"]

    async def test_records_each_step_and_the_plan(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        calls = _Calls(dcfabric_desired_state, _drifted(device))

        await _preview(calls, device.id)

        events = await _events(calls, device.id)
        assert {e.workflow_type for e in events} == {"PlanRemoval"}
        assert {e.target_id for e in events} == {device.id}
        assert [(e.event_type, e.activity_name) for e in events] == [
            (AuditEventType.WORKFLOW_STARTED, None),
            (AuditEventType.ACTIVITY_COMPLETED, "fetch_desired_state"),
            (AuditEventType.ACTIVITY_COMPLETED, "collect_drift_state"),
            (AuditEventType.ACTIVITY_COMPLETED, "detect_drift"),
            (AuditEventType.ACTIVITY_COMPLETED, "plan_removal"),
            (AuditEventType.ACTIVITY_COMPLETED, "dry_run_remove"),
            (AuditEventType.WORKFLOW_TERMINATED, None),
        ]
        terminal = events[-1]
        assert terminal.reason == WorkflowReason.SUCCEEDED
        assert terminal.outcome == "success"
        assert terminal.payload == {
            "plan_paths": [PORT],
            "refused_paths": ["/routing-policy/policy[name=c-probe]"],
        }

    async def test_intent_not_found(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        calls = _Calls(dcfabric_desired_state, _drifted(device))
        calls.intent_store.get_desired_state = AsyncMock(return_value=[])

        with pytest.raises(WorkflowFailureError) as exc_info:
            await _preview(calls, device.id)

        assert _failure(exc_info).type == "device_not_found"
        terminal = (await _events(calls, device.id))[-1]
        assert terminal.reason == WorkflowReason.DEVICE_NOT_FOUND
        calls.collector.collect.assert_not_awaited()

    async def test_intent_unavailable(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        calls = _Calls(dcfabric_desired_state, _drifted(device))
        calls.intent_store.get_desired_state = AsyncMock(side_effect=IntentConnectionError("infrahub down"))

        with pytest.raises(WorkflowFailureError) as exc_info:
            await _preview(calls, device.id)

        failure = _failure(exc_info)
        assert failure.type == "intent_unavailable"
        assert "infrahub down" in failure.message
        assert (await _events(calls, device.id))[-1].reason == WorkflowReason.INTENT_UNAVAILABLE

    async def test_a_failed_collect(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        calls = _Calls(dcfabric_desired_state, _error_report(device))

        with pytest.raises(WorkflowFailureError) as exc_info:
            await _preview(calls, device.id)

        failure = _failure(exc_info)
        assert failure.type == "collect_failed"
        assert "gNMI timeout" in failure.message
        assert (await _events(calls, device.id))[-1].reason == WorkflowReason.COLLECT_FAILED
        calls.observer.plan_removal.assert_not_called()

    async def test_a_collect_that_raises(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        calls = _Calls(dcfabric_desired_state, _drifted(device))
        calls.collector.collect = AsyncMock(side_effect=RuntimeError("socket closed"))

        with pytest.raises(WorkflowFailureError) as exc_info:
            await _preview(calls, device.id)

        assert _failure(exc_info).type == "collect_failed"

    async def test_a_dry_run_refusal_fails_the_preview(self, dcfabric_desired_state) -> None:
        """The planner and the guard share one list (SC-003), so a refusal here is a defect."""
        device = dcfabric_desired_state.device
        calls = _Calls(dcfabric_desired_state, _drifted(device), dry_run_refuses=True)

        with pytest.raises(WorkflowFailureError) as exc_info:
            await _preview(calls, device.id)

        failure = _failure(exc_info)
        assert failure.type == "remove_failed"
        assert PORT in failure.message
        terminal = (await _events(calls, device.id))[-1]
        assert terminal.reason == WorkflowReason.REMOVE_FAILED
        assert terminal.payload["refused"] == {PORT: "protected"}
