"""The worker's telemetry runner (spec 016 T021, data-model.md "The runner", research R3, R5 to R7).

Time is driven by the test: the runner reads a fake clock, and its backoff
sleeps go through a fake that records each delay. The collector's streams are
fed by the test. No device is dialled and nothing sleeps for real.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from snapl_collector.exceptions import TelemetryStreamError
from snapl_collector.models import BgpSessionSample, InterfaceSample, PlatformSample, TelemetrySample, TelemetryTarget
from snapl_intent.models import BGPSession, DesiredState, Device, Interface
from snapl_observability.structural.observer import StructuralObserver
from snapl_orchestrator.audit.memory import InMemoryAuditLog
from snapl_orchestrator.models import AuditEvent, AuditEventType
from tests.unit.test_orchestrator.metric_fakes import FakeMeter

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 11, 19, 22, 0, 0, tzinfo=UTC)
_LEAF_ID = UUID("00000000-0000-0000-0000-0000000000a1")
_SPINE_ID = UUID("00000000-0000-0000-0000-0000000000b1")

_END = object()


# ── Fakes ────────────────────────────────────────────────────────────────


class FakeClock:
    def __init__(self) -> None:
        self.now = _T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeSleep:
    """Records each backoff delay and returns at once."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        await asyncio.sleep(0)


class FakeStream:
    def __init__(self, target: TelemetryTarget) -> None:
        self.target = target
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self.closed = False


class FakeCollector:
    """Each ``stream_telemetry`` call opens a stream the test feeds.

    ``fail_next[name]`` holds exceptions: each call for that device raises the
    next one before yielding anything, until the list is empty.
    """

    def __init__(self) -> None:
        self.streams: dict[str, list[FakeStream]] = {}
        self.fail_next: dict[str, list[BaseException]] = {}

    async def stream_telemetry(self, target: TelemetryTarget, *, interval_s: int):
        assert interval_s == 10
        stream = FakeStream(target)
        self.streams.setdefault(target.device.name, []).append(stream)
        try:
            failures = self.fail_next.get(target.device.name)
            if failures:
                raise failures.pop(0)
            while True:
                item = await stream.queue.get()
                if item is _END:
                    return
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            stream.closed = True

    def latest(self, name: str) -> FakeStream:
        return self.streams[name][-1]

    def open_streams(self) -> dict[str, int]:
        return {name: sum(not s.closed for s in streams) for name, streams in self.streams.items()}


class FakeIntentStore:
    def __init__(self, states: list[DesiredState]) -> None:
        self.states = states
        self.error: Exception | None = None
        self.calls = 0
        self.filters: list[dict[str, Any]] = []

    async def get_desired_state(self, **filters: Any) -> list[DesiredState]:
        self.calls += 1
        self.filters.append(filters)
        if self.error is not None:
            raise self.error
        use_case = filters.get("use_case")
        return [s for s in self.states if use_case is None or s.device.use_case == use_case]


# ── Builders ─────────────────────────────────────────────────────────────


def _device(name: str, device_id: UUID, *, address: str = "10.0.0.1") -> Device:
    return Device(
        id=device_id,
        name=name,
        management_address=address,
        role="leaf" if name.startswith("leaf") else "spine",
        use_case="dcfabric",
        lab_node_name=f"clab-dcfabric-{name}",
    )


def _state(device: Device, interfaces: dict[str, bool], sessions: dict[str, bool] | None = None) -> DesiredState:
    return DesiredState(
        device=device,
        interfaces=[Interface(id=uuid4(), device_id=device.id, name=n, enabled=e) for n, e in interfaces.items()],
        bgp_sessions=[
            BGPSession(id=uuid4(), device_id=device.id, local_asn=65001, peer_address=p, peer_asn=65000, enabled=e)
            for p, e in (sessions or {}).items()
        ],
    )


_LEAF = _device("leaf-01", _LEAF_ID)
_SPINE = _device("spine-01", _SPINE_ID)


