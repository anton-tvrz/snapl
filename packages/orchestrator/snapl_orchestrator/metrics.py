"""The worker's own metrics, recorded through the Temporal SDK's meter.

- gNMI call timing (spec 012, R1, R5), from the activities.
- Scan results and drift paths (spec 015, FR-010, FR-011), from ``detect_drift``.
- Deploy ends (spec 015, FR-012), from ``DeployIntent`` through
  ``workflow.metric_meter()``, which records nothing during replay.

This module is the only place the metrics' names and labels appear, so one test
per metric can hold its label set (spec 012 SC-006, spec 015 FR-013). Every label
value comes from an enum, and a value outside it is recorded as ``unknown``, so no
series carries a path, a message or an id. The runtime adds its own
``activity_type``, ``namespace``, ``task_queue`` and ``service_name``.

A ``timedelta`` histogram is exported in milliseconds (R4). A counter is exported
under the name it is created with: the runtime adds no ``_total`` (spec 015 R6).
When the worker runs without a metrics runtime the meter is a no-op, so callers
need no special case. No recorder ever raises (spec 015 FR-014).
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from temporalio import activity, workflow

from snapl_observability.models import DriftStatus
from snapl_orchestrator.models import WorkflowReason

if TYPE_CHECKING:
    from collections.abc import Callable

    from temporalio.common import MetricMeter

    from snapl_intent.models import Device
    from snapl_observability.models import DriftReport

logger = logging.getLogger(__name__)

__all__ = [
    "DEPLOY_ENDS",
    "DRIFT_PATHS",
    "GNMI_CALL_DURATION",
    "SCAN_RESULTS",
    "CallOutcome",
    "DeployOutcome",
    "GnmiOperation",
    "PathKind",
    "ScanState",
    "record_deploy_end",
    "record_drift_paths",
    "record_gnmi_call",
    "record_scan_result",
]

GNMI_CALL_DURATION = "snapl_gnmi_call_duration"
_DESCRIPTION = "Wall time of one gNMI call made by an activity, in milliseconds"

SCAN_RESULTS = "snapl_scan_results_total"
DRIFT_PATHS = "snapl_drift_paths_total"
DEPLOY_ENDS = "snapl_deploy_ends_total"
_DESCRIPTIONS = {
    SCAN_RESULTS: "Scans of one device, by the drift state the scan found",
    DRIFT_PATHS: "Differing paths found by scans of one device, by kind",
    DEPLOY_ENDS: "Deploys to one device that ended, by outcome and reason",
}
_UNKNOWN = "unknown"
_NO_REASON = "none"


class GnmiOperation(StrEnum):
    CONFIGURE = "configure"  # executor apply
    COLLECT = "collect"  # collector collect


class CallOutcome(StrEnum):
    OK = "ok"
    ERROR = "error"


class ScanState(StrEnum):
    """What one scan found on a device, named as the dashboard names a device's state.

    ``drifted`` (a scan recorded before the undesired split) and
    ``never_scanned`` are the dashboard's alone: a scan made now always knows
    the direction of its paths.
    """

    IN_SYNC = "in_sync"
    MISSING = "missing"
    UNDESIRED = "undesired"
    MISSING_AND_UNDESIRED = "missing_and_undesired"
    ERROR = "error"
    UNKNOWN = _UNKNOWN


class PathKind(StrEnum):
    MISSING = "missing"  # intent asked for it and the device lacks it or differs
    UNDESIRED = "undesired"  # the device carries it and intent never asked
    UNCLASSIFIED = "unclassified"  # direction unknown; a scan made now never gives it


class DeployOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def record_gnmi_call(device: Device, operation: GnmiOperation, started: float, outcome: CallOutcome) -> None:
    """Record one ``snapl_gnmi_call_duration`` sample since ``started`` (a ``time.monotonic()``).

    Never raises: it runs in the activity's ``finally``, where an exception
    would replace the gNMI call's own result or exception (FR-002, FR-004).
    Outside an activity there is no meter, and nothing is recorded.
    """
    elapsed = timedelta(seconds=max(time.monotonic() - started, 0.0))
    try:
        if not activity.in_activity():
            return
        histogram = activity.metric_meter().create_histogram_timedelta(GNMI_CALL_DURATION, _DESCRIPTION, "ms")
        histogram.record(
            elapsed,
            {
                "device": device.name,
                "use_case": device.use_case,
                "operation": str(operation),
                "outcome": str(outcome),
            },
        )
    except Exception:
        logger.warning("could not record %s for device %s", GNMI_CALL_DURATION, device.name, exc_info=True)


def _scan_state(report: DriftReport) -> ScanState:
    status = str(report.status)
    if status == DriftStatus.CLEAN:
        return ScanState.IN_SYNC
    if status == DriftStatus.ERROR:
        return ScanState.ERROR
    if status != DriftStatus.DRIFTED:
        return ScanState.UNKNOWN
    undesired = sum(1 for item in report.items if item.undesired)
    if undesired == 0:
        return ScanState.MISSING
    if undesired == len(report.items):
        return ScanState.UNDESIRED
    return ScanState.MISSING_AND_UNDESIRED


def _add(meter_of: Callable[[], MetricMeter], name: str, value: int, labels: dict[str, str]) -> None:
    """Add ``value`` to the counter ``name``; log and carry on if the meter fails."""
    try:
        meter_of().create_counter(name, _DESCRIPTIONS[name]).add(value, labels)
    except Exception:
        logger.warning("could not record %s for device %s", name, labels.get("device"), exc_info=True)


def record_scan_result(device: Device, report: DriftReport) -> None:
    """Add one to ``snapl_scan_results_total`` for the state this scan found (FR-010).

    Called by ``detect_drift`` once the drift is evaluated. Outside an activity
    nothing is recorded.
    """
    if not activity.in_activity():
        return
    _add(
        activity.metric_meter,
        SCAN_RESULTS,
        1,
        {"device": device.name, "use_case": device.use_case, "state": str(_scan_state(report))},
    )


def record_drift_paths(device: Device, report: DriftReport) -> None:
    """Add this scan's differing paths to ``snapl_drift_paths_total``, by kind (FR-011).

    A kind with no path adds nothing. Outside an activity nothing is recorded.
    """
    if not activity.in_activity():
        return
    undesired = sum(1 for item in report.items if item.undesired)
    for kind, count in ((PathKind.MISSING, len(report.items) - undesired), (PathKind.UNDESIRED, undesired)):
        if count:
            _add(
                activity.metric_meter,
                DRIFT_PATHS,
                count,
                {"device": device.name, "use_case": device.use_case, "kind": str(kind)},
            )


def record_deploy_end(device_name: str, use_case: str, reason: WorkflowReason | str) -> None:
    """Add one to ``snapl_deploy_ends_total`` for a deploy that ended with ``reason`` (FR-012).

    Called by ``DeployIntent`` where it records its end. ``succeeded`` is the
    outcome with reason ``none``; every other reason is a failure, and a reason
    that is not a ``WorkflowReason`` is recorded as ``unknown``. The workflow
    meter records nothing during replay. Outside a workflow nothing is recorded.
    """
    if not workflow.in_workflow():
        return
    known = {r.value for r in WorkflowReason}
    raw = str(reason)
    if raw == WorkflowReason.SUCCEEDED:
        outcome, label = DeployOutcome.SUCCEEDED, _NO_REASON
    else:
        outcome, label = DeployOutcome.FAILED, raw if raw in known else _UNKNOWN
    _add(
        workflow.metric_meter,
        DEPLOY_ENDS,
        1,
        {"device": device_name, "use_case": use_case, "outcome": str(outcome), "reason": label},
    )
