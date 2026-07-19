"""Unit tests for DeployIntentWorkflow — uses Temporal's WorkflowEnvironment."""

from __future__ import annotations

import uuid as _uuid  # noqa: TC003 — used at runtime by _run_with helper
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
from snapl_intent.exceptions import IntentConnectionError
from snapl_observability.models import DriftItem, DriftReport, DriftStatus
from snapl_orchestrator.activities import Activities, set_activities
from snapl_orchestrator.activities.audit import record_audit_event
from snapl_orchestrator.activities.collector import collect_running_state
from snapl_orchestrator.activities.executor import apply_config
from snapl_orchestrator.activities.intent import fetch_desired_state
from snapl_orchestrator.activities.observability import detect_drift
from snapl_orchestrator.audit.memory import InMemoryAuditLog
from snapl_orchestrator.exceptions import AuditLogError
from snapl_orchestrator.models import AuditEventType, WorkflowReason
from snapl_orchestrator.worker.sandbox import build_workflow_runner
from snapl_orchestrator.workflows.deploy_intent import DeployIntentWorkflow

pytestmark = pytest.mark.unit

TASK_QUEUE = "test-orchestrator"


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _apply(device, *, success: bool = True, payload: dict | None = None, error: str | None = None) -> ApplyResult:
    return ApplyResult(
        device_id=device.id,
        device_name=device.name,
        success=success,
        payload=payload if payload is not None else ({} if not success else {"/interface": []}),
        error=error,
        duration_ms=10,
    )


def _collect(device, *, success: bool = True, data: dict | None = None, error: str | None = None) -> CollectResult:
    return CollectResult(
        device_id=device.id,
        device_name=device.name,
        success=success,
        data=data if data is not None else ({} if not success else {"/interface": []}),
        paths=["/interface"],
        error=error,
    )


def _clean_report(device) -> DriftReport:
    return DriftReport(
        device_id=device.id,
        device_name=device.name,
        status=DriftStatus.CLEAN,
        items=[],
        timestamp=_now(),
    )


def _drifted_report(device) -> DriftReport:
    return DriftReport(
        device_id=device.id,
        device_name=device.name,
        status=DriftStatus.DRIFTED,
        items=[
            DriftItem(
                path="/interface[name=ethernet-1/1]/admin-state",
                desired="enable",
                actual="disable",
                entity_kind="Interface",
            )
        ],
        timestamp=_now(),
    )


def _error_report(device, error: str = "kaboom") -> DriftReport:
    return DriftReport(
        device_id=device.id,
        device_name=device.name,
        status=DriftStatus.ERROR,
        items=[],
        error=error,
        timestamp=_now(),
    )


def _build_activities(
    *,
    intent_store=None,
    executor=None,
    collector=None,
    observer=None,
    audit_log=None,
) -> Activities:
    # NB: use `is None`, not `or` — an empty InMemoryAuditLog is falsy (it defines
    # __len__), so `audit_log or InMemoryAuditLog()` would silently discard a passed-in
    # but still-empty log and append to a throwaway instance.
    return Activities(
        intent_store=intent_store if intent_store is not None else MagicMock(),
        executor=executor if executor is not None else MagicMock(),
        collector=collector if collector is not None else MagicMock(),
        observer=observer if observer is not None else MagicMock(),
        audit_log=audit_log if audit_log is not None else InMemoryAuditLog(),
    )


def teardown_function() -> None:
    import snapl_orchestrator.activities as a

    a._activities = None


async def _run_with(env: WorkflowEnvironment, deps: Activities, device_id: _uuid.UUID):
    set_activities(deps)
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[DeployIntentWorkflow],
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
            DeployIntentWorkflow.run,
            device_id,
            id=f"deploy-intent-{device_id}",
            task_queue=TASK_QUEUE,
        )


