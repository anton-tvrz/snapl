"""Unit tests for the record_audit_event activity."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from snapl_orchestrator.activities import Activities, set_activities
from snapl_orchestrator.activities.audit import record_audit_event
from snapl_orchestrator.audit.memory import InMemoryAuditLog
from snapl_orchestrator.exceptions import AuditLogError
from snapl_orchestrator.models import AuditEvent, AuditEventType

pytestmark = pytest.mark.unit


def _install(audit_log) -> None:
    set_activities(
        Activities(
            intent_store=MagicMock(),
            executor=MagicMock(),
            collector=MagicMock(),
            observer=MagicMock(),
            audit_log=audit_log,
        )
    )


def teardown_function() -> None:
    import snapl_orchestrator.activities as a

    a._activities = None


def _event() -> AuditEvent:
    return AuditEvent(
        event_id=uuid4(),
        workflow_id="wf",
        workflow_type="DeployIntent",
        target_id=uuid4(),
        event_type=AuditEventType.WORKFLOW_STARTED,
        timestamp=datetime.now(tz=UTC),
    )


@pytest.mark.asyncio
async def test_record_audit_event_appends_to_log() -> None:
    log = InMemoryAuditLog()
    _install(log)
    event = _event()

    await record_audit_event(event)

    out = await log.query_by_workflow("wf")
    assert len(out) == 1
    assert out[0].event_id == event.event_id


@pytest.mark.asyncio
async def test_record_audit_event_propagates_audit_log_error() -> None:
    failing = MagicMock()

    async def boom(_event):
        raise AuditLogError("disk full")

    failing.append = boom
    _install(failing)

    with pytest.raises(AuditLogError):
        await record_audit_event(_event())
