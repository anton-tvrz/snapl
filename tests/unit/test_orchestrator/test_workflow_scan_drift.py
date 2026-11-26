"""Unit tests for ScanDriftWorkflow — real WorkflowEnvironment coverage (#10).

Drives the workflow against a time-skipping Temporal test environment with the
four downstream blocks mocked, mirroring test_workflow_deploy_intent.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pydantic_core  # noqa: F401 — pre-import to keep workflow sandbox quiet
import pytest
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from snapl_collector.models import CollectResult
from snapl_observability.models import DriftItem, DriftReport, DriftStatus
from snapl_orchestrator.activities import Activities, set_activities
from snapl_orchestrator.activities.audit import record_audit_event
from snapl_orchestrator.activities.collector import collect_drift_state
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
            collect_drift_state,
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


def _checks(events) -> list:
    """A device's drift check events, without the steps recorded beside them."""
    return [e for e in events if e.activity_name == "detect_drift"]


# ---------------------------------------------------------------------------
# Per-device audit trail
#
# `docs/demo-scenarios.md` scenario 8 promises "the per-device query aggregates
# deploys, scans and reconciles that touched the device". The scan's own
# workflow-level events are keyed on the *use case* ("dcfabric"), so before
# these tests a fabric-wide scan that flagged spine-01 left nothing at all
# behind `snapl audit --device spine-01` — the scan that found the drift was
# invisible in the device's story. Caught by the e2e demo arc (#100).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_records_a_per_device_audit_event(make_desired) -> None:
    """Each evaluated device gets an event keyed on its own id."""
    states = [make_desired(f"spine-0{i}") for i in range(1, 4)]
    audit = InMemoryAuditLog()
    collector = MagicMock()
    collector.get_running_config = AsyncMock(side_effect=_collect)
    collector.collect = AsyncMock(side_effect=lambda device, _paths: _collect(device))
    observer = MagicMock()
    observer.detect_drift = AsyncMock(side_effect=lambda desired, _c: _report(desired.device, DriftStatus.CLEAN))
    observer.emit_event = AsyncMock()

    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        await _run(
            env,
            _build_activities(
                intent_store=_intent_store(states), collector=collector, observer=observer, audit_log=audit
            ),
            USE_CASE,
        )

    for state in states:
        # The drift check; the collect step beside it is spec 015's (FR-016).
        events = _checks(await audit.query_by_device(state.device.id))
        assert events, f"scan left no audit trace for {state.device.name}"
        assert {e.workflow_type for e in events} == {"ScanDrift"}
        assert events[0].activity_name == "detect_drift"
        assert events[0].payload["status"] == DriftStatus.CLEAN.value


@pytest.mark.asyncio
async def test_per_device_event_carries_the_drift_finding(make_desired) -> None:
    """A drifted device's event names the count and the paths — the audit is
    the durable record of what the scan saw, not just that it ran."""
    states = [make_desired(f"spine-0{i}") for i in range(1, 3)]
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
        await _run(
            env,
            _build_activities(
                intent_store=_intent_store(states), collector=collector, observer=observer, audit_log=audit
            ),
            USE_CASE,
        )

    # The drift check; the collect step beside it is spec 015's (FR-016).
    events = _checks(await audit.query_by_device(drifted_device.id))
    assert len(events) == 1
    assert events[0].payload["status"] == DriftStatus.DRIFTED.value
    assert events[0].payload["drift_items"] == 1
    assert events[0].payload["paths"] == ["/interface[name=ethernet-1/1]/admin-state"]


