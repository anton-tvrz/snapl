"""Temporal client helpers — connect, list running workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 — runtime use in dataclass field

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

# snapl owns the 18000-18099 port block and binds no conventional default:
# 7233 is also six sibling projects' Temporal, and aiming at a well-known
# port once reached another project's stack (#107). See
# dev/knowledge/host-resource-registry.md.
#
# Defined here rather than in run.py so the CLI can import a client default
# without dragging every workflow into its import graph.
DEFAULT_TEMPORAL_HOST = "localhost:18033"
DEFAULT_NAMESPACE = "default"


@dataclass(frozen=True)
class RunningWorkflowInfo:
    """Lightweight snapshot of a running workflow returned by list_running_workflows."""

    workflow_id: str
    workflow_type: str
    start_time: datetime
    task_queue: str


async def build_client(
    *,
    target: str = DEFAULT_TEMPORAL_HOST,
    namespace: str = DEFAULT_NAMESPACE,
) -> Client:
    """Connect to a Temporal cluster with the pydantic data converter wired in."""
    return await Client.connect(
        target,
        namespace=namespace,
        data_converter=pydantic_data_converter,
    )


async def list_running_workflows(
    client: Client,
    *,
    task_queue: str | None = None,
) -> list[RunningWorkflowInfo]:
    """Return currently running workflows, optionally filtered to a task queue.

    The Temporal visibility API returns full WorkflowExecutionInfo; we project
    that down to a minimal RunningWorkflowInfo so callers (CLI / Presentation)
    don't depend on the temporalio internal model.
    """
    query = 'ExecutionStatus = "Running"'
    if task_queue:
        query += f' AND TaskQueue = "{task_queue}"'

    results: list[RunningWorkflowInfo] = []
    async for execution in client.list_workflows(query=query):
        results.append(
            RunningWorkflowInfo(
                workflow_id=execution.id,
                workflow_type=execution.workflow_type,
                start_time=execution.start_time,
                task_queue=execution.task_queue or "",
            )
        )
    return results