def _intent() -> list[DesiredState]:
    return [
        _state(_LEAF, {"ethernet-1/49": True, "lo0": True}, {"10.10.1.0": True}),
        _state(_SPINE, {"ethernet-1/1": True}, {"10.10.1.1": True}),
    ]


def _sample(
    name: str, *, sync: bool = False, up: dict[str, bool] | None = None, sessions=None, **kw
) -> TelemetrySample:
    return TelemetrySample(
        device_name=name,
        timestamp=_T0,
        interfaces=tuple(InterfaceSample(name=n, oper_up=v, **kw) for n, v in (up or {}).items()),
        sessions=tuple(BgpSessionSample(peer=p, state=s) for p, s in (sessions or {}).items()),
        sync=sync,
    )


def _healthy_leaf(sync: bool = False) -> TelemetrySample:
    return _sample("leaf-01", sync=sync, up={"ethernet-1/49": True, "lo0": True}, sessions={"10.10.1.0": "established"})


def _healthy_spine(sync: bool = False) -> TelemetrySample:
    return _sample("spine-01", sync=sync, up={"ethernet-1/1": True}, sessions={"10.10.1.1": "established"})


async def _settle() -> None:
    for _ in range(60):
        await asyncio.sleep(0)


class Harness:
    def __init__(self, states: list[DesiredState] | None = None, **runner_kwargs: Any) -> None:
        from snapl_orchestrator.telemetry import TelemetryRunner

        self.clock = FakeClock()
        self.sleep = FakeSleep()
        self.collector = FakeCollector()
        self.store = FakeIntentStore(_intent() if states is None else states)
        self.audit = InMemoryAuditLog()
        self.meter = FakeMeter()
        self.runner = TelemetryRunner(
            intent_store=self.store,  # type: ignore[arg-type]
            collector=self.collector,  # type: ignore[arg-type]
            observer=StructuralObserver(),
            audit_log=self.audit,
            meter=self.meter,  # type: ignore[arg-type]
            clock=self.clock,
            sleep=self.sleep,
            **runner_kwargs,
        )

    async def start(self) -> None:
        await self.runner.start()
        await _settle()

    async def push(self, name: str, item: Any) -> None:
        await self.collector.latest(name).queue.put(item)
        await _settle()

    async def health_events(self, name: str) -> list[AuditEvent]:
        events = await self.audit.query_by_workflow(f"telemetry:{name}")
        return [e for e in events if e.event_type is AuditEventType.HEALTH_CHANGED]


@pytest.fixture
async def harness():
    h = Harness()
    yield h
    await h.runner.stop()


# ── Streams ──────────────────────────────────────────────────────────────


