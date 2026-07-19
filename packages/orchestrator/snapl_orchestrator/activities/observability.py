"""Observability activity — wraps snapl_observability.Observer.detect_drift()."""

from __future__ import annotations

import dataclasses

from temporalio import activity

from snapl_collector.models import CollectResult
from snapl_intent.models import DesiredState
from snapl_observability.models import DriftReport
from snapl_orchestrator.activities import get_activities
from snapl_orchestrator.adapters.srlinux import normalize_srlinux_state


@activity.defn(name="detect_drift")
async def detect_drift(desired: DesiredState, collected: CollectResult) -> DriftReport:
    """Compare desired against collected state via the Observer.

    The Collector returns raw SR Linux gNMI state keyed by requested paths; the
    structural diff expects per-entity keys with flat snake_case fields. Translate
    successful collections through the adapter before handing them to the Observer
    (#32). Failed collections carry no data and pass straight through so the
    Observer still emits an ERROR report.

    Every completed check emits an ObservabilityEvent (FR-004) — this activity
    is the production emission point (#67). Delivery is at-least-once: a
    Temporal retry of this activity re-emits.
    """
    activities = get_activities()
    if collected.success:
        collected = dataclasses.replace(collected, data=normalize_srlinux_state(collected.data))
    report = await activities.observer.detect_drift(desired, collected)
    await activities.observer.emit_event(report)
    return report
