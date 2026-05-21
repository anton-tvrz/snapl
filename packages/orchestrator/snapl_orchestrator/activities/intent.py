"""Intent-related activities — wrap snapl_intent.IntentStore calls."""

from __future__ import annotations

from uuid import UUID

from temporalio import activity

from snapl_intent.exceptions import IntentNotFoundError
from snapl_intent.models import (
    DesiredState,
    Device,
)
from snapl_orchestrator.activities import get_activities


@activity.defn(name="fetch_desired_state")
async def fetch_desired_state(device_id: UUID) -> DesiredState:
    """Fetch the desired state for one device.

    Raises:
        IntentNotFoundError: device is not present in the Source of Truth.
        IntentConnectionError: SoT unreachable.
    """
    activities = get_activities()
    states = await activities.intent_store.get_desired_state(device_id=device_id)
    if not states:
        raise IntentNotFoundError(f"No desired state found for device {device_id}")
    return states[0]


@activity.defn(name="fetch_devices_for_use_case")
async def fetch_devices_for_use_case(use_case_id: str) -> list[Device]:
    """Return every device participating in a use case."""
    activities = get_activities()
    states = await activities.intent_store.get_desired_state(use_case=use_case_id)
    return [s.device for s in states]
