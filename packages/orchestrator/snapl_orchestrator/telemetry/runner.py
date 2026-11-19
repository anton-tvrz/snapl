"""The worker's telemetry runner (spec 016, data-model.md "The runner", research R3, R5 to R7).

One stream per device in intent, held in the worker beside the Temporal worker,
not in a workflow: a stream is not a durable business process, and every sample
would otherwise be heartbeat traffic (R2). The runner composes the blocks:

- it reads intent, and builds each device's ``TelemetryTarget`` and ``HealthExpectation``;
- it runs ``Collector.stream_telemetry`` per device and restarts a broken one
  after a backoff (1 s, doubling, capped at 30 s, reset once it streams);
- it exports each sample's figures through the recorders in ``metrics.py``;
- every interval it asks ``Observer.evaluate_health`` and records a change in
  the audit trail as ``health_changed``;
- every sixth interval it re-reads intent, restarting, stopping or starting
  streams whose target changed (FR-004).

A broken stream never touches another device's stream, a scan or a deploy
(FR-003). Nothing here raises out of a tick: a failure is logged and the next
tick tries again.

Samples are timed by the runner's clock when they arrive, not by the device's
timestamp, so health compares one clock with itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from snapl_collector.models import TelemetryTarget
from snapl_observability.models import (
    DeviceHealthState,
    HealthExpectation,
    HealthObservation,
    HealthReason,
    ObservedItem,
)
from snapl_orchestrator.metrics import CounterTotals, record_interface, record_platform, record_session
from snapl_orchestrator.models import AuditEvent, AuditEventType

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from temporalio.common import MetricMeter

    from snapl_collector.abc import Collector
    from snapl_collector.models import TelemetrySample
    from snapl_intent.abc import IntentStore
    from snapl_intent.models import DesiredState, Device
    from snapl_observability.abc import Observer
    from snapl_orchestrator.audit.abc import AuditLog

logger = logging.getLogger(__name__)

SAMPLE_INTERVAL_S = 10
"""Seconds between samples, and between health evaluations. Fixed (research R3)."""

REFRESH_TICKS = 6
"""Intent is re-read every this many ticks (60 s at the fixed interval)."""

WORKFLOW_TYPE = "DeviceTelemetry"
ACTOR = "worker"

_BACKOFF_FIRST_S = 1
_BACKOFF_MAX_S = 30


class StreamState(StrEnum):
    CONNECTING = "connecting"  # dialling, or waiting for the first, complete sample
    STREAMING = "streaming"  # the first sample arrived and the stream is open
    BROKEN = "broken"  # the stream ended; the next attempt waits out the backoff


@dataclass(frozen=True)
class _Recorded:
    """The last health written to the audit trail for a device."""

    state: DeviceHealthState
    texts: tuple[str, ...]


@dataclass
class _Device:
    target: TelemetryTarget
    expectation: HealthExpectation
    state: StreamState = StreamState.CONNECTING
    task: asyncio.Task[None] | None = None
    last_sample_at: datetime | None = None
    interfaces: dict[str, ObservedItem] = field(default_factory=dict)
    sessions: dict[str, ObservedItem] = field(default_factory=dict)
    recorded: _Recorded | None = None

    @property
    def name(self) -> str:
        return self.target.device.name


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _from_intent(states: Iterable[DesiredState]) -> dict[UUID, tuple[TelemetryTarget, HealthExpectation]]:
    """Each device's target and expectation. A device in two services is merged into one."""
    interfaces: dict[UUID, dict[str, bool]] = {}
    sessions: dict[UUID, dict[str, bool]] = {}
    devices: dict[UUID, Device] = {}
    for state in states:
        device_id = state.device.id
        devices.setdefault(device_id, state.device)
        named = interfaces.setdefault(device_id, {})
        for interface in state.interfaces:
            named[interface.name] = named.get(interface.name, False) or interface.enabled
        peers = sessions.setdefault(device_id, {})
        for session in state.bgp_sessions:
            peers[session.peer_address] = peers.get(session.peer_address, False) or session.enabled
    result = {}
    for device_id, device in devices.items():
        target = TelemetryTarget(
            device=device, interfaces=tuple(interfaces[device_id]), peers=tuple(sessions[device_id])
        )
        expectation = HealthExpectation(
            device_name=device.name,
            interfaces_up=frozenset(n for n, enabled in interfaces[device_id].items() if enabled),
            interfaces_down=frozenset(n for n, enabled in interfaces[device_id].items() if not enabled),
            sessions_established=frozenset(p for p, enabled in sessions[device_id].items() if enabled),
        )
        result[device_id] = (target, expectation)
    return result


