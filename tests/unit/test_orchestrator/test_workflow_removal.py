"""PlanRemoval, the read-only removal preview (spec 013 T016), and RemoveConfig (T029).

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
from temporalio import activity
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
from snapl_orchestrator.activities.executor import dry_run_remove, remove_config
from snapl_orchestrator.activities.intent import fetch_desired_state
from snapl_orchestrator.activities.observability import detect_drift, plan_removal
from snapl_orchestrator.audit.memory import InMemoryAuditLog
from snapl_orchestrator.models import AuditEventType, RemovalRequest, WorkflowReason, remove_config_id
from snapl_orchestrator.worker.sandbox import build_workflow_runner
from snapl_orchestrator.workflows.removal import PlanRemovalWorkflow, RemoveConfigWorkflow
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


# ---------------------------------------------------------------------------
# RemoveConfig (spec 013 T029)
# ---------------------------------------------------------------------------

_POLICY_ITEM = "/routing-policy/policy[name=c-probe]/default_action"
_UPLINK = "/interface[name=ethernet-1/1]/description"
_NEIGHBOR = "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=192.0.2.0]"
_C1_REJECTION = (
    'Error in path: .network-instance{.name=="default"}.interface{.name=="ethernet-1/7.0"}\n'
    "    [FailedPrecondition] subinterface ethernet-1/7.0 not found"
)


def _after(device, *extra) -> DriftReport:
    """leaf-04 after a good removal: the port is gone, the refused policy and the forward drift stay."""
    return _report(
        device,
        forward(_UPLINK, "uplink", None),
        undesired(_POLICY_ITEM, "reject", "routing_policy"),
        *extra,
    )


class _RemoveCalls(_Calls):
    """``_Calls`` plus the Set, a deploy check per call, and one report per scan."""

    def __init__(
        self,
        desired,
        reports: list[DriftReport],
        *,
        deploys: list[bool] | None = None,
        rejects: str | None = None,
        dry_run_refuses: bool = False,
        verify_collect_fails: bool = False,
    ) -> None:
        super().__init__(desired, reports[0], dry_run_refuses=dry_run_refuses)
        self.deploys = list(deploys or [False, False])
        device = desired.device
        scans = list(reports)

        async def _detect(*_):
            self.order.append("detect")
            return scans.pop(0) if len(scans) > 1 else scans[0]

        collects = 0

        async def _collect_state(*_):
            nonlocal collects
            collects += 1
            self.order.append("collect")
            if verify_collect_fails and collects > 1:
                raise RuntimeError("socket closed")
            return _collect(device)

        async def _remove(dev, paths):
            self.order.append("remove")
            if rejects is not None:
                return RemoveResult(
                    device_id=dev.id, device_name=dev.name, success=False, paths=paths, dry_run=False, error=rejects
                )
            return RemoveResult(
                device_id=dev.id,
                device_name=dev.name,
                success=True,
                paths=paths,
                dry_run=False,
                device_response="{'timestamp': 1}",
            )

        self.observer.detect_drift = AsyncMock(side_effect=_detect)
        self.collector.collect = AsyncMock(side_effect=_collect_state)
        self.executor.remove = AsyncMock(side_effect=_remove)


_CALLS: list[_RemoveCalls] = []


@activity.defn(name="deploy_in_flight")
async def _fake_deploy_in_flight(device_id: _uuid.UUID) -> bool:
    """Stands in for the real activity, which asks Temporal (tested in test_activity_workflows.py)."""
    calls = _CALLS[-1]
    calls.order.append("deploy_in_flight")
    return calls.deploys.pop(0) if calls.deploys else False


_REMOVE_ACTIVITIES = [
    fetch_desired_state,
    collect_drift_state,
    detect_drift,
    plan_removal,
    dry_run_remove,
    remove_config,
    _fake_deploy_in_flight,
    record_audit_event,
]


def _request(device_id, paths=(PORT,)) -> RemovalRequest:
    return RemovalRequest(device_id=device_id, actor="anton", confirmed_paths=list(paths))


async def _remove(calls: _RemoveCalls, request: RemovalRequest):
    _CALLS.append(calls)
    set_activities(calls.activities())
    async with (
        await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env,
        Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[RemoveConfigWorkflow],
            workflow_runner=build_workflow_runner(),
            activities=_REMOVE_ACTIVITIES,
        ),
    ):
        return await env.client.execute_workflow(
            RemoveConfigWorkflow.run,
            request,
            id=remove_config_id(request.device_id),
            task_queue=TASK_QUEUE,
        )


async def _trail(calls: _Calls, device_id):
    return await calls.audit.query_by_workflow(remove_config_id(device_id))


_HAPPY_ORDER = [
    "deploy_in_flight",
    "intent",
    "collect",
    "detect",
    "plan_removal",
    "deploy_in_flight",
    "dry_run_remove",
    "remove",
    "collect",
    "detect",
]


class TestRemoveConfig:
    async def test_the_happy_path(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        calls = _RemoveCalls(dcfabric_desired_state, [_drifted(device), _after(device)])
        request = _request(device.id)

        result = await _remove(calls, request)

        assert calls.order == _HAPPY_ORDER
        assert result.success is True
        assert result.reason == WorkflowReason.SUCCEEDED
        assert result.workflow_id == remove_config_id(device.id)
        assert result.device_name == device.name
        assert result.deleted == [PORT]
        assert result.device_response == "{'timestamp': 1}"
        assert result.plan is not None
        assert result.plan.delete_paths == [PORT]
        assert result.verification is not None
        assert result.verification.gone == [PORT]
        assert result.verification.remaining == []
        assert result.verification.new_forward == []
        assert result.verification.still_refused == [_POLICY_ITEM]
        assert result.ended_at >= result.started_at
        calls.executor.remove.assert_awaited_once()
        assert calls.executor.remove.await_args.args[1] == request.confirmed_paths

    @pytest.mark.parametrize(
        ("rescan", "confirmed", "new_paths"),
        [
            ("one_more", [PORT], [PORT, _NEIGHBOR]),
            ("one_fewer", [PORT, _NEIGHBOR], [PORT]),
            ("nothing_left", [PORT], []),
        ],
    )
    async def test_a_changed_plan_writes_nothing(self, dcfabric_desired_state, rescan, confirmed, new_paths) -> None:
        device = dcfabric_desired_state.device
        port = undesired(f"{PORT}/description", "c-probe", "interface")
        if rescan == "one_more":
            report = _report(device, port, undesired(f"{_NEIGHBOR}/peer_asn", 65099, "bgp_session"))
        elif rescan == "one_fewer":
            report = _report(device, port, undesired(_POLICY_ITEM, "reject", "routing_policy"))
        else:
            report = _report(device)
        calls = _RemoveCalls(dcfabric_desired_state, [report])

        result = await _remove(calls, _request(device.id, confirmed))

        assert result.reason == WorkflowReason.PLAN_CHANGED
        assert result.success is False
        assert result.deleted == []
        assert result.plan is not None
        assert result.plan.delete_paths == new_paths
        assert "remove" not in calls.order
        assert "dry_run_remove" not in calls.order
        terminal = (await _trail(calls, device.id))[-1]
        assert terminal.reason == WorkflowReason.PLAN_CHANGED
        assert terminal.payload["plan_paths"] == new_paths
        assert "deleted_paths" not in terminal.payload

    async def test_a_deploy_in_flight_at_the_start_writes_nothing(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        calls = _RemoveCalls(dcfabric_desired_state, [_drifted(device)], deploys=[True])

        result = await _remove(calls, _request(device.id))

        assert result.reason == WorkflowReason.DEPLOY_IN_FLIGHT
        assert calls.order == ["deploy_in_flight"]
        assert result.deleted == []
        assert (await _trail(calls, device.id))[-1].reason == WorkflowReason.DEPLOY_IN_FLIGHT

    async def test_a_deploy_that_starts_before_the_set_writes_nothing(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        calls = _RemoveCalls(dcfabric_desired_state, [_drifted(device)], deploys=[False, True])

        result = await _remove(calls, _request(device.id))

        assert result.reason == WorkflowReason.DEPLOY_IN_FLIGHT
        assert result.device_name == device.name
        assert calls.order == _HAPPY_ORDER[:6]
        assert result.deleted == []

    async def test_a_dry_run_refusal_sends_nothing(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        calls = _RemoveCalls(dcfabric_desired_state, [_drifted(device)], dry_run_refuses=True)

        result = await _remove(calls, _request(device.id))

        assert result.reason == WorkflowReason.REMOVE_FAILED
        assert PORT in (result.detail or "")
        assert "remove" not in calls.order
        assert result.deleted == []
        terminal = (await _trail(calls, device.id))[-1]
        assert terminal.payload["refused"] == {PORT: "protected"}

    async def test_a_rejection_keeps_the_device_text(self, dcfabric_desired_state) -> None:
        """C1: SR Linux refuses the whole Set, so nothing was deleted."""
        device = dcfabric_desired_state.device
        calls = _RemoveCalls(dcfabric_desired_state, [_drifted(device)], rejects=_C1_REJECTION)

        result = await _remove(calls, _request(device.id))

        assert result.reason == WorkflowReason.REMOVE_FAILED
        assert result.detail == _C1_REJECTION
        assert result.deleted == []
        assert result.verification is None
        assert calls.order == _HAPPY_ORDER[:8]
        events = await _trail(calls, device.id)
        failed = [e for e in events if e.event_type == AuditEventType.ACTIVITY_FAILED]
        assert [(e.activity_name, e.payload["paths"], e.payload["error"]) for e in failed] == [
            ("remove_config", [PORT], _C1_REJECTION)
        ]

    async def test_something_left_under_a_delete_fails_verification(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        left = undesired(f"{PORT}/enabled", True, "interface")
        calls = _RemoveCalls(dcfabric_desired_state, [_drifted(device), _after(device, left)])

        result = await _remove(calls, _request(device.id))

        assert result.reason == WorkflowReason.VERIFICATION_FAILED
        assert result.deleted == [PORT]
        assert result.verification is not None
        assert result.verification.remaining == [PORT]
        assert result.verification.gone == []

    async def test_new_forward_drift_fails_verification(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        lost = forward("/interface[name=ethernet-1/2]/enabled", True, None)
        calls = _RemoveCalls(dcfabric_desired_state, [_drifted(device), _after(device, lost)])

        result = await _remove(calls, _request(device.id))

        assert result.reason == WorkflowReason.VERIFICATION_FAILED
        assert result.verification is not None
        assert result.verification.gone == [PORT]
        assert result.verification.new_forward == ["/interface[name=ethernet-1/2]/enabled"]

    async def test_a_lookalike_name_is_not_under_a_delete(self, dcfabric_desired_state) -> None:
        """ethernet-1/70 is not under /interface[name=ethernet-1/7]."""
        device = dcfabric_desired_state.device
        other = undesired("/interface[name=ethernet-1/70]/description", "x", "interface")
        before = _report(device, undesired(f"{PORT}/description", "c-probe", "interface"), other)
        calls = _RemoveCalls(dcfabric_desired_state, [before, _report(device, other)])

        result = await _remove(calls, _request(device.id, ["/interface[name=ethernet-1/70]", PORT]))

        assert result.reason == WorkflowReason.VERIFICATION_FAILED
        assert result.verification is not None
        assert result.verification.gone == [PORT]
        assert result.verification.remaining == ["/interface[name=ethernet-1/70]"]

    async def test_a_failed_verifying_scan_keeps_what_was_deleted(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        calls = _RemoveCalls(dcfabric_desired_state, [_drifted(device)], verify_collect_fails=True)

        result = await _remove(calls, _request(device.id))

        assert result.reason == WorkflowReason.COLLECT_FAILED
        assert result.deleted == [PORT]
        assert result.verification is None
        assert "socket closed" in (result.detail or "")
        terminal = (await _trail(calls, device.id))[-1]
        assert terminal.payload["deleted_paths"] == [PORT]

    async def test_intent_not_found_sends_nothing(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        calls = _RemoveCalls(dcfabric_desired_state, [_drifted(device)])
        calls.intent_store.get_desired_state = AsyncMock(return_value=[])

        result = await _remove(calls, _request(device.id))

        assert result.reason == WorkflowReason.DEVICE_NOT_FOUND
        assert result.device_name is None
        assert result.deleted == []
        calls.collector.collect.assert_not_awaited()
        calls.executor.remove.assert_not_awaited()

    async def test_the_audit_trail(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        calls = _RemoveCalls(dcfabric_desired_state, [_drifted(device), _after(device)])

        await _remove(calls, _request(device.id))

        events = await _trail(calls, device.id)
        assert {e.workflow_type for e in events} == {"RemoveConfig"}
        assert {e.target_id for e in events} == {device.id}
        start = events[0]
        assert start.event_type == AuditEventType.WORKFLOW_STARTED
        assert start.actor == "anton"
        assert start.payload["confirmed_paths"] == [PORT]
        assert [(e.event_type, e.activity_name) for e in events[1:-1]] == [
            (AuditEventType.ACTIVITY_COMPLETED, name)
            for name in [
                "deploy_in_flight",
                "fetch_desired_state",
                "collect_drift_state",
                "detect_drift",
                "plan_removal",
                "deploy_in_flight",
                "dry_run_remove",
                "remove_config",
                "collect_drift_state",
                "detect_drift",
            ]
        ]
        [sent] = [e for e in events if e.activity_name == "remove_config"]
        assert sent.payload == {"paths": [PORT], "device_response": "{'timestamp': 1}"}
        terminal = events[-1]
        assert terminal.event_type == AuditEventType.WORKFLOW_TERMINATED
        assert terminal.outcome == "success"
        assert terminal.reason == WorkflowReason.SUCCEEDED
        assert terminal.payload == {
            "deleted_paths": [PORT],
            "device_response": "{'timestamp': 1}",
            "gone": [PORT],
            "remaining": [],
            "new_forward": [],
            "still_refused": [_POLICY_ITEM],
            "detail": None,
        }


class TestRemoveConfigCancellation:
    """Cancelled before the Set, nothing was written; after it, the trail names what was."""

    async def _cancel_while_held(self, calls: _RemoveCalls, request: RemovalRequest, hold: str):
        """Hold the ``hold``-th collect (1 = the first scan, 2 = the verifying scan) and cancel the run then."""
        import asyncio

        from .quiet import until_quiet, within

        started, release = asyncio.Event(), asyncio.Event()
        held_at = 1 if hold == "first_scan" else 2
        seen = 0
        inner = calls.collector.collect.side_effect

        async def _collect_state(*args):
            nonlocal seen
            seen += 1
            if seen == held_at:
                calls.order.append("collect")
                started.set()
                await release.wait()
                return _collect(args[0])
            return await inner(*args)

        calls.collector.collect = AsyncMock(side_effect=_collect_state)
        _CALLS.append(calls)
        set_activities(calls.activities())
        env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
        try:
            worker = Worker(
                env.client,
                task_queue=TASK_QUEUE,
                workflows=[RemoveConfigWorkflow],
                workflow_runner=build_workflow_runner(),
                activities=_REMOVE_ACTIVITIES,
            )
            running = asyncio.create_task(worker.run())
            try:
                handle = await env.client.start_workflow(
                    RemoveConfigWorkflow.run, request, id=remove_config_id(request.device_id), task_queue=TASK_QUEUE
                )
                await within(started.wait(), 10, "the held collect's start")
                await until_quiet([(handle, ["collect_drift_state"])])
                await handle.cancel()
                return await within(handle.result(), 20, "the cancelled removal's result")
            finally:
                release.set()
                try:
                    await within(worker.shutdown(), 15, "the worker's shutdown")
                finally:
                    running.cancel()
        finally:
            await within(env.shutdown(), 15, "the test server's shutdown")

    async def test_cancelled_before_the_set(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        calls = _RemoveCalls(dcfabric_desired_state, [_drifted(device)])

        result = await self._cancel_while_held(calls, _request(device.id), "first_scan")

        assert result.reason == WorkflowReason.CANCELLED
        assert result.deleted == []
        assert "remove" not in calls.order
        last = (await _trail(calls, device.id))[-1]
        assert last.event_type == AuditEventType.WORKFLOW_CANCELLED
        assert "deleted_paths" not in last.payload

    async def test_cancelled_after_the_set_names_what_was_deleted(self, dcfabric_desired_state) -> None:
        device = dcfabric_desired_state.device
        calls = _RemoveCalls(dcfabric_desired_state, [_drifted(device)])

        result = await self._cancel_while_held(calls, _request(device.id), "verifying_scan")

        assert result.reason == WorkflowReason.CANCELLED
        assert result.deleted == [PORT]
        last = (await _trail(calls, device.id))[-1]
        assert last.event_type == AuditEventType.WORKFLOW_CANCELLED
        assert last.payload["deleted_paths"] == [PORT]