class TestStreams:
    async def test_one_stream_per_device_with_the_target_from_intent(self, harness: Harness) -> None:
        await harness.start()

        assert harness.collector.open_streams() == {"leaf-01": 1, "spine-01": 1}
        assert harness.collector.latest("leaf-01").target == TelemetryTarget(
            device=_LEAF, interfaces=("ethernet-1/49", "lo0"), peers=("10.10.1.0",)
        )
        assert harness.collector.latest("spine-01").target == TelemetryTarget(
            device=_SPINE, interfaces=("ethernet-1/1",), peers=("10.10.1.1",)
        )

    async def test_streams_only_the_use_case_it_was_given(self) -> None:
        # #341: the lab's Infrahub also holds the integration tests' test_edge devices.
        edge = _device("edge-01", uuid4(), address="10.200.0.1").model_copy(
            update={"use_case": "test_edge", "lab_node_name": None}
        )
        h = Harness([*_intent(), _state(edge, {"ethernet-1/1": True})], use_case="dcfabric")
        await h.start()
        try:
            for _ in range(6):
                h.clock.advance(10)
                await h.runner.tick()
            await _settle()

            assert h.collector.open_streams() == {"leaf-01": 1, "spine-01": 1}
            assert h.store.filters == [{"use_case": "dcfabric"}, {"use_case": "dcfabric"}]
        finally:
            await h.runner.stop()

    async def test_without_a_use_case_it_streams_every_device_in_intent(self) -> None:
        edge = _device("edge-01", uuid4(), address="10.200.0.1").model_copy(update={"use_case": "test_edge"})
        h = Harness([*_intent(), _state(edge, {"ethernet-1/1": True})])
        await h.start()
        try:
            assert h.collector.open_streams() == {"edge-01": 1, "leaf-01": 1, "spine-01": 1}
            assert h.store.filters == [{"use_case": None}]
        finally:
            await h.runner.stop()

    async def test_a_device_in_two_services_gets_one_stream_naming_both(self) -> None:
        h = Harness([_state(_LEAF, {"ethernet-1/49": True}), _state(_LEAF, {"lo0": True}, {"10.10.1.0": True})])
        await h.start()
        try:
            assert h.collector.open_streams() == {"leaf-01": 1}
            assert h.collector.latest("leaf-01").target.interfaces == ("ethernet-1/49", "lo0")
            assert h.collector.latest("leaf-01").target.peers == ("10.10.1.0",)
        finally:
            await h.runner.stop()

    async def test_a_sync_sample_moves_the_stream_to_streaming(self, harness: Harness) -> None:
        from snapl_orchestrator.telemetry import StreamState

        await harness.start()
        assert harness.runner.stream_state("leaf-01") is StreamState.CONNECTING

        await harness.push("leaf-01", _healthy_leaf(sync=True))

        assert harness.runner.stream_state("leaf-01") is StreamState.STREAMING

    async def test_a_sample_records_the_figures(self, harness: Harness) -> None:
        await harness.start()
        sample = TelemetrySample(
            device_name="leaf-01",
            timestamp=_T0,
            interfaces=(InterfaceSample(name="ethernet-1/49", oper_up=True, in_octets=100),),
            sessions=(BgpSessionSample(peer="10.10.1.0", state="established", prefixes_received=3),),
            platform=PlatformSample(cpu_percent=5, memory_percent=94),
            sync=True,
        )
        await harness.push("leaf-01", sample)

        names = {name for name, _, _ in harness.meter.sets()}
        assert names == {
            "snapl_interface_up",
            "snapl_bgp_session_established",
            "snapl_bgp_prefixes_received",
            "snapl_device_cpu_percent",
            "snapl_device_memory_percent",
        }
        [(_, value, labels)] = harness.meter.adds("snapl_interface_octets_total")
        assert value == 0  # the first total adds 0 (R5)
        assert labels["device"] == "leaf-01"
        assert labels["interface"] == "ethernet-1/49"

    async def test_a_stream_that_raises_retries_after_a_doubling_backoff(self, harness: Harness) -> None:
        from snapl_orchestrator.telemetry import StreamState

        harness.collector.fail_next["leaf-01"] = [TelemetryStreamError("UNAVAILABLE") for _ in range(7)]
        await harness.start()

        assert harness.sleep.delays == [1, 2, 4, 8, 16, 30, 30]
        assert len(harness.collector.streams["leaf-01"]) == 8
        assert harness.runner.stream_state("leaf-01") is StreamState.CONNECTING

    async def test_streaming_resets_the_backoff(self, harness: Harness) -> None:
        from snapl_orchestrator.telemetry import StreamState

        harness.collector.fail_next["leaf-01"] = [TelemetryStreamError("x"), TelemetryStreamError("x")]
        await harness.start()
        assert harness.sleep.delays == [1, 2]

        await harness.push("leaf-01", _healthy_leaf(sync=True))
        assert harness.runner.stream_state("leaf-01") is StreamState.STREAMING
        await harness.push("leaf-01", TelemetryStreamError("socket closed"))

        assert harness.sleep.delays == [1, 2, 1]
        assert harness.collector.open_streams()["leaf-01"] == 1

    async def test_a_stream_that_ends_is_broken_too(self, harness: Harness) -> None:
        await harness.start()
        await harness.push("leaf-01", _healthy_leaf(sync=True))
        await harness.push("leaf-01", _END)

        assert harness.sleep.delays == [1]
        assert len(harness.collector.streams["leaf-01"]) == 2

    async def test_a_broken_stream_does_not_touch_another_device(self, harness: Harness) -> None:
        """FR-003."""
        await harness.start()
        await harness.push("spine-01", _healthy_spine(sync=True))
        first_spine = harness.collector.latest("spine-01")

        await harness.push("leaf-01", TelemetryStreamError("UNAVAILABLE"))
        await harness.push("spine-01", _healthy_spine())

        assert harness.collector.latest("spine-01") is first_spine
        assert not first_spine.closed
        assert first_spine.queue.empty()

    async def test_a_restarted_stream_starts_its_totals_again(self, harness: Harness) -> None:
        await harness.start()
        await harness.push("leaf-01", _sample("leaf-01", sync=True, up={"ethernet-1/49": True}, in_octets=100))
        await harness.push("leaf-01", _sample("leaf-01", up={"ethernet-1/49": True}, in_octets=150))
        await harness.push("leaf-01", TelemetryStreamError("x"))
        await harness.push("leaf-01", _sample("leaf-01", sync=True, up={"ethernet-1/49": True}, in_octets=9000))

        adds = [value for _, value, labels in harness.meter.adds("snapl_interface_octets_total")]
        assert adds == [0, 50, 0]


