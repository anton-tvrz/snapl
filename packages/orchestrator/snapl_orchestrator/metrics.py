"""The worker's own metrics, recorded through the Temporal SDK's meter.

- gNMI call timing (spec 012, R1, R5), from the activities.
- Scan results and drift paths (spec 015, FR-010, FR-011), from ``detect_drift``.
- Deploy ends (spec 015, FR-012), from ``DeployIntent`` through
  ``workflow.metric_meter()``, which records nothing during replay.
- Device telemetry (spec 016, research R5): interfaces, BGP sessions, CPU and
  memory, from the worker's telemetry runner through the runtime's own meter
  (``Runtime.metric_meter``), not an activity's.

This module is the only place the metrics' names and labels appear, so one test
per metric can hold its label set (spec 012 SC-006, spec 015 FR-013). Every label
value comes from an enum, and a value outside it is recorded as ``unknown``, so no
series carries a path, a message or an id. A telemetry series's ``interface`` and
``peer`` come from intent, never from the device's answer. The runtime adds its own
``activity_type``, ``namespace``, ``task_queue`` and ``service_name`` to an
activity's meter, and only ``service_name`` to its own.

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

    from snapl_collector.models import BgpSessionSample, InterfaceSample, PlatformSample
    from snapl_intent.models import Device
    from snapl_observability.models import DriftReport

logger = logging.getLogger(__name__)

__all__ = [
    "BGP_PREFIXES_RECEIVED",
    "BGP_SESSION_ESTABLISHED",
    "DEPLOY_ENDS",
    "DEVICE_CPU_PERCENT",
    "DEVICE_MEMORY_PERCENT",
    "DRIFT_PATHS",
    "GNMI_CALL_DURATION",
    "INTERFACE_DISCARDS",
    "INTERFACE_ERRORS",
    "INTERFACE_OCTETS",
    "INTERFACE_UP",
    "SCAN_RESULTS",
    "TELEMETRY_FAMILIES",
    "CallOutcome",
    "CounterTotals",
    "DeployOutcome",
    "Direction",
    "GnmiOperation",
    "PathKind",
    "ScanState",
    "record_deploy_end",
    "record_drift_paths",
    "record_gnmi_call",
    "record_interface",
    "record_platform",
    "record_scan_result",
    "record_session",
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


# ── Device telemetry (spec 016, research R5) ─────────────────────────────

INTERFACE_UP = "snapl_interface_up"
INTERFACE_OCTETS = "snapl_interface_octets_total"
INTERFACE_ERRORS = "snapl_interface_errors_total"
INTERFACE_DISCARDS = "snapl_interface_discards_total"
BGP_SESSION_ESTABLISHED = "snapl_bgp_session_established"
BGP_PREFIXES_RECEIVED = "snapl_bgp_prefixes_received"
DEVICE_CPU_PERCENT = "snapl_device_cpu_percent"
DEVICE_MEMORY_PERCENT = "snapl_device_memory_percent"

# Each family and its type, in contracts/telemetry.md's order.
TELEMETRY_FAMILIES = {
    INTERFACE_UP: "gauge",
    INTERFACE_OCTETS: "counter",
    INTERFACE_ERRORS: "counter",
    INTERFACE_DISCARDS: "counter",
    BGP_SESSION_ESTABLISHED: "gauge",
    BGP_PREFIXES_RECEIVED: "gauge",
    DEVICE_CPU_PERCENT: "gauge",
    DEVICE_MEMORY_PERCENT: "gauge",
}

_TELEMETRY_DESCRIPTIONS = {
    INTERFACE_UP: "An intent interface's operational state: 1 up, 0 otherwise",
    INTERFACE_OCTETS: "Octets through an intent interface, by direction",
    INTERFACE_ERRORS: "Error packets on an intent interface, by direction",
    INTERFACE_DISCARDS: "Discarded packets on an intent interface, by direction",
    BGP_SESSION_ESTABLISHED: "An intent BGP session end: 1 established, 0 otherwise",
    BGP_PREFIXES_RECEIVED: "Prefixes received on an intent BGP session end",
    DEVICE_CPU_PERCENT: "The device's CPU use, in percent",
    DEVICE_MEMORY_PERCENT: "The device's memory use, in percent",
}


class Direction(StrEnum):
    IN = "in"
    OUT = "out"


# Counter family -> (direction, InterfaceSample field) pairs.
_INTERFACE_COUNTERS: dict[str, tuple[tuple[Direction, str], ...]] = {
    INTERFACE_OCTETS: ((Direction.IN, "in_octets"), (Direction.OUT, "out_octets")),
    INTERFACE_ERRORS: ((Direction.IN, "in_errors"), (Direction.OUT, "out_errors")),
    INTERFACE_DISCARDS: ((Direction.IN, "in_discards"), (Direction.OUT, "out_discards")),
}


class CounterTotals:
    """The last total seen per series, to turn a device's totals into counter increments (FR-014).

    The first total adds 0, so a worker or stream start never counts a device's
    lifetime as one step. A rise adds the difference. A fall means the device's
    counter restarted, so the new total is all new. ``rate()`` never sees a
    negative or a spike.
    """

    def __init__(self) -> None:
        self._last: dict[tuple[str, str, str], int] = {}

    def delta(self, device: str, item: str, leaf: str, total: int) -> int:
        key = (device, item, leaf)
        last = self._last.get(key)
        self._last[key] = total
        if last is None:
            return 0
        return total - last if total >= last else total

    def reset(self, device: str) -> None:
        """Forget a device's totals, when its stream restarts."""
        for key in [k for k in self._last if k[0] == device]:
            del self._last[key]