@pytest.mark.asyncio
async def test_happy_path_returns_succeeded(dcfabric_desired_state) -> None:
    device = dcfabric_desired_state.device
    audit = InMemoryAuditLog()
    intent_store = MagicMock()
    intent_store.get_desired_state = AsyncMock(return_value=[dcfabric_desired_state])
    executor = MagicMock()
    executor.apply = AsyncMock(return_value=_apply(device))
    collector = MagicMock()
    collector.collect = AsyncMock(return_value=_collect(device))
    collector.get_running_config = AsyncMock(return_value=_collect(device))
    observer = MagicMock()
    observer.detect_drift = AsyncMock(return_value=_clean_report(device))
    observer.emit_event = AsyncMock()

    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        result = await _run_with(
            env,
            _build_activities(
                intent_store=intent_store,
                executor=executor,
                collector=collector,
                observer=observer,
                audit_log=audit,
            ),
            device.id,
        )

    assert result.success is True
    assert result.reason == WorkflowReason.SUCCEEDED
    # Audit log carries WORKFLOW_STARTED + 4 ACTIVITY_COMPLETED + WORKFLOW_TERMINATED.
    events = await audit.query_by_workflow(result.workflow_id)
    types = [e.event_type for e in events]
    assert AuditEventType.WORKFLOW_STARTED in types
    assert AuditEventType.WORKFLOW_TERMINATED in types
    assert types.count(AuditEventType.ACTIVITY_COMPLETED) >= 4


@pytest.mark.asyncio
async def test_intent_not_found_terminates_before_apply(dcfabric_desired_state) -> None:
    device = dcfabric_desired_state.device
    audit = InMemoryAuditLog()
    intent_store = MagicMock()
    intent_store.get_desired_state = AsyncMock(return_value=[])  # not found
    executor = MagicMock()
    executor.apply = AsyncMock()
    collector = MagicMock()
    collector.collect = AsyncMock()
    observer = MagicMock()

    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        result = await _run_with(
            env,
            _build_activities(
                intent_store=intent_store,
                executor=executor,
                collector=collector,
                observer=observer,
                audit_log=audit,
            ),
            device.id,
        )

    assert result.success is False
    assert result.reason == WorkflowReason.DEVICE_NOT_FOUND
    executor.apply.assert_not_awaited()
    collector.collect.assert_not_awaited()


@pytest.mark.asyncio
async def test_intent_connection_failure_reports_intent_unavailable(dcfabric_desired_state) -> None:
    device = dcfabric_desired_state.device
    audit = InMemoryAuditLog()
    intent_store = MagicMock()
    intent_store.get_desired_state = AsyncMock(side_effect=IntentConnectionError("SoT unreachable"))
    executor = MagicMock()
    executor.apply = AsyncMock()
    collector = MagicMock()
    collector.collect = AsyncMock()
    observer = MagicMock()

    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        result = await _run_with(
            env,
            _build_activities(
                intent_store=intent_store,
                executor=executor,
                collector=collector,
                observer=observer,
                audit_log=audit,
            ),
            device.id,
        )

    assert result.success is False
    assert result.reason == WorkflowReason.INTENT_UNAVAILABLE
    executor.apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_failure_short_circuits_collect_and_verify(dcfabric_desired_state) -> None:
    device = dcfabric_desired_state.device
    audit = InMemoryAuditLog()
    intent_store = MagicMock()
    intent_store.get_desired_state = AsyncMock(return_value=[dcfabric_desired_state])
    executor = MagicMock()
    executor.apply = AsyncMock(return_value=_apply(device, success=False, error="connectivity error"))
    collector = MagicMock()
    collector.collect = AsyncMock()
    observer = MagicMock()
    observer.detect_drift = AsyncMock()
    observer.emit_event = AsyncMock()

    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        result = await _run_with(
            env,
            _build_activities(
                intent_store=intent_store,
                executor=executor,
                collector=collector,
                observer=observer,
                audit_log=audit,
            ),
            device.id,
        )

    assert result.reason == WorkflowReason.APPLY_FAILED
    assert "connectivity error" in (result.detail or "")
    collector.collect.assert_not_awaited()
    observer.detect_drift.assert_not_awaited()


