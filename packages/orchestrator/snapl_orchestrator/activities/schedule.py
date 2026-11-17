"""Schedule activity: a run returns its own schedule to report (#136, spec 009 FR-018).

The one place a run changes its schedule. Workflow code may not do I/O, so the
change is an activity, and it uses the worker's own Temporal client
(``activity.client()``) so the activities container needs no client of its own
(spec 009 research R8).
"""

from __future__ import annotations

from temporalio import activity

from snapl_orchestrator.activities import get_activities
from snapl_orchestrator.exceptions import ScheduleNotFoundError
from snapl_orchestrator.models import StandDownRequest
from snapl_orchestrator.schedules import stand_down_drift_schedule


@activity.defn(name="stand_down_schedule")
async def stand_down_schedule(request: StandDownRequest) -> bool:
    """Switch the use case's drift schedule to report. Returns whether it changed.

    False when the schedule is already in report, or was deleted while the run
    was in progress: either way there is nothing left to stand down, and
    failing would only make Temporal retry.
    """
    try:
        return await stand_down_drift_schedule(
            activity.client(),
            use_case_id=request.use_case_id,
            reason=request.reason,
            audit_log=get_activities().audit_log,
        )
    except ScheduleNotFoundError:
        activity.logger.warning("no drift schedule for %s to stand down", request.use_case_id)
        return False
