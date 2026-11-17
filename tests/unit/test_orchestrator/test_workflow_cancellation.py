"""Cancellation handling — WorkflowEnvironment coverage (#12).

Each test starts a workflow, blocks it inside an activity until it has
demonstrably begun, cancels the handle, and asserts the terminal cancellation
behaviour (reason=CANCELLED / WORKFLOW_CANCELLED audit event).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pydantic_core  # noqa: F401 — pre-import to keep workflow sandbox quiet
import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from snapl_orchestrator.activities import Activities, set_activities
from snapl_orchestrator.activities.audit import record_audit_event
from snapl_orchestrator.activities.collector import collect_drift_state
from snapl_orchestrator.activities.executor import apply_config
from snapl_orchestrator.activities.intent import (
    fetch_desired_state,
    fetch_devices_for_use_case,
)
from snapl_orchestrator.activities.observability import detect_drift
from snapl_orchestrator.audit.memory import InMemoryAuditLog
from snapl_orchestrator.models import AuditEventType, WorkflowReason
from snapl_orchestrator.worker.sandbox import build_workflow_runner
from snapl_orchestrator.workflows.deploy_intent import DeployIntentWorkflow
from snapl_orchestrator.workflows.reconcile_devices import ReconcileDevicesWorkflow
from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow

pytestmark = pytest.mark.unit

_DEPLOY_ACTIVITIES = [
    fetch_desired_state,
    fetch_devices_for_use_case,
    apply_config,
    collect_drift_state,
    detect_drift,
    record_audit_event,
]


def teardown_function() -> None:
    import snapl_orchestrator.activities as a

    a._activities = None


def _install_blocking_intent(state, audit, started: asyncio.Event) -> None:
    """Install activities whose intent fetch blocks until cancelled."""

    async def _get(*, device_id=None, use_case=None):
        started.set()
        await asyncio.sleep(30)  # released by cancellation / worker shutdown
        return [state]

    intent_store = MagicMock()
    intent_store.get_desired_state = AsyncMock(side_effect=_get)
    set_activities(
        Activities(
            intent_store=intent_store,
            executor=MagicMock(),
            collector=MagicMock(),
            observer=MagicMock(),
            audit_log=audit,
        )
    )


async def _start_block_cancel(workflow_run, arg, *, workflows, started, task_queue, wf_id, ready=None):
    """Start a workflow, wait until its activity has begun, cancel, return the outcome.

    ``ready``, when given, is awaited (with a deadline) after the activity has
    begun and before the cancel, for a test that cancels at a later point.

    Returns the workflow result, or the raised exception for workflows that
    audit and re-raise on cancellation (reconcile) — callers can then assert
    the audit trail regardless of how the workflow terminates.
    """
    async with (
        await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env,
        Worker(
            env.client,
            task_queue=task_queue,
            workflows=workflows,
            workflow_runner=build_workflow_runner(),
            activities=_DEPLOY_ACTIVITIES,
        ),
    ):
        handle = await env.client.start_workflow(workflow_run, arg, id=wf_id, task_queue=task_queue)
        await asyncio.wait_for(started.wait(), timeout=10)
        if ready is not None:
            await ready()
        await handle.cancel()
        try:
            return await handle.result()
        except Exception as exc:
            return exc


@pytest.mark.asyncio
async def test_deploy_intent_cancellation_records_cancelled_event(dcfabric_desired_state) -> None:
    device = dcfabric_desired_state.device
    audit = InMemoryAuditLog()
    started = asyncio.Event()
    _install_blocking_intent(dcfabric_desired_state, audit, started)

    wf_id = f"deploy-cancel-{device.id}"
    result = await _start_block_cancel(
        DeployIntentWorkflow.run,
        device.id,
        workflows=[DeployIntentWorkflow],
        started=started,
        task_queue="test-cancel-deploy",
        wf_id=wf_id,
    )

    types = [e.event_type for e in await audit.query_by_workflow(wf_id)]
    assert AuditEventType.WORKFLOW_CANCELLED in types
    assert result.reason == WorkflowReason.CANCELLED


@pytest.mark.asyncio
async def test_scan_drift_cancellation_records_cancelled_event(make_desired) -> None:
    audit = InMemoryAuditLog()
    started = asyncio.Event()
    _install_blocking_intent(make_desired("spine-01"), audit, started)

    wf_id = "scan-cancel-dcfabric"
    result = await _start_block_cancel(
        ScanDriftWorkflow.run,
        "dcfabric",
        workflows=[ScanDriftWorkflow],
        started=started,
        task_queue="test-cancel-scan",
        wf_id=wf_id,
    )

    types = [e.event_type for e in await audit.query_by_workflow(wf_id)]
    assert AuditEventType.WORKFLOW_CANCELLED in types
    # The empty result must not read as a clean scan of zero devices.
    assert result.cancelled is True


@pytest.mark.asyncio
async def test_reconcile_cancellation_propagates_to_children(make_desired) -> None:
    state = make_desired("spine-01")
    device_id = state.device.id
    audit = InMemoryAuditLog()
    started = asyncio.Event()
    _install_blocking_intent(state, audit, started)

    wf_id = f"reconcile-cancel-{device_id}"
    await _start_block_cancel(
        ReconcileDevicesWorkflow.run,
        [device_id],
        workflows=[ReconcileDevicesWorkflow, DeployIntentWorkflow],
        started=started,
        task_queue="test-cancel-reconcile",
        wf_id=wf_id,
    )

    types = [e.event_type for e in await audit.query_by_workflow(wf_id)]
    assert AuditEventType.WORKFLOW_CANCELLED in types


@pytest.mark.asyncio
async def test_a_cancelled_scan_keeps_the_reports_that_completed(make_desired) -> None:
    """A scan cancelled part-way returns what it had finished (#36). Before, it
    returned no reports and total=0, although the per-device result was already
    in the audit trail."""
    from datetime import UTC, datetime

    from snapl_collector.models import CollectResult
    from snapl_observability.models import DriftReport, DriftStatus

    done, blocked = make_desired("spine-01"), make_desired("spine-02")
    audit = InMemoryAuditLog()
    started = asyncio.Event()

    async def _get(*, device_id=None, use_case=None):
        if use_case is not None:
            return [done, blocked]
        if device_id == done.device.id:
            return [done]
        started.set()
        await asyncio.sleep(30)  # spine-02 is held until the cancel / worker shutdown
        return [blocked]

    async def spine_01_checked() -> None:
        """Wait, with a deadline, until spine-01's drift check is in the trail.

        The workflow takes a device's report just before it records the check,
        so the cancel then arrives after the report was taken. Since spec 015
        the scan records spine-01's collect step first, so any spine-01 event
        is not enough. The wait is here, not in the held activity, so a late
        check fails this test instead of hanging the worker's shutdown.
        """
        deadline = asyncio.get_running_loop().time() + 20
        while asyncio.get_running_loop().time() < deadline:
            events = await audit.query_by_workflow(wf_id)
            if any(e.target_id == done.device.id and e.activity_name == "detect_drift" for e in events):
                return
            await asyncio.sleep(0.01)
        seen = [(e.activity_name, e.event_type.value) for e in await audit.query_by_workflow(wf_id)]
        raise AssertionError(f"spine-01's drift check was not recorded within 20 s; the trail had {seen}")

    intent_store = MagicMock()
    intent_store.get_desired_state = AsyncMock(side_effect=_get)
    collector = MagicMock()
    collector.collect = AsyncMock(
        return_value=CollectResult(
            device_id=done.device.id, device_name=done.device.name, success=True, data={}, paths=["/interface"]
        )
    )
    observer = MagicMock()
    observer.detect_drift = AsyncMock(
        return_value=DriftReport(
            device_id=done.device.id,
            device_name=done.device.name,
            status=DriftStatus.CLEAN,
            items=[],
            timestamp=datetime.now(tz=UTC),
        )
    )
    observer.emit_event = AsyncMock()
    set_activities(
        Activities(
            intent_store=intent_store, executor=MagicMock(), collector=collector, observer=observer, audit_log=audit
        )
    )

    wf_id = "scan-cancel-partial"
    result = await _start_block_cancel(
        ScanDriftWorkflow.run,
        "dcfabric",
        workflows=[ScanDriftWorkflow],
        started=started,
        task_queue="test-cancel-scan-partial",
        wf_id=wf_id,
        ready=spine_01_checked,
    )

    assert result.cancelled is True
    assert set(result.reports) == {done.device.id}
    assert (result.total, result.clean, result.drifted, result.errored) == (1, 1, 0, 0)
