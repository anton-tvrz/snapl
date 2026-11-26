"""Collector activity — wraps snapl_collector.Collector.collect()."""

from __future__ import annotations

import time

from temporalio import activity

from snapl_collector.models import CollectResult
from snapl_intent.models import Device
from snapl_orchestrator.activities import get_activities
from snapl_orchestrator.adapters.srlinux import DRIFT_PATHS
from snapl_orchestrator.metrics import CallOutcome, GnmiOperation, record_gnmi_call


@activity.defn(name="collect_drift_state")
async def collect_drift_state(device: Device) -> CollectResult:
    """Retrieve the live device state that drift detection compares against intent.

    Which paths to fetch is vendor knowledge, so it is resolved here from the
    collect adapter rather than passed in by the workflows (#119). The paths
    are exactly the containers ``detect_drift`` normalizes, so the two always
    agree on shape.

    The call's wall time is recorded as ``snapl_gnmi_call_duration`` (spec 012
    R5), with the same outcome rule as ``apply_config``.
    """
    activities = get_activities()
    outcome = CallOutcome.ERROR
    started = time.monotonic()
    try:
        result = await activities.collector.collect(device, list(DRIFT_PATHS))
        outcome = CallOutcome.OK if result.success else CallOutcome.ERROR
        return result
    finally:
        record_gnmi_call(device, GnmiOperation.COLLECT, started, outcome)