def _observe(items: dict[str, ObservedItem], name: str, state: str, now: datetime) -> None:
    """Keep ``since`` while the state holds; restart it when the state changes."""
    current = items.get(name)
    if current is None or current.state != state:
        items[name] = ObservedItem(state=state, since=now)


class TelemetryRunner:
    """Streams every device in intent, exports the figures and records health changes."""

    def __init__(
        self,
        *,
        intent_store: IntentStore,
        collector: Collector,
        observer: Observer,
        audit_log: AuditLog,
        meter: MetricMeter,
        clock: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        interval_s: int = SAMPLE_INTERVAL_S,
    ) -> None:
        self._intent_store = intent_store
        self._collector = collector
        self._observer = observer
        self._audit_log = audit_log
        self._meter = meter
        self._clock = clock
        self._sleep = sleep
        self._interval_s = interval_s
        self._devices: dict[UUID, _Device] = {}
        self._totals = CounterTotals()
        self._ticks = 0

    # ── Public ───────────────────────────────────────────────────────────

    def devices(self) -> list[str]:
        """The names of the devices being streamed."""
        return sorted(device.name for device in self._devices.values())

    def stream_state(self, name: str) -> StreamState:
        return next(device.state for device in self._devices.values() if device.name == name)

    async def run(self) -> None:
        """Start, tick every interval until cancelled, then stop cleanly."""
        await self.start()
        try:
            while True:
                await self._sleep(self._interval_s)
                await self.tick()
        finally:
            await self.stop()

    async def start(self) -> None:
        """Read intent and start a stream per device."""
        await self._refresh()

    async def tick(self) -> None:
        """Evaluate every device's health; every sixth tick, re-read intent."""
        self._ticks += 1
        for device in list(self._devices.values()):
            await self._evaluate(device)
        if self._ticks % REFRESH_TICKS == 0:
            await self._refresh()

    async def stop(self) -> None:
        """Stop every stream, and record ``unknown`` for each device whose last health was something else."""
        devices = list(self._devices.values())
        self._devices.clear()
        await self._cancel([device.task for device in devices if device.task is not None])
        now = self._clock()
        for device in devices:
            if device.recorded is not None and device.recorded.state is not DeviceHealthState.UNKNOWN:
                await self._record(device, DeviceHealthState.UNKNOWN, (HealthReason.telemetry_stopped(),), now)

    # ── Intent ───────────────────────────────────────────────────────────

    async def _refresh(self) -> None:
        try:
            wanted = _from_intent(await self._intent_store.get_desired_state())
        except Exception as exc:
            logger.warning(
                "telemetry: could not read intent, keeping %d stream(s) until the next refresh: %s",
                len(self._devices),
                exc,
            )
            return
        for device_id in [d for d in self._devices if d not in wanted]:
            gone = self._devices.pop(device_id)
            logger.info("telemetry: %s left intent, stopping its stream", gone.name)
            await self._cancel([gone.task] if gone.task else [])
        for device_id, (target, expectation) in wanted.items():
            device = self._devices.get(device_id)
            if device is None:
                device = _Device(target=target, expectation=expectation)
                device.recorded = await self._last_recorded(device_id, target.device.name)
                self._devices[device_id] = device
                self._start_stream(device)
            elif device.target != target:
                logger.info("telemetry: intent changed for %s, restarting its stream", device.name)
                await self._cancel([device.task] if device.task else [])
                device.target = target
                device.expectation = expectation
                device.interfaces = {n: o for n, o in device.interfaces.items() if n in target.interfaces}
                device.sessions = {p: o for p, o in device.sessions.items() if p in target.peers}
                self._start_stream(device)
            else:
                device.expectation = expectation

    async def _last_recorded(self, device_id: UUID, name: str) -> _Recorded | None:
        """The device's last ``health_changed``, so a restart does not write a change that did not happen."""
        try:
            events = await self._audit_log.query_by_device(device_id)
        except Exception as exc:
            logger.warning("telemetry: could not read %s's last health: %s", name, exc)
            return None
        for event in reversed(events):
            if event.event_type is AuditEventType.HEALTH_CHANGED:
                texts = tuple(str(r.get("text", "")) for r in event.payload.get("reasons") or [])
                return _Recorded(state=DeviceHealthState(event.payload["after"]), texts=texts)
        return None

    # ── Streams ──────────────────────────────────────────────────────────

    def _start_stream(self, device: _Device) -> None:
        device.state = StreamState.CONNECTING
        device.task = asyncio.create_task(self._stream(device), name=f"snapl-telemetry-{device.name}")

    async def _cancel(self, tasks: list[asyncio.Task[None]]) -> None:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _stream(self, device: _Device) -> None:
        backoff = _BACKOFF_FIRST_S
        while True:
            device.state = StreamState.CONNECTING
            self._totals.reset(device.name)
            try:
                async for sample in self._collector.stream_telemetry(device.target, interval_s=self._interval_s):
                    if sample.sync and device.state is not StreamState.STREAMING:
                        device.state = StreamState.STREAMING
                        backoff = _BACKOFF_FIRST_S
                    self._on_sample(device, sample)
                reason = "the stream ended"
            except Exception as exc:
                reason = str(exc) or type(exc).__name__
            device.state = StreamState.BROKEN
            logger.warning("telemetry from %s broke (%s); retrying in %d s", device.name, reason, backoff)
            await self._sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX_S)

    def _on_sample(self, device: _Device, sample: TelemetrySample) -> None:
        now = self._clock()
        device.last_sample_at = now
        intent_device = device.target.device
        for interface in sample.interfaces:
            record_interface(self._meter, intent_device, interface, self._totals)
            if interface.oper_up is not None:
                _observe(device.interfaces, interface.name, "up" if interface.oper_up else "down", now)
        for session in sample.sessions:
            record_session(self._meter, intent_device, session)
            if session.state is not None:
                _observe(device.sessions, session.peer, session.state, now)
        if sample.platform is not None:
            record_platform(self._meter, intent_device, sample.platform)

    # ── Health ───────────────────────────────────────────────────────────

    async def _evaluate(self, device: _Device) -> None:
        now = self._clock()
        observation = HealthObservation(
            last_sample_at=device.last_sample_at,
            interfaces=dict(device.interfaces),
            sessions=dict(device.sessions),
        )
        try:
            health = self._observer.evaluate_health(
                device.expectation, observation, now=now, interval_s=self._interval_s
            )
        except Exception as exc:
            logger.warning("telemetry: could not evaluate %s's health: %s", device.name, exc)
            return
        texts = tuple(reason.text for reason in health.reasons)
        if device.recorded == _Recorded(state=health.state, texts=texts):
            return
        await self._record(device, health.state, health.reasons, now)

    async def _record(
        self,
        device: _Device,
        state: DeviceHealthState,
        reasons: tuple[HealthReason, ...],
        now: datetime,
    ) -> None:
        before = device.recorded.state if device.recorded is not None else DeviceHealthState.UNKNOWN
        event = AuditEvent(
            event_id=uuid4(),
            workflow_id=f"telemetry:{device.name}",
            workflow_type=WORKFLOW_TYPE,
            target_id=device.target.device.id,
            event_type=AuditEventType.HEALTH_CHANGED,
            payload={
                "before": before.value,
                "after": state.value,
                "reasons": [reason.model_dump(mode="json") for reason in reasons],
                "last_sample_at": device.last_sample_at.isoformat() if device.last_sample_at else None,
            },
            timestamp=now,
            actor=ACTOR,
        )
        try:
            await self._audit_log.append(event)
        except Exception as exc:
            logger.warning("telemetry: could not record %s's health (%s): %s", device.name, state.value, exc)
            return
        device.recorded = _Recorded(state=state, texts=tuple(reason.text for reason in reasons))
        logger.info("telemetry: %s is %s (was %s)", device.name, state.value, before.value)
