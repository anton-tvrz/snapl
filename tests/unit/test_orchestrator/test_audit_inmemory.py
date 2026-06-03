"""Unit tests for InMemoryAuditLog."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from snapl_orchestrator.audit.memory import InMemoryAuditLog
from snapl_orchestrator.models import AuditEvent, AuditEventType, WorkflowReason

pytestmark = pytest.mark.unit


def _event(
    *,
    workflow_id: str = "wf-1",
    target_id: UUID | str | None = None,
    event_type: AuditEventType = AuditEventType.WORKFLOW_STARTED,
    timestamp: datetime | None = None,
    activity_name: str | None = None,
    outcome: str | None = None,
    reason: WorkflowReason | None = None,
) -> AuditEvent:
    return AuditEvent(
        event_id=uuid4(),
        workflow_id=workflow_id,
        workflow_type="DeployIntent",
        target_id=target_id if target_id is not None else uuid4(),
        event_type=event_type,
        activity_name=activity_name,
        outcome=outcome,
        reason=reason,
        timestamp=timestamp or datetime.now(tz=UTC),
    )


@pytest.mark.asyncio
async def test_append_then_query_by_workflow() -> None:
    log = InMemoryAuditLog()
    e1 = _event(workflow_id="wf-A")
    e2 = _event(workflow_id="wf-B")
    e3 = _event(workflow_id="wf-A")
    await log.append(e1)
    await log.append(e2)
    await log.append(e3)
    out = await log.query_by_workflow("wf-A")
    assert [e.event_id for e in out] == [e1.event_id, e3.event_id]


@pytest.mark.asyncio
async def test_query_by_workflow_returns_chronological() -> None:
    log = InMemoryAuditLog()
    t0 = datetime(2026, 5, 1, tzinfo=UTC)
    older = _event(workflow_id="wf", timestamp=t0)
    newer = _event(workflow_id="wf", timestamp=t0 + timedelta(seconds=5))
    # Insert newer first to verify sort.
    await log.append(newer)
    await log.append(older)
    out = await log.query_by_workflow("wf")
    assert [e.event_id for e in out] == [older.event_id, newer.event_id]


@pytest.mark.asyncio
async def test_query_by_device_filters_uuid_target() -> None:
    log = InMemoryAuditLog()
    device_a = uuid4()
    device_b = uuid4()
    e1 = _event(target_id=device_a, workflow_id="wf-A")
    e2 = _event(target_id=device_b, workflow_id="wf-B")
    e3 = _event(target_id=device_a, workflow_id="wf-C")
    await log.append(e1)
    await log.append(e2)
    await log.append(e3)
    out = await log.query_by_device(device_a)
    assert {e.event_id for e in out} == {e1.event_id, e3.event_id}


@pytest.mark.asyncio
async def test_query_by_time_range_is_half_open() -> None:
    log = InMemoryAuditLog()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    e0 = _event(timestamp=base)
    e1 = _event(timestamp=base + timedelta(seconds=10))
    e2 = _event(timestamp=base + timedelta(seconds=20))
    for e in (e0, e1, e2):
        await log.append(e)
    out = await log.query_by_time_range(base + timedelta(seconds=5), base + timedelta(seconds=20))
    # e1 included; e0 before start; e2 == end → excluded (half-open).
    assert [e.event_id for e in out] == [e1.event_id]


@pytest.mark.asyncio
async def test_concurrent_appends_are_serialised() -> None:
    log = InMemoryAuditLog()
    events = [_event(workflow_id="wf") for _ in range(50)]
    await asyncio.gather(*(log.append(e) for e in events))
    out = await log.query_by_workflow("wf")
    assert len(out) == 50
    assert {e.event_id for e in out} == {e.event_id for e in events}


@pytest.mark.asyncio
async def test_query_returns_list_copy_caller_mutations_dont_affect_state() -> None:
    log = InMemoryAuditLog()
    e1 = _event(workflow_id="wf")
    await log.append(e1)
    out = await log.query_by_workflow("wf")
    out.clear()
    # Re-query — original entries still there.
    out2 = await log.query_by_workflow("wf")
    assert len(out2) == 1
