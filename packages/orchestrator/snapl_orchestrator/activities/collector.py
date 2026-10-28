"""Collector activity — wraps snapl_collector.Collector.collect()."""

from __future__ import annotations

from temporalio import activity

from snapl_collector.models import CollectResult
from snapl_intent.models import Device
from snapl_orchestrator.activities import get_activities
from snapl_orchestrator.adapters.srlinux import DRIFT_PATHS


@activity.defn(name="collect_drift_state")
async def collect_drift_state(device: Device) -> CollectResult:
    """Retrieve the live device state that drift detection compares against intent.

    Which paths to fetch is vendor knowledge, so it is resolved here from the
    collect adapter rather than passed in by the workflows (#119). The paths
    are exactly the containers ``detect_drift`` normalizes, so the two always
    agree on shape.
    """
    activities = get_activities()
    return await activities.collector.collect(device, list(DRIFT_PATHS))
