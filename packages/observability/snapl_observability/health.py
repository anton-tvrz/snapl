"""The device health rule (spec 016, research R6).

Pure: no clock and no I/O. The caller passes ``now``, so the same inputs always
give the same health. CPU and memory are not inputs (FR-008), and nothing here
touches drift (FR-010).
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from snapl_observability.models import DeviceHealth, DeviceHealthState, HealthReason

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from snapl_observability.models import HealthExpectation, HealthObservation, ObservedItem

# A device with no sample for longer than this many intervals is unknown (FR-007).
STALE_INTERVALS = 3

# An item must be out of its expected state for longer than this many intervals
# to count, so one lost or late sample never flags a device (FR-006).
DEBOUNCE_INTERVALS = 1

_ABSENT = "absent"


def evaluate_health(
    expectation: HealthExpectation,
    observation: HealthObservation,
    *,
    now: datetime,
    interval_s: int,
) -> DeviceHealth:
    """Judge one device: healthy, unhealthy with one reason per fault, or unknown.

    1. No sample yet, or none for longer than three intervals: ``unknown``.
    2. A fault is an expected item that was never reported (``absent``), or
       whose state has differed from the expected one for longer than one
       interval. Interfaces intent expects up must be up. An interface intent
       expects down must only be reported; being up is drift, not a fault.
       Sessions intent expects must be established.
    3. Any fault: ``unhealthy``. None: ``healthy``.

    Reasons list interfaces by name, then sessions by peer. A link that is down
    and the session it carries are both listed: the rule does not guess at cause.
    """
    last = observation.last_sample_at
    if last is None or now - last > timedelta(seconds=STALE_INTERVALS * interval_s):
        return DeviceHealth(
            device_name=expectation.device_name,
            state=DeviceHealthState.UNKNOWN,
            reasons=(HealthReason.no_telemetry(last),),
            evaluated_at=now,
        )

    debounce = timedelta(seconds=DEBOUNCE_INTERVALS * interval_s)
    expected_interfaces = dict.fromkeys(expectation.interfaces_up, "up")
    expected_interfaces |= dict.fromkeys(expectation.interfaces_down, "down")

    reasons: list[HealthReason] = []
    for name, expected in sorted(expected_interfaces.items()):
        actual = _fault(observation.interfaces, name, "up" if expected == "up" else None, now, debounce)
        if actual is not None:
            reasons.append(HealthReason.interface(name, actual=actual, expected=expected))
    for peer in sorted(expectation.sessions_established):
        actual = _fault(observation.sessions, peer, "established", now, debounce)
        if actual is not None:
            reasons.append(HealthReason.bgp_session(peer, actual=actual))

    return DeviceHealth(
        device_name=expectation.device_name,
        state=DeviceHealthState.UNHEALTHY if reasons else DeviceHealthState.HEALTHY,
        reasons=tuple(reasons),
        evaluated_at=now,
    )


def _fault(
    observed: Mapping[str, ObservedItem],
    name: str,
    required: str | None,
    now: datetime,
    debounce: timedelta,
) -> str | None:
    """The actual state to report if ``name`` is a fault, else ``None``.

    ``required`` is the state the item must hold, or ``None`` when any reported
    state will do.
    """
    item = observed.get(name)
    if item is None:
        return _ABSENT
    if required is None or item.state == required:
        return None
    return item.state if now - item.since > debounce else None
