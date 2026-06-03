"""Unit tests for Temporal client helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from snapl_orchestrator.worker.client import (
    RunningWorkflowInfo,
    list_running_workflows,
)

pytestmark = pytest.mark.unit


class _AsyncIter:
    """Helper: turns a sync list into an async iterator for mocking list_workflows."""

    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


@pytest.mark.asyncio
async def test_list_running_workflows_returns_projected_info() -> None:
    client = MagicMock()
    execution = SimpleNamespace(
        id="deploy-intent-abc",
        workflow_type="DeployIntent",
        start_time=datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC),
        task_queue="snapl-orchestrator",
    )
    client.list_workflows = MagicMock(return_value=_AsyncIter([execution]))

    out = await list_running_workflows(client, task_queue="snapl-orchestrator")

    assert len(out) == 1
    assert isinstance(out[0], RunningWorkflowInfo)
    assert out[0].workflow_id == "deploy-intent-abc"
    assert out[0].workflow_type == "DeployIntent"
    assert out[0].task_queue == "snapl-orchestrator"
    # Verify the visibility query included both filters.
    args, _ = client.list_workflows.call_args
    kwargs = client.list_workflows.call_args.kwargs
    query = kwargs.get("query") or (args[0] if args else "")
    assert 'ExecutionStatus = "Running"' in query
    assert 'TaskQueue = "snapl-orchestrator"' in query


@pytest.mark.asyncio
async def test_list_running_workflows_without_task_queue_filter() -> None:
    client = MagicMock()
    client.list_workflows = MagicMock(return_value=_AsyncIter([]))

    out = await list_running_workflows(client)

    assert out == []
    kwargs = client.list_workflows.call_args.kwargs
    args = client.list_workflows.call_args.args
    query = kwargs.get("query") or (args[0] if args else "")
    assert 'ExecutionStatus = "Running"' in query
    assert "TaskQueue" not in query


@pytest.mark.asyncio
async def test_list_running_workflows_empty_when_no_workflows() -> None:
    client = MagicMock()
    client.list_workflows = MagicMock(return_value=_AsyncIter([]))

    assert await list_running_workflows(client, task_queue="x") == []


# AsyncMock import is kept for parity with other test modules.
_ = AsyncMock