@pytest.mark.asyncio
async def test_verification_drifted_yields_verification_failed(dcfabric_desired_state) -> None:
    device = dcfabric_desired_state.device
    audit = InMemoryAuditLog()
    intent_store = MagicMock()
    intent_store.get_desired_state = AsyncMock(return_value=[dcfabric_desired_state])
    executor = MagicMock()
    executor.apply = AsyncMock(return_value=_apply(device))
    collector = MagicMock()
    collector.collect = AsyncMock(return_value=_collect(device))
    observer = MagicMock()
    observer.detect_drift = AsyncMock(return_value=_drifted_report(device))
    observer.emit_event = AsyncMock()

    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        result = await _run_with(
            env,
            _build_activities(
                intent_store=intent_store,
                executor=executor,
                collector=collector,
                observer=observer,
                audit_log=audit,
            ),
            device.id,
        )

    assert result.reason == WorkflowReason.VERIFICATION_FAILED
    assert len(result.drift_items) == 1


@pytest.mark.asyncio
async def test_collect_failure_yields_collect_failed(dcfabric_desired_state) -> None:
    device = dcfabric_desired_state.device
    audit = InMemoryAuditLog()
    intent_store = MagicMock()
    intent_store.get_desired_state = AsyncMock(return_value=[dcfabric_desired_state])
    executor = MagicMock()
    executor.apply = AsyncMock(return_value=_apply(device))
    collector = MagicMock()
    # Successful gNMI return, but Observer maps the failure case to status=ERROR.
    collector.collect = AsyncMock(return_value=_collect(device, success=False, error="timeout"))
    observer = MagicMock()
    observer.detect_drift = AsyncMock(return_value=_error_report(device, error="timeout"))
    observer.emit_event = AsyncMock()

    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        result = await _run_with(
            env,
            _build_activities(
                intent_store=intent_store,
                executor=executor,
                collector=collector,
                observer=observer,
                audit_log=audit,
            ),
            device.id,
        )

    assert result.reason == WorkflowReason.COLLECT_FAILED
    assert "timeout" in (result.detail or "")


@pytest.mark.asyncio
async def test_audit_log_failure_yields_audit_failed_terminal(dcfabric_desired_state) -> None:
    """If the audit-write activity exhausts retries, the workflow surfaces AUDIT_FAILED."""
    device = dcfabric_desired_state.device

    # Audit log that always raises — the activity's retry policy will exhaust attempts.
    failing_audit = MagicMock()
    failing_audit.append = AsyncMock(side_effect=AuditLogError("disk full"))

    intent_store = MagicMock()
    intent_store.get_desired_state = AsyncMock(return_value=[dcfabric_desired_state])
    executor = MagicMock()
    executor.apply = AsyncMock(return_value=_apply(device))
    collector = MagicMock()
    collector.collect = AsyncMock(return_value=_collect(device))
    observer = MagicMock()
    observer.detect_drift = AsyncMock(return_value=_clean_report(device))
    observer.emit_event = AsyncMock()

    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        with pytest.raises(WorkflowFailureError) as excinfo:
            await _run_with(
                env,
                _build_activities(
                    intent_store=intent_store,
                    executor=executor,
                    collector=collector,
                    observer=observer,
                    audit_log=failing_audit,
                ),
                device.id,
            )
    # The workflow propagates the failure of the audit activity as a WorkflowFailureError
    # whose *cause chain* (ActivityError → ApplicationError) carries the AuditLogError
    # detail — the top-level message is just "Workflow execution failed".
    chain = []
    err: BaseException | None = excinfo.value
    while err is not None:
        chain.append(str(err))
        err = err.__cause__
    joined = " ".join(chain).lower()
    assert "audit" in joined or "disk" in joined