@pytest.mark.asyncio
async def test_device_that_errors_is_audited_as_a_failure(make_desired) -> None:
    """An unreachable device is the case an operator most needs to find later."""
    states = [make_desired("spine-01")]
    audit = InMemoryAuditLog()
    collector = MagicMock()
    collector.get_running_config = AsyncMock(side_effect=RuntimeError("dial failed"))
    collector.collect = AsyncMock(side_effect=RuntimeError("dial failed"))
    observer = MagicMock()
    observer.detect_drift = AsyncMock()
    observer.emit_event = AsyncMock()

    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        result = await _run(
            env,
            _build_activities(
                intent_store=_intent_store(states), collector=collector, observer=observer, audit_log=audit
            ),
            USE_CASE,
        )

    assert result.errored == 1
    # The drift check; the collect step beside it is spec 015's (FR-016).
    events = _checks(await audit.query_by_device(states[0].device.id))
    assert len(events) == 1
    assert events[0].event_type is AuditEventType.ACTIVITY_FAILED
    assert events[0].outcome == "failure"


# ---------------------------------------------------------------------------
# FR-012a (spec 010, research R4): the direction of each difference survives
# into the audit trail, so history can tell missing from undesired config.
# ---------------------------------------------------------------------------

_FORWARD = DriftItem(
    path="/interface[name=ethernet-1/1]/admin-state",
    desired="enable",
    actual="disable",
    entity_kind="Interface",
)
_UNDESIRED_A = DriftItem(
    path="/interface[name=ethernet-1/7]/description",
    desired=None,
    actual="hand-added",
    entity_kind="Interface",
    undesired=True,
)
_UNDESIRED_B = DriftItem(
    path="/interface[name=ethernet-1/9]",
    desired=None,
    actual={"admin-state": "enable"},
    entity_kind="Interface",
    undesired=True,
)


async def _scan_one_device_payload(make_desired, items: list[DriftItem]) -> dict:
    states = [make_desired("leaf-01")]
    audit = InMemoryAuditLog()
    collector = MagicMock()
    collector.collect = AsyncMock(side_effect=lambda device, _paths: _collect(device))
    status = DriftStatus.DRIFTED if items else DriftStatus.CLEAN
    observer = MagicMock()
    observer.detect_drift = AsyncMock(side_effect=lambda desired, _c: _report(desired.device, status, items=items))
    observer.emit_event = AsyncMock()

    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        await _run(
            env,
            _build_activities(
                intent_store=_intent_store(states), collector=collector, observer=observer, audit_log=audit
            ),
            USE_CASE,
        )

    # The drift check; the collect step beside it is spec 015's (FR-016).
    events = _checks(await audit.query_by_device(states[0].device.id))
    assert len(events) == 1
    return events[0].payload


@pytest.mark.asyncio
async def test_per_device_event_names_the_undesired_paths_in_order(make_desired) -> None:
    payload = await _scan_one_device_payload(make_desired, [_UNDESIRED_A, _FORWARD, _UNDESIRED_B])

    assert payload["paths"] == [_UNDESIRED_A.path, _FORWARD.path, _UNDESIRED_B.path]
    assert payload["undesired_paths"] == [_UNDESIRED_A.path, _UNDESIRED_B.path]


@pytest.mark.asyncio
async def test_forward_only_drift_records_no_undesired_paths(make_desired) -> None:
    payload = await _scan_one_device_payload(make_desired, [_FORWARD])

    assert payload["undesired_paths"] == []


@pytest.mark.asyncio
async def test_clean_device_records_neither_paths_nor_undesired_paths(make_desired) -> None:
    payload = await _scan_one_device_payload(make_desired, [])

    assert payload == {"status": DriftStatus.CLEAN.value, "drift_items": 0}


# ---------------------------------------------------------------------------
# Spec 015 US4 (T038, FR-016): a scan records its steps. Reading intent is one
# step for the use case, collecting is one step per device, beside the
# `detect_drift` event per device it already writes (research R7).
# ---------------------------------------------------------------------------


def _steps(events, name: str) -> list:
    return [e for e in events if e.activity_name == name]


