"""Cancelling a workflow in a test only once it is quiet (#333, #375).

The time-skipping test server refuses a workflow task that cancels an
activity whose completion arrived while that task was running
(``ACTIVITY_UNKNOWN``), and never times the task out, so the run hangs. A
test that cancels as soon as an activity's side effect shows (an audit
record, a fake's call) can send its cancel while some activity is still
completing. ``until_quiet`` waits until no activity can complete inside the
cancel's workflow task: only the held activities are pending, and the last
workflow task has been answered, with none scheduled after it.

``SlowAfterCancel`` makes the race certain rather than rare, so a test can
show that its wait is enough.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
from typing import TYPE_CHECKING

from temporalio.api.enums.v1 import EventType, PendingActivityState
from temporalio.service import RPCError, RPCStatusCode

if TYPE_CHECKING:
    from collections.abc import Iterable

    from temporalio.client import WorkflowHandle


class SlowAfterCancel(concurrent.futures.ThreadPoolExecutor):
    """Runs workflow activations, each ``delay`` seconds late once ``slow`` is set (#333).

    Pass it as a worker's ``workflow_task_executor``. Host code, outside the
    sandbox. It stands in for a slow CI runner, so a test can make the
    cancel's workflow task long enough for an activity's completion to land
    inside it. The delay stays under the SDK's 2 s deadlock limit.
    """

    def __init__(self, delay: float) -> None:
        super().__init__(max_workers=4)
        self.delay = delay
        self.slow = False

    def submit(self, fn, /, *args, **kwargs):
        def late():
            if self.slow:
                time.sleep(self.delay)
            return fn(*args, **kwargs)

        return super().submit(late)


async def within(awaitable, seconds: float, what: str):
    """Await with a deadline; on a miss, fail naming the step instead of hanging (#298)."""
    try:
        return await asyncio.wait_for(awaitable, timeout=seconds)
    except TimeoutError:
        raise AssertionError(f"{what} did not finish within {seconds:g} s") from None


async def where_it_stopped(handle: WorkflowHandle) -> str:
    """The run's status, pending activities and last history events, for a failure message."""
    try:
        desc = await within(handle.describe(), 5, "describing the run")
        history = await within(handle.fetch_history(), 5, "fetching the run's history")
    except AssertionError as err:
        return str(err)
    pending = [
        (p.activity_type.name, PendingActivityState.Name(p.state)) for p in desc.raw_description.pending_activities
    ]
    last = [EventType.Name(e.event_type).removeprefix("EVENT_TYPE_") for e in history.events[-12:]]
    return f"status {desc.status.name if desc.status else None}; pending activities {pending}; last events {last}"


_WORKFLOW_TASK_EVENTS = {
    EventType.EVENT_TYPE_WORKFLOW_TASK_SCHEDULED,
    EventType.EVENT_TYPE_WORKFLOW_TASK_STARTED,
    EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED,
    EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED,
    EventType.EVENT_TYPE_WORKFLOW_TASK_TIMED_OUT,
}


async def _is_quiet(handle: WorkflowHandle, pending: list[str]) -> bool:
    try:
        desc = await handle.describe()
    except RPCError as err:
        if err.status is RPCStatusCode.NOT_FOUND:  # a child not started yet
            return False
        raise
    names = sorted(p.activity_type.name for p in desc.raw_description.pending_activities)
    tasks = [e.event_type for e in (await handle.fetch_history()).events if e.event_type in _WORKFLOW_TASK_EVENTS]
    return names == sorted(pending) and bool(tasks) and tasks[-1] == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED


async def until_quiet(
    runs: Iterable[tuple[WorkflowHandle, list[str]]],
    *,
    seconds: float = 20,
) -> None:
    """Wait, with a deadline, until every run is quiet.

    Each entry is a run and the activities it should have pending: the ones
    the test holds. A run is quiet when exactly those are pending and its last
    workflow task event is ``WORKFLOW_TASK_COMPLETED``: no task is scheduled
    or running. Events that task's commands added (an activity scheduled, a
    child started) may follow it. A cancel sent then can't meet an activity
    completing inside its workflow task.

    Name every run the cancel reaches (a parent and its children), since the
    race is in whichever run handles it.
    """
    runs = list(runs)
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        if all([await _is_quiet(handle, pending) for handle, pending in runs]):
            return
        await asyncio.sleep(0.05)
    stopped = [f"{handle.id}: {await where_it_stopped(handle)}" for handle, _ in runs]
    raise AssertionError(f"not quiet within {seconds:g} s: " + " | ".join(stopped))
