"""Collector activity — wraps snapl_collector.Collector.collect()."""

from __future__ import annotations

from temporalio import activity

from snapl_collector.models import CollectResult
from snapl_intent.models import Device
from snapl_orchestrator.activities import get_activities


@activity.defn(name="collect_running_state")
async def collect_running_state(device: Device, paths: list[str]) -> CollectResult:
    """Retrieve live device state via the Collector.

    Empty ``paths`` retrieves the full running configuration; non-empty
    ``paths`` performs a targeted gNMI GET for those YANG paths.
    """
    activities = get_activities()
    if not paths:
        return await activities.collector.get_running_config(device)
    return await activities.collector.collect(device, paths)