# ── Health ───────────────────────────────────────────────────────────────


class TestHealth:
    async def test_a_change_writes_one_event_with_the_payload(self, harness: Harness) -> None:
        await harness.start()
        await harness.push(
            "spine-01", _sample("spine-01", sync=True, up={"ethernet-1/1": True}, sessions={"10.10.1.1": "active"})
        )
        harness.clock.advance(11)
        await harness.push("spine-01", _sample("spine-01", sessions={"10.10.1.1": "active"}))
        await harness.runner.tick()

        [event] = await harness.health_events("spine-01")
        assert event.workflow_id == "telemetry:spine-01"
        assert event.workflow_type == "DeviceTelemetry"
        assert event.target_id == _SPINE_ID
        assert event.actor == "worker"
        assert event.timestamp == harness.clock.now
        assert event.payload == {
            "before": "unknown",  # nothing recorded yet reads as unknown
            "after": "unhealthy",
            "reasons": [
                {
                    "kind": "bgp_session",
                    "item": "10.10.1.1",
                    "expected": "established",
                    "actual": "active",
                    "text": "BGP 10.10.1.1 active (expected established)",
                }
            ],
            "last_sample_at": harness.clock.now.isoformat(),
        }

    async def test_no_change_writes_nothing(self, harness: Harness) -> None:
        await harness.start()
        await harness.push("leaf-01", _healthy_leaf(sync=True))
        harness.clock.advance(10)
        await harness.runner.tick()
        harness.clock.advance(5)
        await harness.push("leaf-01", _healthy_leaf())
        harness.clock.advance(5)
        await harness.runner.tick()

        [event] = await harness.health_events("leaf-01")
        assert event.payload["after"] == "healthy"
        assert event.payload["reasons"] == []

    async def test_since_holds_while_the_state_holds(self, harness: Harness) -> None:
        """An item's ``since`` moves only when its state changes, so the debounce counts from the change."""
        await harness.start()
        await harness.push("leaf-01", _healthy_leaf(sync=True))
        harness.clock.advance(10)
        await harness.push("leaf-01", _sample("leaf-01", up={"ethernet-1/49": False}))
        harness.clock.advance(6)
        await harness.push("leaf-01", _sample("leaf-01", up={"ethernet-1/49": False}))
        await harness.runner.tick()  # down for 6 s: debounced
        assert [e.payload["after"] for e in await harness.health_events("leaf-01")] == ["healthy"]

        harness.clock.advance(5)
        await harness.push("leaf-01", _sample("leaf-01", up={"ethernet-1/49": False}))
        await harness.runner.tick()  # down for 11 s

        events = await harness.health_events("leaf-01")
        assert [e.payload["after"] for e in events] == ["healthy", "unhealthy"]
        assert events[-1].payload["before"] == "healthy"
        assert [r["text"] for r in events[-1].payload["reasons"]] == ["ethernet-1/49 down (expected up)"]

    async def test_a_silent_device_turns_unknown(self, harness: Harness) -> None:
        await harness.start()
        await harness.push("leaf-01", _healthy_leaf(sync=True))
        await harness.runner.tick()
        harness.clock.advance(31)
        await harness.runner.tick()

        events = await harness.health_events("leaf-01")
        assert [e.payload["after"] for e in events] == ["healthy", "unknown"]
        assert events[-1].payload["reasons"][0]["text"] == "no telemetry since 2026-11-19T22:00:00+00:00"
        assert events[-1].payload["last_sample_at"] == _T0.isoformat()

    async def test_the_expectation_follows_intent(self) -> None:
        """A disabled interface is expected down; a disabled session is not expected at all."""
        h = Harness([_state(_LEAF, {"ethernet-1/49": True, "ethernet-1/50": False}, {"10.10.1.0": False})])
        await h.start()
        try:
            await h.push(
                "leaf-01",
                _sample(
                    "leaf-01",
                    sync=True,
                    up={"ethernet-1/49": True, "ethernet-1/50": True},
                    sessions={"10.10.1.0": "idle"},
                ),
            )
            h.clock.advance(20)
            await h.push("leaf-01", _sample("leaf-01", up={"ethernet-1/49": True}))
            await h.runner.tick()

            [event] = await h.health_events("leaf-01")
            assert event.payload["after"] == "healthy"
        finally:
            await h.runner.stop()

    async def test_the_last_recorded_health_is_read_back_at_start(self, harness: Harness) -> None:
        await harness.audit.append(
            AuditEvent(
                event_id=uuid4(),
                workflow_id="telemetry:leaf-01",
                workflow_type="DeviceTelemetry",
                target_id=_LEAF_ID,
                event_type=AuditEventType.HEALTH_CHANGED,
                payload={"before": "unknown", "after": "healthy", "reasons": [], "last_sample_at": None},
                timestamp=_T0 - timedelta(hours=1),
                actor="worker",
            )
        )
        await harness.start()
        await harness.push("leaf-01", _healthy_leaf(sync=True))
        await harness.runner.tick()

        assert len(await harness.health_events("leaf-01")) == 1  # only the one written before start

    async def test_a_health_rule_that_fails_is_logged_and_the_tick_goes_on(self, harness: Harness, caplog) -> None:
        await harness.start()
        await harness.push("spine-01", _healthy_spine(sync=True))

        calls = {"n": 0}
        real = harness.runner._observer.evaluate_health

        def _flaky(expectation, observation, *, now, interval_s):
            calls["n"] += 1
            if expectation.device_name == "leaf-01":
                raise RuntimeError("boom")
            return real(expectation, observation, now=now, interval_s=interval_s)

        harness.runner._observer.evaluate_health = _flaky  # type: ignore[method-assign]
        with caplog.at_level("WARNING", logger="snapl_orchestrator.telemetry.runner"):
            await harness.runner.tick()

        assert calls["n"] == 2
        assert [e.payload["after"] for e in await harness.health_events("spine-01")] == ["healthy"]
        assert any("leaf-01" in r.getMessage() for r in caplog.records)


