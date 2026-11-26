"""Observability activities — wrap snapl_observability.Observer.detect_drift() and plan_removal()."""

from __future__ import annotations

import dataclasses

from temporalio import activity

from snapl_collector.models import CollectResult
from snapl_intent.models import DesiredState
from snapl_observability.models import DriftReport, RemovalPlan
from snapl_orchestrator.activities import get_activities
from snapl_orchestrator.adapters.srlinux import normalize_srlinux_state
from snapl_orchestrator.metrics import record_drift_paths, record_scan_result


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

    Under ``ScanDrift`` the check is a scan, and it is counted (spec 015
    FR-010, FR-011). Under ``DeployIntent`` it is the deploy's verification,
    which the deploy counter already holds, so it is not counted again.
    """
    activities = get_activities()
    if collected.success:
        collected = dataclasses.replace(collected, data=normalize_srlinux_state(collected.data))
    report = await activities.observer.detect_drift(desired, collected)
    if _in_a_scan():
        record_scan_result(desired.device, report)
        record_drift_paths(desired.device, report)
    await activities.observer.emit_event(report)
    return report


@activity.defn(name="plan_removal")
async def plan_removal(report: DriftReport) -> RemovalPlan:
    """The deletes that would remove ``report``'s undesired config (spec 013).

    Pure and synchronous in the Observer; an activity only so the workflow can
    reach it without importing the drift field map (research R1).
    """
    return get_activities().observer.plan_removal(report)


_SCAN_WORKFLOW_TYPE = "ScanDrift"


def _in_a_scan() -> bool:
    return activity.in_activity() and activity.info().workflow_type == _SCAN_WORKFLOW_TYPE
