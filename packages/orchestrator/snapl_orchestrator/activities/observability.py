"""Observability activity — wraps snapl_observability.Observer.detect_drift()."""

from __future__ import annotations

from temporalio import activity

from snapl_collector.models import CollectResult
from snapl_intent.models import DesiredState
from snapl_observability.models import DriftReport
from snapl_orchestrator.activities import get_activities


@activity.defn(name="detect_drift")
async def detect_drift(desired: DesiredState, collected: CollectResult) -> DriftReport:
    """Compare desired against collected state via the Observer."""
    activities = get_activities()
    return await activities.observer.detect_drift(desired, collected)
