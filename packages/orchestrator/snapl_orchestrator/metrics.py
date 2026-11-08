"""gNMI call timing, recorded through the Temporal SDK's activity meter (spec 012, R1, R5).

This module is the only place the metric's name and labels appear, so one test
can hold the label set to exactly ``device``, ``use_case``, ``operation`` and
``outcome`` (SC-006). The runtime adds its own ``activity_type``, ``namespace``,
``task_queue`` and ``service_name``.

A ``timedelta`` histogram is exported in milliseconds (R4). When the worker runs
without a metrics runtime the meter is a no-op, so callers need no special case.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from temporalio import activity

if TYPE_CHECKING:
    from snapl_intent.models import Device

logger = logging.getLogger(__name__)

__all__ = ["GNMI_CALL_DURATION", "CallOutcome", "GnmiOperation", "record_gnmi_call"]

GNMI_CALL_DURATION = "snapl_gnmi_call_duration"
_DESCRIPTION = "Wall time of one gNMI call made by an activity, in milliseconds"


class GnmiOperation(StrEnum):
    CONFIGURE = "configure"  # executor apply
    COLLECT = "collect"  # collector collect


class CallOutcome(StrEnum):
    OK = "ok"
    ERROR = "error"


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