def _set(meter: MetricMeter, name: str, value: int, labels: dict[str, str]) -> None:
    meter.create_gauge(name, _TELEMETRY_DESCRIPTIONS[name]).set(value, labels)


def record_interface(meter: MetricMeter, device: Device, sample: InterfaceSample, totals: CounterTotals) -> None:
    """Record what one message carried for an intent interface. A leaf it did not carry records nothing.

    ``sample.name`` is the target's name for the interface: the collector drops
    any item the target does not name, so the label comes from intent.
    """
    labels = {"device": device.name, "use_case": device.use_case, "interface": sample.name}
    try:
        if sample.oper_up is not None:
            _set(meter, INTERFACE_UP, int(sample.oper_up), labels)
        for name, pairs in _INTERFACE_COUNTERS.items():
            for direction, field in pairs:
                total = getattr(sample, field)
                if total is None:
                    continue
                increment = totals.delta(device.name, sample.name, field, total)
                meter.create_counter(name, _TELEMETRY_DESCRIPTIONS[name]).add(
                    increment, {**labels, "direction": str(direction)}
                )
    except Exception:
        logger.warning("could not record interface %s for device %s", sample.name, device.name, exc_info=True)


def record_session(meter: MetricMeter, device: Device, sample: BgpSessionSample) -> None:
    """Record what one message carried for an intent BGP session end."""
    labels = {"device": device.name, "use_case": device.use_case, "peer": sample.peer}
    try:
        if sample.state is not None:
            _set(meter, BGP_SESSION_ESTABLISHED, int(sample.state == "established"), labels)
        if sample.prefixes_received is not None:
            _set(meter, BGP_PREFIXES_RECEIVED, sample.prefixes_received, labels)
    except Exception:
        logger.warning("could not record BGP session %s for device %s", sample.peer, device.name, exc_info=True)


def record_platform(meter: MetricMeter, device: Device, sample: PlatformSample) -> None:
    """Record the device's CPU and memory use, where the message carried them."""
    labels = {"device": device.name, "use_case": device.use_case}
    try:
        if sample.cpu_percent is not None:
            _set(meter, DEVICE_CPU_PERCENT, sample.cpu_percent, labels)
        if sample.memory_percent is not None:
            _set(meter, DEVICE_MEMORY_PERCENT, sample.memory_percent, labels)
    except Exception:
        logger.warning("could not record CPU and memory for device %s", device.name, exc_info=True)