async def _scan_with(make_desired, *, collect, names=("spine-01", "leaf-01"), audit=None):
    states = [make_desired(name) for name in names]
    audit = audit if audit is not None else InMemoryAuditLog()
    collector = MagicMock()
    collector.collect = AsyncMock(side_effect=collect)
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
    return states, result, await audit.query_by_workflow(result.workflow_id)


@pytest.mark.asyncio
async def test_a_scan_records_reading_intent_once_for_the_use_case(make_desired) -> None:
    _, _, events = await _scan_with(make_desired, collect=lambda device, _paths: _collect(device))

    [intent] = _steps(events, "fetch_devices_for_use_case")
    assert intent.event_type is AuditEventType.ACTIVITY_COMPLETED
    assert intent.outcome == "success"
    assert intent.target_id == USE_CASE
    assert intent.payload == {"devices": 2}


@pytest.mark.asyncio
async def test_a_scan_records_collecting_each_device_beside_its_drift_check(make_desired) -> None:
    states, _, events = await _scan_with(make_desired, collect=lambda device, _paths: _collect(device))

    collects = _steps(events, "collect_drift_state")
    assert {e.target_id for e in collects} == {s.device.id for s in states}
    assert {(e.event_type, e.outcome) for e in collects} == {(AuditEventType.ACTIVITY_COMPLETED, "success")}
    # Each device's collect is recorded before its drift check, which is still there.
    for state in states:
        [collect] = [e for e in collects if e.target_id == state.device.id]
        [check] = [e for e in _steps(events, "detect_drift") if e.target_id == state.device.id]
        assert collect.timestamp <= check.timestamp


@pytest.mark.asyncio
async def test_a_collect_that_raises_is_recorded_failed_with_its_reason_and_the_scan_carries_on(make_desired) -> None:
    async def collect(device, _paths):
        if device.name == "leaf-01":
            raise ConnectionError("leaf-01 unreachable")
        return _collect(device)

    states, result, events = await _scan_with(make_desired, collect=collect)

    leaf = next(s.device for s in states if s.device.name == "leaf-01")
    [failed] = [e for e in _steps(events, "collect_drift_state") if e.target_id == leaf.id]
    assert failed.event_type is AuditEventType.ACTIVITY_FAILED
    assert failed.outcome == "failure"
    assert "leaf-01 unreachable" in failed.payload["error"]
    assert (result.total, result.clean, result.errored) == (2, 1, 1)
    assert len(_steps(events, "detect_drift")) == 2


@pytest.mark.asyncio
async def test_a_collect_that_reports_failure_is_recorded_failed_with_its_error(make_desired) -> None:
    def collect(device, _paths):
        if device.name == "leaf-01":
            return CollectResult(
                device_id=device.id, device_name=device.name, success=False, data={}, paths=[], error="gNMI timeout"
            )
        return _collect(device)

    states, _, events = await _scan_with(make_desired, collect=collect)

    leaf = next(s.device for s in states if s.device.name == "leaf-01")
    [failed] = [e for e in _steps(events, "collect_drift_state") if e.target_id == leaf.id]
    assert failed.event_type is AuditEventType.ACTIVITY_FAILED
    assert failed.payload == {"error": "gNMI timeout"}


@pytest.mark.asyncio
async def test_a_scan_that_cannot_read_intent_records_the_failed_step_and_fails(make_desired) -> None:
    audit = InMemoryAuditLog()
    store = MagicMock()
    store.get_desired_state = AsyncMock(side_effect=ConnectionError("Infrahub unreachable"))

    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        with pytest.raises(WorkflowFailureError):
            await _run(
                env,
                _build_activities(intent_store=store, collector=MagicMock(), observer=MagicMock(), audit_log=audit),
                USE_CASE,
            )

    [failed] = _steps(await audit.query_by_workflow(f"scan-drift-{USE_CASE}"), "fetch_devices_for_use_case")
    assert failed.event_type is AuditEventType.ACTIVITY_FAILED
    assert failed.target_id == USE_CASE
    assert "Infrahub unreachable" in failed.payload["error"]
