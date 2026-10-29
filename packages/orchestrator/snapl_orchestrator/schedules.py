"""Drift schedules — create, inspect and steer the Temporal Schedule per use case (spec 009).

Plain async functions over a connected ``Client``, called by the CLI and by
``invoke demo.up``. They work with no worker running (FR-022): stopping
unattended writes is the command most likely to be needed when the worker is
the thing that is wrong. Every mutating function appends exactly one
``SCHEDULE_CHANGED`` audit event, after its Temporal call succeeds.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from temporalio.api.common.v1 import Payload
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
    ScheduleUpdate,
    WorkflowExecutionStatus,
)
from temporalio.service import RPCError, RPCStatusCode

from snapl_orchestrator.exceptions import ScheduleNotFoundError
from snapl_orchestrator.models import (
    AuditEvent,
    AuditEventType,
    DriftResponseMode,
    DriftResponseRequest,
    DriftResponseResult,
    DriftScheduleView,
    RunSummary,
    ScheduleChange,
    ScheduledRunInfo,
)
from snapl_orchestrator.models import drift_schedule_id as schedule_id_for

if TYPE_CHECKING:
    from snapl_orchestrator.audit.abc import AuditLog

__all__ = [
    "DEFAULT_SCAN_INTERVAL",
    "MIN_SCAN_INTERVAL",
    "build_drift_schedule",
    "describe_drift_schedule",
    "ensure_drift_schedule",
    "find_drift_schedule",
    "schedule_id_for",
]

DEFAULT_SCAN_INTERVAL = timedelta(minutes=5)
MIN_SCAN_INTERVAL = timedelta(minutes=1)

_WORKFLOW_TYPE = "DriftResponse"
_AUDIT_WORKFLOW_TYPE = "DriftSchedule"
_RECENT_RUNS = 5
_NEXT_RUNS = 3


def build_drift_schedule(
    *,
    use_case_id: str,
    interval: timedelta,
    task_queue: str,
    mode: DriftResponseMode = DriftResponseMode.REPORT,
) -> Schedule:
    """The Schedule for one use case's drift scans. Pure.

    Overlap is SKIP, so a slow run makes the next firing a no-op rather than a
    queued stale scan. The catch-up window is one interval: the SDK default
    (365 days) would replay every firing missed while Temporal or the laptop
    was down, and in remediate mode that is a write for a moment that passed.
    """
    if interval < MIN_SCAN_INTERVAL:
        raise ValueError(f"interval {interval} is below the 1m minimum")
    schedule_id = schedule_id_for(use_case_id)
    return Schedule(
        action=ScheduleActionStartWorkflow(
            _WORKFLOW_TYPE,
            DriftResponseRequest(use_case_id=use_case_id, mode=mode, schedule_id=schedule_id),
            id=schedule_id,
            task_queue=task_queue,
            static_summary=f"drift response: {mode.value}",
        ),
        spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=interval)]),
        policy=SchedulePolicy(
            overlap=ScheduleOverlapPolicy.SKIP,
            catchup_window=interval,
            pause_on_failure=False,
        ),
        state=ScheduleState(),
    )


# ---------------------------------------------------------------------------
# Operator control
# ---------------------------------------------------------------------------


async def ensure_drift_schedule(
    client: Client,
    *,
    use_case_id: str,
    task_queue: str,
    audit_log: AuditLog,
    actor: str,
    interval: timedelta = DEFAULT_SCAN_INTERVAL,
) -> DriftScheduleView:
    """Create the use case's schedule in REPORT, or change only its interval.

    Re-running setup must never un-pause a suppressed schedule or flip its
    response (FR-004), so an existing schedule keeps its action and state; only
    the interval and the catch-up window that tracks it change. Records
    ``created`` or ``interval_changed``, and nothing when the interval is unchanged.
    """
    schedule = build_drift_schedule(use_case_id=use_case_id, interval=interval, task_queue=task_queue)
    schedule_id = schedule_id_for(use_case_id)
    try:
        await client.create_schedule(schedule_id, schedule)
    except ScheduleAlreadyRunningError:
        previous: list[timedelta] = []

        def _updater(update_input) -> ScheduleUpdate | None:
            current = update_input.description.schedule
            old = current.spec.intervals[0].every if current.spec.intervals else None
            previous[:] = [old] if old is not None else []
            if old == interval:
                return None
            return ScheduleUpdate(
                schedule=dataclasses.replace(
                    current,
                    spec=schedule.spec,
                    policy=dataclasses.replace(current.policy, catchup_window=interval),
                )
            )

        await _handle(client, use_case_id).update(_updater)
        if previous and previous[0] != interval:
            await _record_change(
                audit_log,
                use_case_id=use_case_id,
                actor=actor,
                change=ScheduleChange.INTERVAL_CHANGED,
                from_seconds=int(previous[0].total_seconds()),
                to_seconds=int(interval.total_seconds()),
            )
    else:
        await _record_change(
            audit_log,
            use_case_id=use_case_id,
            actor=actor,
            change=ScheduleChange.CREATED,
            interval_seconds=int(interval.total_seconds()),
            mode=DriftResponseMode.REPORT.value,
        )
    return await describe_drift_schedule(client, use_case_id=use_case_id)


async def describe_drift_schedule(client: Client, *, use_case_id: str) -> DriftScheduleView:
    """Read-only projection of the schedule, with status and summary for recent runs."""
    try:
        description = await _handle(client, use_case_id).describe()
    except RPCError as exc:
        raise _not_found(use_case_id, exc) from exc
    schedule = description.schedule
    request = await _decode_request(client, schedule.action)
    info = description.info
    return DriftScheduleView(
        schedule_id=description.id,
        use_case_id=request.use_case_id,
        interval=schedule.spec.intervals[0].every,
        mode=request.mode,
        paused=schedule.state.paused,
        note=schedule.state.note or None,
        next_runs=list(info.next_action_times[:_NEXT_RUNS]),
        recent_runs=[await _run_info(client, action) for action in info.recent_actions[-_RECENT_RUNS:]],
        running=[execution.workflow_id for execution in info.running_actions],
        skipped_overlap=info.num_actions_skipped_overlap,
    )


async def find_drift_schedule(client: Client, *, use_case_id: str) -> DriftScheduleView | None:
    """``describe_drift_schedule``, but None for a use case with no schedule."""
    try:
        return await describe_drift_schedule(client, use_case_id=use_case_id)
    except ScheduleNotFoundError:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _handle(client: Client, use_case_id: str):
    return client.get_schedule_handle(schedule_id_for(use_case_id))


def _not_found(use_case_id: str, exc: RPCError) -> Exception:
    if exc.status == RPCStatusCode.NOT_FOUND:
        return ScheduleNotFoundError(f"no drift schedule for use case {use_case_id!r}")
    return exc


async def _decode_request(client: Client, action: Any) -> DriftResponseRequest:
    """The request stored in the schedule's action.

    A described schedule carries its arguments as raw Payloads; decode them with
    the client's (pydantic) converter. A schedule built in-process already holds
    the model.
    """
    args = list(action.args)
    if args and isinstance(args[0], Payload):
        [request] = await client.data_converter.decode(args, [DriftResponseRequest])
        return request
    return args[0]


async def _run_info(client: Client, action_result: Any) -> ScheduledRunInfo:
    execution = action_result.action
    handle = client.get_workflow_handle(
        execution.workflow_id,
        run_id=execution.first_execution_run_id,
        result_type=DriftResponseResult,
    )
    status = "unknown"
    summary = None
    # A missing or unreadable run must not break `schedule show`: the operator
    # still gets the run's id and time, and the Web UI has the rest.
    try:
        described = await handle.describe()
        if described.status is not None:
            status = described.status.name.lower()
        if described.status == WorkflowExecutionStatus.COMPLETED:
            summary = RunSummary.from_result(await handle.result())
    except Exception:  # noqa: S110
        pass
    return ScheduledRunInfo(
        workflow_id=execution.workflow_id,
        scheduled_at=action_result.scheduled_at,
        started_at=action_result.started_at,
        status=status,
        summary=summary,
    )


async def _record_change(
    audit_log: AuditLog,
    *,
    use_case_id: str,
    actor: str,
    change: ScheduleChange,
    **payload: Any,
) -> None:
    await audit_log.append(
        AuditEvent(
            event_id=uuid4(),
            # The schedule id as workflow_id makes `snapl audit --workflow
            # drift-response-<uc>` the schedule's whole policy history.
            workflow_id=schedule_id_for(use_case_id),
            workflow_type=_AUDIT_WORKFLOW_TYPE,
            target_id=use_case_id,
            event_type=AuditEventType.SCHEDULE_CHANGED,
            payload={"change": change.value, **payload},
            timestamp=datetime.now(tz=UTC),
            actor=actor,
        )
    )
