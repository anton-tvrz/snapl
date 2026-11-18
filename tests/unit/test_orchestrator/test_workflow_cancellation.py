"""Cancellation handling — WorkflowEnvironment coverage (#12).

Each test starts a workflow, blocks it inside an activity until it has
demonstrably begun, cancels the handle, and asserts the terminal cancellation
behaviour (reason=CANCELLED / WORKFLOW_CANCELLED audit event).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pydantic_core  # noqa: F401 — pre-import to keep workflow sandbox quiet
import pytest
from temporalio import workflow
from temporalio.api.enums.v1 import EventType, PendingActivityState
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


def _install_blocking_intent(state, audit, started: asyncio.Event, release: asyncio.Event) -> None:
    """Install activities whose intent fetch is held until the test releases it."""

    async def _get(*, device_id=None, use_case=None):
        started.set()
        await release.wait()  # set by _start_block_cancel before the worker shuts down
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


async def _within(awaitable, seconds: float, what: str):
    """Await with a deadline; on a miss, fail naming the step instead of hanging (#298)."""
    try:
        return await asyncio.wait_for(awaitable, timeout=seconds)
    except TimeoutError:
        raise AssertionError(f"{what} did not finish within {seconds:g} s") from None


async def _where_it_stopped(handle) -> str:
    """The run's status, pending activities and last history events, for a failure message."""
    try:
        desc = await _within(handle.describe(), 5, "describing the run")
        history = await _within(handle.fetch_history(), 5, "fetching the run's history")
    except AssertionError as err:
        return str(err)
    pending = [
        (p.activity_type.name, PendingActivityState.Name(p.state)) for p in desc.raw_description.pending_activities
    ]
    last = [EventType.Name(e.event_type).removeprefix("EVENT_TYPE_") for e in history.events[-12:]]
    return f"status {desc.status.name if desc.status else None}; pending activities {pending}; last events {last}"


async def _start_block_cancel(
    workflow_run,
    arg,
    *,
    workflows,
    started,
    release,
    task_queue,
    wf_id,
    ready=None,
    result_within: float = 20,
):
    """Start a workflow, wait until its activity has begun, cancel, return the outcome.

    ``ready``, when given, is awaited (with a deadline) after the activity has
    begun and before the cancel, for a test that cancels at a later point.
    The held activity stays in flight while the cancel is handled, then
    ``release`` lets it return before the worker shuts down, so the shutdown
    never depends on cancelling it.

    Every wait has a deadline (#298): a run that does not finish after its
    cancel fails with its status, pending activities and last events, and a
    stuck worker or test server shutdown fails naming which one.

    Returns the workflow result, or the raised exception for workflows that
    audit and re-raise on cancellation (reconcile) — callers can then assert
    the audit trail regardless of how the workflow terminates.
    """
    env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    try:
        worker = Worker(
            env.client,
            task_queue=task_queue,
            workflows=workflows,
            workflow_runner=build_workflow_runner(),
            activities=_DEPLOY_ACTIVITIES,
        )
        running = asyncio.create_task(worker.run())
        try:
            handle = await env.client.start_workflow(workflow_run, arg, id=wf_id, task_queue=task_queue)
            await _within(started.wait(), 10, "the held activity's start")
            if ready is not None:
                await ready()
            await handle.cancel()
            try:
                return await asyncio.wait_for(handle.result(), timeout=result_within)
            except TimeoutError:
                raise AssertionError(
                    f"{wf_id} did not finish within {result_within:g} s after its cancel: "
                    f"{await _where_it_stopped(handle)}"
                ) from None
            except Exception as exc:
                return exc
        finally:
            release.set()
            try:
                await _within(worker.shutdown(), 15, "the worker's shutdown")
            finally:
                running.cancel()
    finally:
        await _within(env.shutdown(), 15, "the test server's shutdown")


@pytest.mark.asyncio
async def test_deploy_intent_cancellation_records_cancelled_event(dcfabric_desired_state) -> None:
    device = dcfabric_desired_state.device
    audit = InMemoryAuditLog()
    started, release = asyncio.Event(), asyncio.Event()
    _install_blocking_intent(dcfabric_desired_state, audit, started, release)

    wf_id = f"deploy-cancel-{device.id}"
    result = await _start_block_cancel(
        DeployIntentWorkflow.run,
        device.id,
        workflows=[DeployIntentWorkflow],
        started=started,
        release=release,
        task_queue="test-cancel-deploy",
        wf_id=wf_id,
    )

    types = [e.event_type for e in await audit.query_by_workflow(wf_id)]
    assert AuditEventType.WORKFLOW_CANCELLED in types
    assert result.reason == WorkflowReason.CANCELLED


@pytest.mark.asyncio
async def test_scan_drift_cancellation_records_cancelled_event(make_desired) -> None:
    audit = InMemoryAuditLog()
    started, release = asyncio.Event(), asyncio.Event()
    _install_blocking_intent(make_desired("spine-01"), audit, started, release)

    wf_id = "scan-cancel-dcfabric"
    result = await _start_block_cancel(
        ScanDriftWorkflow.run,
        "dcfabric",
        workflows=[ScanDriftWorkflow],
        started=started,
        release=release,
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
    started, release = asyncio.Event(), asyncio.Event()
    _install_blocking_intent(state, audit, started, release)

    wf_id = f"reconcile-cancel-{device_id}"
    await _start_block_cancel(
        ReconcileDevicesWorkflow.run,
        [device_id],
        workflows=[ReconcileDevicesWorkflow, DeployIntentWorkflow],
        started=started,
        release=release,
        task_queue="test-cancel-reconcile",
        wf_id=wf_id,
    )

    types = [e.event_type for e in await audit.query_by_workflow(wf_id)]
    assert AuditEventType.WORKFLOW_CANCELLED in types


@workflow.defn(name="IgnoresItsCancel")
class _IgnoresItsCancel:
    """Starts the held intent fetch, then never finishes once cancelled."""

    @workflow.run
    async def run(self, device_id: str) -> None:
        try:
            await workflow.execute_activity(
                fetch_desired_state, device_id, start_to_close_timeout=timedelta(seconds=30)
            )
        except BaseException:
            await workflow.wait_condition(lambda: False)


@pytest.mark.asyncio
async def test_a_workflow_that_never_ends_after_its_cancel_fails_in_seconds(make_desired) -> None:
    """A run that stalls after the cancel fails with what it was doing, instead of
    hanging the job until its timeout (#298)."""
    state = make_desired("spine-01")
    audit = InMemoryAuditLog()
    started, release = asyncio.Event(), asyncio.Event()
    _install_blocking_intent(state, audit, started, release)

    began = asyncio.get_running_loop().time()
    with pytest.raises(AssertionError, match=r"did not finish within 2 s after its cancel") as failure:
        await _start_block_cancel(
            _IgnoresItsCancel.run,
            str(state.device.id),
            workflows=[_IgnoresItsCancel],
            started=started,
            release=release,
            task_queue="test-cancel-never-ends",
            wf_id="cancel-never-ends",
            result_within=2,
        )

    assert asyncio.get_running_loop().time() - began < 30
    assert "RUNNING" in str(failure.value)
    assert release.is_set()


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
    started, release = asyncio.Event(), asyncio.Event()

    async def _get(*, device_id=None, use_case=None):
        if use_case is not None:
            return [done, blocked]
        if device_id == done.device.id:
            return [done]
        started.set()
        await release.wait()  # spine-02 is held until the outcome is in
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
        release=release,
        task_queue="test-cancel-scan-partial",
        wf_id=wf_id,
        ready=spine_01_checked,
    )

    assert result.cancelled is True
    assert set(result.reports) == {done.device.id}
    assert (result.total, result.clean, result.drifted, result.errored) == (1, 1, 0, 0)
