"""Audit activity — appends an AuditEvent to the durable AuditLog."""

from __future__ import annotations

from temporalio import activity

from snapl_orchestrator.activities import get_activities
from snapl_orchestrator.models import AuditEvent


@activity.defn(name="record_audit_event")
async def record_audit_event(event: AuditEvent) -> None:
    """Append an AuditEvent to the durable AuditLog.

    Failures bubble up as exceptions so Temporal's retry policy can re-attempt.
    """
    activities = get_activities()
    await activities.audit_log.append(event)
