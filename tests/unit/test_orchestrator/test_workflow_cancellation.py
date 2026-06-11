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
from snapl_orchestrator.activities.collector import collect_running_state
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
    collect_running_state,
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


async def _start_block_cancel(workflow_run, arg, *, workflows, started, task_queue, wf_id):
    """Start a workflow, wait until its activity has begun, cancel, return the outcome.

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
    await _start_block_cancel(
        ScanDriftWorkflow.run,
        "dcfabric",
        workflows=[ScanDriftWorkflow],
        started=started,
        task_queue="test-cancel-scan",
        wf_id=wf_id,
    )

    types = [e.event_type for e in await audit.query_by_workflow(wf_id)]
    assert AuditEventType.WORKFLOW_CANCELLED in types


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