# ── Lab captures (spec 016 T057) ─────────────────────────────────────────

_CAPTURES = Path(__file__).parents[1] / "test_collector" / "fixtures" / "telemetry"


async def _parsed_capture(name: str, target: TelemetryTarget) -> list[tuple[float, TelemetrySample]]:
    """The capture read by the real collector, each sample with its seconds since the subscription began."""
    from snapl_collector.gnmi.collector import GnmiCollector

    entries = json.loads((_CAPTURES / name).read_text())

    async def _replay(**_kwargs):
        for entry in entries:
            yield entry["msg"]

    collector = GnmiCollector(host="127.0.0.1", password="test")  # pragma: allowlist secret
    with patch("snapl_collector.gnmi.collector.subscribe_sample", _replay):
        samples = [s async for s in collector.stream_telemetry(target, interval_s=10)]
    assert len(samples) == len(entries)
    return [(entry["t_rel"], sample) for entry, sample in zip(entries, samples, strict=True)]


async def _replay_through_runner(device: Device, state: DesiredState, capture: str) -> list[tuple[str, list[str]]]:
    """Feed a capture to the runner at its own pace, ticking at the end of each 10 s interval.

    Returns each recorded health change as (state, reason texts).
    """
    h = Harness([state])
    await h.start()
    try:
        target = h.collector.latest(device.name).target
        samples = await _parsed_capture(capture, target)
        ticked = 0
        for t_rel, sample in samples:
            while t_rel >= (ticked + 1) * 10:  # the interval before this sample ends: tick at its last moment
                ticked += 1
                h.clock.now = _T0 + timedelta(seconds=ticked * 10 - 0.01)
                await h.runner.tick()
            h.clock.now = _T0 + timedelta(seconds=t_rel)
            await h.push(device.name, sample)
        h.clock.now = _T0 + timedelta(seconds=(ticked + 1) * 10 - 0.01)
        await h.runner.tick()
        events = await h.health_events(device.name)
        return [(e.payload["after"], [r["text"] for r in e.payload["reasons"]]) for e in events]
    finally:
        await h.runner.stop()


