"""Drift schedules — create, inspect and steer the Temporal Schedule per use case (spec 009).

Plain async functions over a connected ``Client``, called by the CLI and by
``invoke demo.up``. They work with no worker running (FR-022): stopping
unattended writes is the command most likely to be needed when the worker is
the thing that is wrong. Every mutating function appends exactly one
``SCHEDULE_CHANGED`` audit event, after its Temporal call succeeds.
"""

from __future__ import annotations

import dataclasses
import logging
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

from snapl_orchestrator.exceptions import (
    ScheduleNotFoundError,
    ScheduleRunInProgressError,
    UnreadableScheduleError,
)
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

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_SCAN_INTERVAL",
    "MIN_SCAN_INTERVAL",
    "RUN_TIMEOUT",
    "build_drift_schedule",
    "delete_drift_schedule",
    "describe_drift_schedule",
    "ensure_drift_schedule",
    "find_drift_schedule",
    "pause_drift_schedule",
    "resume_drift_schedule",
    "schedule_id_for",
    "set_drift_response",
    "trigger_drift_schedule",
]

DEFAULT_SCAN_INTERVAL = timedelta(minutes=5)
MIN_SCAN_INTERVAL = timedelta(minutes=1)
# Long enough for a real remediate run; short enough that a run stuck retrying
# a workflow task gives back the SKIP overlap slot instead of silently
# blocking every later scan.
RUN_TIMEOUT = timedelta(minutes=30)

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
    queued stale scan, and every run is bounded by ``RUN_TIMEOUT`` so a stuck
    one cannot hold that slot forever. The catch-up window is one interval: the
    SDK default (365 days) would replay every firing missed while Temporal or
    the laptop was down, and in remediate mode that is a write for a moment
    that passed.
    """
    if interval < MIN_SCAN_INTERVAL:
        raise ValueError(f"interval {interval} is below the 1m minimum")
    return Schedule(
        action=_action(use_case_id=use_case_id, mode=mode, task_queue=task_queue),
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
    response (FR-004), so an existing schedule keeps its response and state;
    only the interval and the catch-up window that tracks it change, plus the
    run timeout on a schedule created before runs had one. Records ``created``
    or ``interval_changed``, and nothing when the interval is unchanged.
    """
    schedule = build_drift_schedule(use_case_id=use_case_id, interval=interval, task_queue=task_queue)
    schedule_id = schedule_id_for(use_case_id)
    try:
        await client.create_schedule(schedule_id, schedule)
    except ScheduleAlreadyRunningError:
        # None when the interval was replaced outside snapl (e.g. by a calendar spec).
        previous: list[timedelta | None] = []

        def _updater(update_input) -> ScheduleUpdate | None:
            current = update_input.description.schedule
            old = current.spec.intervals[0].every if current.spec.intervals else None
            previous[:] = [old]
            if old == interval and current.action.execution_timeout == RUN_TIMEOUT:
                return None
            return ScheduleUpdate(
                schedule=dataclasses.replace(
                    current,
                    # The described action keeps its encoded request, so the response is untouched.
                    action=dataclasses.replace(current.action, execution_timeout=RUN_TIMEOUT),
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
                from_seconds=int(previous[0].total_seconds()) if previous[0] is not None else None,
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


async def set_drift_response(
    client: Client, *, use_case_id: str, mode: DriftResponseMode, audit_log: AuditLog, actor: str
) -> DriftScheduleView:
    """Switch what scheduled runs do. Takes effect from the next run; a run in
    progress keeps the mode it started with (FR-016).

    Only the action is rebuilt; interval, policy and pause state are kept. The
    current mode is read from the description the update is built on, not an
    earlier read, so a switch that landed in between is not undone or audited
    with a stale ``from`` (Temporal's update has no conflict token, so a
    narrow window remains). The caller confirms before switching to
    REMEDIATE — this function does not prompt.
    """
    previous: list[DriftResponseMode] = []

    async def _updater(update_input) -> ScheduleUpdate | None:
        schedule = update_input.description.schedule
        previous[:] = [(await _decode_request(client, schedule.action)).mode]
        if previous[0] is mode:
            return None
        return ScheduleUpdate(
            schedule=dataclasses.replace(
                schedule,
                action=_action(use_case_id=use_case_id, mode=mode, task_queue=schedule.action.task_queue),
            )
        )

    try:
        await _handle(client, use_case_id).update(_updater)
    except RPCError as exc:
        _raise_if_not_found(use_case_id, exc)
        raise
    if previous[0] is not mode:
        await _record_change(
            audit_log,
            use_case_id=use_case_id,
            actor=actor,
            change=ScheduleChange.RESPONSE_CHANGED,
            **{"from": previous[0].value, "to": mode.value},
        )
    return await describe_drift_schedule(client, use_case_id=use_case_id)


async def pause_drift_schedule(
    client: Client, *, use_case_id: str, reason: str, audit_log: AuditLog, actor: str
) -> None:
    """Suppress: no scheduled run starts until resumed. An unexplained pause is
    as bad as an unexplained write, so the reason is required and becomes the
    schedule's note, visible in the Web UI too."""
    if not reason.strip():
        raise ValueError("pausing a drift schedule requires a reason")
    try:
        await _handle(client, use_case_id).pause(note=reason)
    except RPCError as exc:
        _raise_if_not_found(use_case_id, exc)
        raise
    await _record_change(audit_log, use_case_id=use_case_id, actor=actor, change=ScheduleChange.PAUSED, reason=reason)


async def resume_drift_schedule(
    client: Client, *, use_case_id: str, audit_log: AuditLog, actor: str, note: str | None = None
) -> None:
    """Runs start again at the next interval."""
    try:
        await _handle(client, use_case_id).unpause(note=note)
    except RPCError as exc:
        _raise_if_not_found(use_case_id, exc)
        raise
    await _record_change(audit_log, use_case_id=use_case_id, actor=actor, change=ScheduleChange.RESUMED, note=note)


async def trigger_drift_schedule(
    client: Client, *, use_case_id: str, audit_log: AuditLog, actor: str
) -> DriftResponseMode:
    """One run now, under the current response. Returns the mode the run uses;
    a caller about to trigger a REMEDIATE run confirms first.

    Refused while a run is in progress: the SKIP overlap policy would drop the
    trigger, and a ``triggered`` record for a run that never started would be
    wrong. A run starting between this check and the trigger still makes it a
    no-op; the window is one round trip.
    """
    view = await describe_drift_schedule(client, use_case_id=use_case_id)
    if view.running:
        raise ScheduleRunInProgressError(
            f"a run of {view.schedule_id} is already in progress ({', '.join(view.running)}); nothing was triggered"
        )
    try:
        await _handle(client, use_case_id).trigger()
    except RPCError as exc:
        _raise_if_not_found(use_case_id, exc)
        raise
    await _record_change(
        audit_log, use_case_id=use_case_id, actor=actor, change=ScheduleChange.TRIGGERED, mode=view.mode.value
    )
    return view.mode


async def delete_drift_schedule(client: Client, *, use_case_id: str, audit_log: AuditLog, actor: str) -> None:
    """No further runs start. A run in progress finishes."""
    try:
        await _handle(client, use_case_id).delete()
    except RPCError as exc:
        _raise_if_not_found(use_case_id, exc)
        raise
    await _record_change(audit_log, use_case_id=use_case_id, actor=actor, change=ScheduleChange.DELETED)


async def describe_drift_schedule(client: Client, *, use_case_id: str) -> DriftScheduleView:
    """Read-only projection of the schedule, with status and summary for recent runs."""
    try:
        description = await _handle(client, use_case_id).describe()
    except RPCError as exc:
        _raise_if_not_found(use_case_id, exc)
        raise
    schedule = description.schedule
    if not schedule.spec.intervals:
        raise UnreadableScheduleError(f"drift schedule {description.id} has no interval; it was changed outside snapl")
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


def _raise_if_not_found(use_case_id: str, exc: RPCError) -> None:
    """Translate NOT_FOUND; the caller re-raises anything else unchanged."""
    if exc.status == RPCStatusCode.NOT_FOUND:
        raise ScheduleNotFoundError(f"no drift schedule for use case {use_case_id!r}") from exc


def _action(*, use_case_id: str, mode: DriftResponseMode, task_queue: str) -> ScheduleActionStartWorkflow:
    schedule_id = schedule_id_for(use_case_id)
    return ScheduleActionStartWorkflow(
        _WORKFLOW_TYPE,
        DriftResponseRequest(use_case_id=use_case_id, mode=mode, schedule_id=schedule_id),
        id=schedule_id,
        task_queue=task_queue,
        execution_timeout=RUN_TIMEOUT,
        static_summary=f"drift response: {mode.value}",
    )


async def _decode_request(client: Client, action: Any) -> DriftResponseRequest:
    """The request stored in the schedule's action.

    A described schedule carries its arguments as raw Payloads; decode them with
    the client's (pydantic) converter. A schedule built in-process already holds
    the model.
    """
    args = list(action.args)
    if args and isinstance(args[0], Payload):
        [decoded] = await client.data_converter.decode(args, [DriftResponseRequest])
        return DriftResponseRequest.model_validate(decoded)
    return DriftResponseRequest.model_validate(args[0])


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
    except Exception:
        logger.debug("could not read scheduled run %s", execution.workflow_id, exc_info=True)
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
