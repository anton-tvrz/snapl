"""Workflow activities: what a run needs to know about other runs (spec 013 research R8).

A removal writes nothing while a deploy to the same device is running. Workflow
code may not call Temporal's client, so the check is an activity, and it uses
the worker's own client (``activity.client()``), as ``stand_down_schedule``
does, so the activities container needs no client of its own.
"""

from __future__ import annotations

from uuid import UUID

from temporalio import activity
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode


@activity.defn(name="deploy_in_flight")
async def deploy_in_flight(device_id: UUID) -> bool:
    """Whether ``deploy-intent-{device_id}`` is running now.

    A device never deployed has no such workflow, which is not in flight.
    Any other RPC error is raised, for Temporal to retry.
    """
    handle = activity.client().get_workflow_handle(f"deploy-intent-{device_id}")
    try:
        description = await handle.describe()
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return False
        raise
    return description.status == WorkflowExecutionStatus.RUNNING
