"""Drift schedules — create, inspect and steer the Temporal Schedule per use case (spec 009).

Plain async functions over a connected ``Client``, called by the CLI and by
``invoke demo.up``. They work with no worker running (FR-022): stopping
unattended writes is the command most likely to be needed when the worker is
the thing that is wrong. Every mutating function appends exactly one
``SCHEDULE_CHANGED`` audit event, after its Temporal call succeeds.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
)

from snapl_orchestrator.models import DriftResponseMode, DriftResponseRequest
from snapl_orchestrator.models import drift_schedule_id as schedule_id_for

__all__ = [
    "DEFAULT_SCAN_INTERVAL",
    "MIN_SCAN_INTERVAL",
    "build_drift_schedule",
    "schedule_id_for",
]

DEFAULT_SCAN_INTERVAL = timedelta(minutes=5)
MIN_SCAN_INTERVAL = timedelta(minutes=1)

_WORKFLOW_TYPE = "DriftResponse"


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
