"""Executor activity — wraps snapl_executor.Executor.apply()."""

from __future__ import annotations

import time

from temporalio import activity

from snapl_executor.models import ApplyResult
from snapl_intent.models import DesiredState
from snapl_orchestrator.activities import get_activities
from snapl_orchestrator.metrics import CallOutcome, GnmiOperation, record_gnmi_call


@activity.defn(name="apply_config")
async def apply_config(desired: DesiredState) -> ApplyResult:
    """Apply the desired state to the device via the Executor.

    Returns an ApplyResult — device-side failures are reflected in
    ``ApplyResult.success`` and ``ApplyResult.error`` rather than raised.

    The call's wall time is recorded as ``snapl_gnmi_call_duration`` (spec 012
    R5). The outcome starts as ``error`` so that an exception, cancellation
    included, is recorded as one and then propagates unchanged.
    """
    activities = get_activities()
    outcome = CallOutcome.ERROR
    started = time.monotonic()
    try:
        result = await activities.executor.apply(desired)
        outcome = CallOutcome.OK if result.success else CallOutcome.ERROR
        return result
    finally:
        record_gnmi_call(desired.device, GnmiOperation.CONFIGURE, started, outcome)
