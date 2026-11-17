"""The workflows' one way to write an audit event (#139).

Each workflow used to build its own ``AuditEvent`` and run
``record_audit_event`` with its own copy of the retry policy and timeout. They
all call ``record_event`` now, and say only what differs: the workflow type,
the target and, for unattended runs, the actor.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from snapl_orchestrator.activities.audit import record_audit_event
    from snapl_orchestrator.models import AuditEvent

if TYPE_CHECKING:
    from snapl_orchestrator.models import AuditEventType, WorkflowReason

__all__ = ["AUDIT_RETRY", "AUDIT_TIMEOUT", "record_event"]

AUDIT_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=5)
AUDIT_TIMEOUT = timedelta(seconds=10)


async def record_event(
    *,
    workflow_type: str,
    target_id: Any,
    event_type: AuditEventType,
    workflow_id: str | None = None,
    activity_name: str | None = None,
    outcome: str | None = None,
    reason: WorkflowReason | None = None,
    payload: dict | None = None,
    actor: str | None = None,
) -> None:
    """Record one audit event for the running workflow."""
    event = AuditEvent(
        event_id=workflow.uuid4(),
        workflow_id=workflow_id or workflow.info().workflow_id,
        workflow_type=workflow_type,
        target_id=target_id,
        event_type=event_type,
        activity_name=activity_name,
        outcome=outcome,
        reason=reason,
        payload=payload or {},
        timestamp=workflow.now(),
        actor=actor,
    )
    await workflow.execute_activity(
        record_audit_event,
        event,
        start_to_close_timeout=AUDIT_TIMEOUT,
        retry_policy=AUDIT_RETRY,
    )