class TestLabCaptures:
    """The fault captures from the lab, through the real collector and the runner (spec 016 T057)."""

    async def test_leaf01_flagged_while_its_uplink_is_down_and_cleared_when_bgp_returns(self) -> None:
        state = _state(_LEAF, {"ethernet-1/49": True}, {"10.10.1.0": True})
        changes = await _replay_through_runner(_LEAF, state, "srlinux_v26_3_1_leaf01_interface_down_up.json")
        assert changes == [
            ("healthy", []),
            ("unhealthy", ["ethernet-1/49 down (expected up)", "BGP 10.10.1.0 active (expected established)"]),
            ("unhealthy", ["BGP 10.10.1.0 active (expected established)"]),  # the link is back before the session
            ("healthy", []),
        ]

    async def test_spine01_flagged_once_the_paused_peers_hold_timer_runs_out(self) -> None:
        state = _state(_SPINE, {"ethernet-1/1": True}, {"10.10.1.1": True})
        changes = await _replay_through_runner(_SPINE, state, "srlinux_v26_3_1_spine01_peer_paused.json")
        assert changes == [
            ("healthy", []),
            ("unhealthy", ["BGP 10.10.1.1 connect (expected established)"]),
            ("healthy", []),
        ]


# ── Intent refresh ───────────────────────────────────────────────────────


