"""Unit tests for ScanDriftWorkflow — real WorkflowEnvironment coverage (#10).

Drives the workflow against a time-skipping Temporal test environment with the
four downstream blocks mocked, mirroring test_workflow_deploy_intent.py.
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
from snapl_observability.models import DriftItem, DriftReport, DriftStatus
from snapl_orchestrator.activities import Activities, set_activities
from snapl_orchestrator.activities.audit import record_audit_event
from snapl_orchestrator.activities.collector import collect_running_state
from snapl_orchestrator.activities.intent import (
    fetch_desired_state,
    fetch_devices_for_use_case,
)
from snapl_orchestrator.activities.observability import detect_drift
from snapl_orchestrator.audit.memory import InMemoryAuditLog
from snapl_orchestrator.models import AuditEventType, WorkflowReason
from snapl_orchestrator.worker.sandbox import build_workflow_runner
from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow

pytestmark = pytest.mark.unit

TASK_QUEUE = "test-scan-drift"
USE_CASE = "dcfabric"


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _collect(device) -> CollectResult:
    return CollectResult(
        device_id=device.id,
        device_name=device.name,
        success=True,
        data={"/interface": []},
        paths=["/interface"],
    )


def _report(device, status: DriftStatus, *, items=None, error: str | None = None) -> DriftReport:
    return DriftReport(
        device_id=device.id,
        device_name=device.name,
        status=status,
        items=items or [],
        error=error,
        timestamp=_now(),
    )


def _build_activities(*, intent_store, collector, observer, audit_log) -> Activities:
    # `is None`, not `or` — an empty InMemoryAuditLog is falsy (defines __len__).
    return Activities(
        intent_store=intent_store,
        executor=MagicMock(),
        collector=collector,
        observer=observer,
        audit_log=audit_log if audit_log is not None else InMemoryAuditLog(),
    )


def teardown_function() -> None:
    import snapl_orchestrator.activities as a

    a._activities = None


def _intent_store(states: list) -> MagicMock:
    """Mock IntentStore: get_desired_state(use_case=...) -> all states;
    get_desired_state(device_id=...) -> the single matching state."""
    by_device = {s.device.id: s for s in states}

    async def _get(*, use_case=None, device_id=None):
        if use_case is not None:
            return states
        if device_id is not None:
            return [by_device[device_id]] if device_id in by_device else []
        return []

    store = MagicMock()
    store.get_desired_state = AsyncMock(side_effect=_get)
    return store


async def _run(env: WorkflowEnvironment, deps: Activities, use_case_id: str):
    set_activities(deps)
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[ScanDriftWorkflow],
        workflow_runner=build_workflow_runner(),
        activities=[
            fetch_devices_for_use_case,
            fetch_desired_state,
            collect_running_state,
            detect_drift,
            record_audit_event,
        ],
    ):
        return await env.client.execute_workflow(
            ScanDriftWorkflow.run,
            use_case_id,
            id=f"scan-drift-{use_case_id}",
            task_queue=TASK_QUEUE,
        )


@pytest.mark.asyncio
async def test_all_clean_returns_zero_drift(make_desired) -> None:
    states = [make_desired(f"spine-0{i}") for i in range(1, 4)]
    audit = InMemoryAuditLog()
    collector = MagicMock()
    collector.get_running_config = AsyncMock(side_effect=_collect)
    collector.collect = AsyncMock(side_effect=lambda device, _paths: _collect(device))
    observer = MagicMock()
    observer.detect_drift = AsyncMock(side_effect=lambda desired, _c: _report(desired.device, DriftStatus.CLEAN))
    observer.emit_event = AsyncMock()

    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        result = await _run(
            env,
            _build_activities(
                intent_store=_intent_store(states), collector=collector, observer=observer, audit_log=audit
            ),
            USE_CASE,
        )

    assert result.total == 3
    assert result.clean == 3
    assert result.drifted == 0
    assert result.errored == 0
    assert len(result.reports) == 3


@pytest.mark.asyncio
async def test_one_drifted_device_identified(make_desired) -> None:
    states = [make_desired(f"spine-0{i}") for i in range(1, 4)]
    drifted_device = states[1].device
    audit = InMemoryAuditLog()
    collector = MagicMock()
    collector.get_running_config = AsyncMock(side_effect=_collect)
    collector.collect = AsyncMock(side_effect=lambda device, _paths: _collect(device))

    def _detect(desired, _c) -> DriftReport:
        if desired.device.id == drifted_device.id:
            return _report(
                desired.device,
                DriftStatus.DRIFTED,
                items=[
                    DriftItem(
                        path="/interface[name=ethernet-1/1]/admin-state",
                        desired="enable",
                        actual="disable",
                        entity_kind="Interface",
                    )
                ],
            )
        return _report(desired.device, DriftStatus.CLEAN)

    observer = MagicMock()
    observer.detect_drift = AsyncMock(side_effect=_detect)
    observer.emit_event = AsyncMock()

    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        result = await _run(
            env,
            _build_activities(
                intent_store=_intent_store(states), collector=collector, observer=observer, audit_log=audit
            ),
            USE_CASE,
        )

    assert result.drifted == 1
    assert result.clean == 2
    report = result.reports[drifted_device.id]
    assert report.status == DriftStatus.DRIFTED
    assert report.items[0].path == "/interface[name=ethernet-1/1]/admin-state"


@pytest.mark.asyncio
async def test_collect_failure_isolated_to_errored_count(make_desired) -> None:
    states = [make_desired(f"spine-0{i}") for i in range(1, 4)]
    failing_device = states[2].device
    audit = InMemoryAuditLog()

    async def _grc(device):
        if device.id == failing_device.id:
            raise ConnectionError("device unreachable")
        return _collect(device)

    collector = MagicMock()

    async def _grc_collect(device, paths):
        return await _grc(device)

    collector.get_running_config = AsyncMock(side_effect=_grc)
    collector.collect = AsyncMock(side_effect=_grc_collect)
    observer = MagicMock()
    observer.detect_drift = AsyncMock(side_effect=lambda desired, _c: _report(desired.device, DriftStatus.CLEAN))
    observer.emit_event = AsyncMock()

    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        result = await _run(
            env,
            _build_activities(
                intent_store=_intent_store(states), collector=collector, observer=observer, audit_log=audit
            ),
            USE_CASE,
        )

    # The one device's collect failure does not abort the scan.
    assert result.total == 3
    assert result.errored == 1
    assert result.clean == 2
    assert result.reports[failing_device.id].status == DriftStatus.ERROR


@pytest.mark.asyncio
async def test_records_started_and_terminated_audit_events(make_desired) -> None:
    states = [make_desired("spine-01")]
    audit = InMemoryAuditLog()
    collector = MagicMock()
    collector.get_running_config = AsyncMock(side_effect=_collect)
    collector.collect = AsyncMock(side_effect=lambda device, _paths: _collect(device))
    observer = MagicMock()
    observer.detect_drift = AsyncMock(side_effect=lambda desired, _c: _report(desired.device, DriftStatus.CLEAN))
    observer.emit_event = AsyncMock()

    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        result = await _run(
            env,
            _build_activities(
                intent_store=_intent_store(states), collector=collector, observer=observer, audit_log=audit
            ),
            USE_CASE,
        )

    events = await audit.query_by_workflow(result.workflow_id)
    types = [e.event_type for e in events]
    assert AuditEventType.WORKFLOW_STARTED in types
    assert AuditEventType.WORKFLOW_TERMINATED in types
    terminal = next(e for e in events if e.event_type == AuditEventType.WORKFLOW_TERMINATED)
    assert terminal.reason == WorkflowReason.SUCCEEDED
