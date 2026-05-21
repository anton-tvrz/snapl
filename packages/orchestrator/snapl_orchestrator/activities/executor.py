"""Executor activity — wraps snapl_executor.Executor.apply()."""

from __future__ import annotations

from temporalio import activity

from snapl_executor.models import ApplyResult
from snapl_intent.models import DesiredState
from snapl_orchestrator.activities import get_activities


@activity.defn(name="apply_config")
async def apply_config(desired: DesiredState) -> ApplyResult:
    """Apply the desired state to the device via the Executor.

    Returns an ApplyResult — device-side failures are reflected in
    ``ApplyResult.success`` and ``ApplyResult.error`` rather than raised.
    """
    activities = get_activities()
    return await activities.executor.apply(desired)
