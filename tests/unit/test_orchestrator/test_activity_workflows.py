"""The ``deploy_in_flight`` activity (spec 013 T028, research R8).

A removal refuses to write while ``deploy-intent-{device}`` runs. The activity
asks the worker's own Temporal client (``activity.client()``), as
``stand_down_schedule`` does, so the activities container needs no client.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import ActivityEnvironment

from snapl_orchestrator.activities.workflows import deploy_in_flight

pytestmark = pytest.mark.unit


class _Handle:
    def __init__(self, outcome) -> None:
        self._outcome = outcome

    async def describe(self):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return SimpleNamespace(status=self._outcome)


class _Client:
    def __init__(self, outcome) -> None:
        self._outcome = outcome
        self.asked: list[str] = []

    def get_workflow_handle(self, workflow_id: str, **_kwargs) -> _Handle:
        self.asked.append(workflow_id)
        return _Handle(self._outcome)


async def _run(outcome):
    device_id = uuid4()
    client = _Client(outcome)
    in_flight = await ActivityEnvironment(client=client).run(deploy_in_flight, device_id)  # type: ignore[arg-type]
    return in_flight, client.asked, device_id


async def test_a_running_deploy_is_in_flight() -> None:
    in_flight, asked, device_id = await _run(WorkflowExecutionStatus.RUNNING)

    assert in_flight is True
    assert asked == [f"deploy-intent-{device_id}"]


@pytest.mark.parametrize(
    "status",
    [
        WorkflowExecutionStatus.COMPLETED,
        WorkflowExecutionStatus.FAILED,
        WorkflowExecutionStatus.TERMINATED,
        WorkflowExecutionStatus.CANCELED,
        WorkflowExecutionStatus.TIMED_OUT,
    ],
)
async def test_a_deploy_that_ended_is_not_in_flight(status) -> None:
    in_flight, _, _ = await _run(status)

    assert in_flight is False


async def test_a_device_never_deployed_is_not_in_flight() -> None:
    in_flight, _, _ = await _run(RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b""))

    assert in_flight is False


async def test_any_other_rpc_error_is_raised_for_temporal_to_retry() -> None:
    with pytest.raises(RPCError):
        await _run(RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b""))