class TestRefresh:
    async def _ticks(self, h: Harness, n: int) -> None:
        for _ in range(n):
            h.clock.advance(10)
            await h.runner.tick()
        await _settle()

    async def test_intent_is_reread_every_sixth_tick(self, harness: Harness) -> None:
        await harness.start()
        assert harness.store.calls == 1
        await self._ticks(harness, 5)
        assert harness.store.calls == 1
        await self._ticks(harness, 1)
        assert harness.store.calls == 2
        await self._ticks(harness, 6)
        assert harness.store.calls == 3

    async def test_a_changed_target_restarts_that_stream_only(self, harness: Harness) -> None:
        await harness.start()
        old_leaf = harness.collector.latest("leaf-01")
        old_spine = harness.collector.latest("spine-01")
        harness.store.states = [
            _state(_LEAF, {"ethernet-1/49": True, "ethernet-1/50": True, "lo0": True}, {"10.10.1.0": True}),
            _state(_SPINE, {"ethernet-1/1": True}, {"10.10.1.1": True}),
        ]
        await self._ticks(harness, 6)

        assert old_leaf.closed
        assert harness.collector.latest("leaf-01").target.interfaces == ("ethernet-1/49", "ethernet-1/50", "lo0")
        assert harness.collector.latest("spine-01") is old_spine
        assert not old_spine.closed

    async def test_a_removed_device_stops_and_a_new_one_starts(self, harness: Harness) -> None:
        await harness.start()
        leaf_02 = _device("leaf-02", uuid4())
        harness.store.states = [
            _state(_LEAF, {"ethernet-1/49": True, "lo0": True}, {"10.10.1.0": True}),
            _state(leaf_02, {"ethernet-1/49": True}),
        ]
        await self._ticks(harness, 6)

        assert harness.collector.open_streams() == {"leaf-01": 1, "spine-01": 0, "leaf-02": 1}
        assert harness.runner.devices() == ["leaf-01", "leaf-02"]

    async def test_a_failing_intent_store_keeps_the_streams_and_retries_at_the_next_refresh(
        self, harness: Harness, caplog
    ) -> None:
        await harness.start()
        harness.store.error = ConnectionError("infrahub down")
        with caplog.at_level("WARNING", logger="snapl_orchestrator.telemetry.runner"):
            await self._ticks(harness, 6)

        assert harness.collector.open_streams() == {"leaf-01": 1, "spine-01": 1}
        assert any("infrahub down" in r.getMessage() for r in caplog.records)

        harness.store.error = None
        harness.store.states = [_state(_LEAF, {"ethernet-1/49": True, "lo0": True}, {"10.10.1.0": True})]
        await self._ticks(harness, 6)
        assert harness.collector.open_streams() == {"leaf-01": 1, "spine-01": 0}

    async def test_a_failing_first_read_starts_nothing_and_retries(self) -> None:
        h = Harness()
        h.store.error = ConnectionError("infrahub down")
        await h.start()
        try:
            assert h.collector.streams == {}
            h.store.error = None
            await self._ticks(h, 6)
            assert h.collector.open_streams() == {"leaf-01": 1, "spine-01": 1}
        finally:
            await h.runner.stop()


# ── Stop and run ─────────────────────────────────────────────────────────


class TestStop:
    async def test_a_clean_stop_records_unknown_for_each_device_not_already_unknown(self) -> None:
        h = Harness()
        await h.start()
        await h.push("leaf-01", _healthy_leaf(sync=True))
        await h.runner.tick()  # leaf healthy, spine unknown (no telemetry yet)

        await h.runner.stop()

        leaf = await h.health_events("leaf-01")
        assert [(e.payload["before"], e.payload["after"]) for e in leaf] == [
            ("unknown", "healthy"),
            ("healthy", "unknown"),
        ]
        assert [r["text"] for r in leaf[-1].payload["reasons"]] == ["telemetry stopped"]
        assert [e.payload["after"] for e in await h.health_events("spine-01")] == ["unknown"]
        assert h.collector.open_streams() == {"leaf-01": 0, "spine-01": 0}

    async def test_stop_is_safe_twice(self) -> None:
        h = Harness()
        await h.start()
        await h.runner.stop()
        await h.runner.stop()
        assert h.collector.open_streams() == {"leaf-01": 0, "spine-01": 0}


class TestRun:
    async def test_run_ticks_every_interval_and_stops_cleanly_when_cancelled(self) -> None:
        from snapl_orchestrator.telemetry import SAMPLE_INTERVAL_S

        h = Harness()
        ticks: list[float] = []
        parked = asyncio.Event()

        async def _sleep(seconds: float) -> None:
            if seconds == SAMPLE_INTERVAL_S:
                ticks.append(seconds)
                if len(ticks) > 2:
                    parked.set()
                    await asyncio.Event().wait()
            await asyncio.sleep(0)

        h.runner._sleep = _sleep  # type: ignore[method-assign]
        task = asyncio.create_task(h.runner.run())
        await asyncio.wait_for(parked.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert SAMPLE_INTERVAL_S == 10
        assert len(ticks) == 3
        assert [e.payload["after"] for e in await h.health_events("leaf-01")] == ["unknown"]
        assert h.collector.open_streams() == {"leaf-01": 0, "spine-01": 0}
