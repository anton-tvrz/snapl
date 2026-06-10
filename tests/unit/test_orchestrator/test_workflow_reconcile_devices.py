"""Unit tests for ReconcileDevicesWorkflow.

Input-validation and class-registration tests run without a Temporal server.
The per-device outcome tests drive the workflow (and its child DeployIntent
workflows) against a time-skipping environment, mirroring
test_workflow_deploy_intent.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pydantic_core  # noqa: F401 — pre-import to keep workflow sandbox quiet
import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from snapl_collector.models import CollectResult
from snapl_executor.models import ApplyResult
from snapl_observability.models import DriftItem, DriftReport, DriftStatus
from snapl_orchestrator.activities import Activities, set_activities
from snapl_orchestrator.activities.audit import record_audit_event
from snapl_orchestrator.activities.collector import collect_running_state
from snapl_orchestrator.activities.executor import apply_config
from snapl_orchestrator.activities.intent import fetch_desired_state
from snapl_orchestrator.activities.observability import detect_drift
from snapl_orchestrator.audit.memory import InMemoryAuditLog
from snapl_orchestrator.models import WorkflowReason
from snapl_orchestrator.worker.sandbox import build_workflow_runner
from snapl_orchestrator.workflows.deploy_intent import DeployIntentWorkflow
from snapl_orchestrator.workflows.reconcile_devices import ReconcileDevicesWorkflow

pytestmark = pytest.mark.unit

TASK_QUEUE = "test-reconcile"


def _now() -> datetime:
    return datetime.now(tz=UTC)


def test_reconcile_devices_workflow_class_registered() -> None:
    """ReconcileDevicesWorkflow is a Temporal workflow class with the right name."""
    defn = getattr(ReconcileDevicesWorkflow, "__temporal_workflow_definition", None)
    assert defn is not None
    assert defn.name == "ReconcileDevices"


@pytest.mark.asyncio
async def test_reconcile_devices_workflow_rejects_empty_device_ids() -> None:
    """Calling run() with an empty list raises ValueError before any child workflow."""
    wf = ReconcileDevicesWorkflow()
    with pytest.raises(ValueError, match="device_ids must be non-empty"):
        await wf.run([])


# ---------------------------------------------------------------------------
# WorkflowEnvironment-backed per-device outcome tests
# ---------------------------------------------------------------------------


def _apply(device) -> ApplyResult:
    return ApplyResult(
        device_id=device.id,
        device_name=device.name,
        success=True,
        payload={"/interface": []},
        duration_ms=5,
    )


def _collect(device) -> CollectResult:
    return CollectResult(
        device_id=device.id,
        device_name=device.name,
        success=True,
        data={"/interface": []},
        paths=["/interface"],
    )


def _report(device, status: DriftStatus, *, items=None) -> DriftReport:
    return DriftReport(
        device_id=device.id,
        device_name=device.name,
        status=status,
        items=items or [],
        timestamp=_now(),
    )


def _drift_item() -> DriftItem:
    return DriftItem(
        path="/interface[name=ethernet-1/1]/admin-state",
        desired="enable",
        actual="disable",
        entity_kind="Interface",
    )


def _build_activities(states: list, *, drifted_ids: set | None = None) -> Activities:
    """Wire mocked blocks so each device's DeployIntent runs deterministically.

    Devices whose id is in ``drifted_ids`` come back DRIFTED (→ VERIFICATION_FAILED);
    all others come back CLEAN (→ SUCCEEDED).
    """
    drifted_ids = drifted_ids or set()
    by_device = {s.device.id: s for s in states}

    async def _get_desired(*, use_case=None, device_id=None):
        return [by_device[device_id]] if device_id in by_device else []

    def _detect(desired, _collected) -> DriftReport:
        if desired.device.id in drifted_ids:
            return _report(desired.device, DriftStatus.DRIFTED, items=[_drift_item()])
        return _report(desired.device, DriftStatus.CLEAN)

    intent_store = MagicMock()
    intent_store.get_desired_state = AsyncMock(side_effect=_get_desired)
    executor = MagicMock()
    executor.apply = AsyncMock(side_effect=lambda desired: _apply(desired.device))
    collector = MagicMock()
    collector.collect = AsyncMock(side_effect=lambda device, _paths: _collect(device))
    collector.get_running_config = AsyncMock(side_effect=_collect)
    observer = MagicMock()
    observer.detect_drift = AsyncMock(side_effect=_detect)

    return Activities(
        intent_store=intent_store,
        executor=executor,
        collector=collector,
        observer=observer,
        audit_log=InMemoryAuditLog(),
    )


def teardown_function() -> None:
    import snapl_orchestrator.activities as a

    a._activities = None


async def _run(env: WorkflowEnvironment, deps: Activities, device_ids: list):
    set_activities(deps)
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[ReconcileDevicesWorkflow, DeployIntentWorkflow],
        workflow_runner=build_workflow_runner(),
        activities=[
            fetch_desired_state,
            apply_config,
            collect_running_state,
            detect_drift,
            record_audit_event,
        ],
    ):
        return await env.client.execute_workflow(
            ReconcileDevicesWorkflow.run,
            device_ids,
            id=f"reconcile-{device_ids[0]}",
            task_queue=TASK_QUEUE,
        )


@pytest.mark.asyncio
async def test_all_devices_succeed(make_desired) -> None:
    states = [make_desired(f"spine-0{i}") for i in range(1, 3)]
    ids = [s.device.id for s in states]

    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        result = await _run(env, _build_activities(states), ids)

    assert result.total == 2
    assert result.succeeded == 2
    assert result.failed == 0
    assert result.skipped == 0
    assert len(result.device_results) == 2
    assert all(r.reason == WorkflowReason.SUCCEEDED for r in result.device_results.values())


@pytest.mark.asyncio
async def test_one_device_verification_fails(make_desired) -> None:
    states = [make_desired(f"spine-0{i}") for i in range(1, 3)]
    ids = [s.device.id for s in states]
    drifted = states[1].device.id

    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        result = await _run(env, _build_activities(states, drifted_ids={drifted}), ids)

    assert result.total == 2
    assert result.succeeded == 1
    assert result.failed == 1
    assert result.device_results[drifted].reason == WorkflowReason.VERIFICATION_FAILED
    assert result.device_results[drifted].success is False


@pytest.mark.xfail(
    reason="SoT-missing device is counted as failed (INTENT_UNAVAILABLE), not skipped — bug tracked in #15",
    strict=True,
)
@pytest.mark.asyncio
async def test_missing_device_is_skipped(make_desired) -> None:
    """Spec FR-004: a device absent from the SoT should be skipped, not failed."""
    present = make_desired("spine-01")
    # Only `present` is known to the intent store; the second id resolves to no state.
    missing_id = make_desired("ghost").device.id
    ids = [present.device.id, missing_id]

    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        result = await _run(env, _build_activities([present]), ids)

    assert result.succeeded == 1
    assert result.skipped == 1
    assert result.failed == 0
    assert missing_id not in result.device_results
