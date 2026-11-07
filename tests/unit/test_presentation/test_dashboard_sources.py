"""The dashboard's data sources (spec 010 T015, T016, research R3 and R13).

Each source turns one dependency into a ``View``: data plus whether the
dependency answered and how old the data is. A dependency that fails is a view
with a reason and a remedy, never an exception.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from snapl_intent.exceptions import IntentError
from snapl_intent.infrahub.identity import IdentityCheck, SotIdentity
from snapl_intent.models import DesiredState, Device
from snapl_orchestrator.audit.abc import AuditLog
from snapl_orchestrator.models import AuditEvent, AuditEventType
from snapl_presentation.dashboard.models import SourceName
from snapl_presentation.dashboard.sources import AuditSource, Cached, SotSource
from snapl_presentation.settings import CliSettings

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 7, 21, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def _event(at: datetime) -> AuditEvent:
    return AuditEvent(
        event_id=uuid4(),
        workflow_id="scan-drift-dcfabric-1",
        workflow_type="ScanDrift",
        target_id="dcfabric",
        event_type=AuditEventType.WORKFLOW_STARTED,
        timestamp=at,
    )


class FakeLog(AuditLog):
    """An in-memory trail that records every range asked for, and refuses writes."""

    def __init__(self, events: list[AuditEvent] | None = None) -> None:
        self.events = list(events or [])
        self.ranges: list[tuple[datetime, datetime]] = []
        self.fail = False

    async def append(self, event: AuditEvent) -> None:
        raise AssertionError("the dashboard must never write to the audit trail")

    async def query_by_workflow(self, workflow_id: str) -> list[AuditEvent]:
        raise AssertionError("the dashboard reads through query_by_time_range only")

    async def query_by_device(self, device_id: UUID) -> list[AuditEvent]:
        raise AssertionError("the dashboard reads through query_by_time_range only")

    async def query_by_time_range(self, start: datetime, end: datetime) -> list[AuditEvent]:
        self.ranges.append((start, end))
        if self.fail:
            raise OSError("disk I/O error")
        return [event for event in self.events if start <= event.timestamp < end]


def _audit_source(log: FakeLog, clock: Clock) -> AuditSource:
    async def open_log(path: str) -> AuditLog:
        return log

    return AuditSource("/var/snapl/audit.db", open_log=open_log, clock=clock)


# --- AuditSource (T015) --------------------------------------------------------


async def test_first_refresh_reads_the_whole_trail():
    events = [_event(T0 - timedelta(days=200)), _event(T0 - timedelta(minutes=1))]
    log = FakeLog(events)

    view = await _audit_source(log, Clock()).refresh()

    assert view.source is SourceName.AUDIT
    assert view.available is True
    assert view.fetched_at == T0
    assert view.data == events
    start, end = log.ranges[0]
    assert start <= T0 - timedelta(days=200)
    assert end > T0


async def test_next_refresh_reads_a_trailing_window_and_drops_duplicates():
    newest = T0 - timedelta(minutes=1)
    log = FakeLog([_event(T0 - timedelta(hours=2)), _event(newest)])
    source = _audit_source(log, Clock())
    await source.refresh()

    view = await source.refresh()

    assert log.ranges[1][0] == newest - timedelta(minutes=5)
    assert view.data is not None
    assert len(view.data) == 2
    assert len({event.event_id for event in view.data}) == 2


async def test_late_row_inside_the_window_is_seen_on_the_next_refresh():
    newest = T0 - timedelta(seconds=10)
    log = FakeLog([_event(newest)])
    source = _audit_source(log, Clock())
    await source.refresh()
    late = _event(newest - timedelta(seconds=30))
    log.events.append(late)

    view = await source.refresh()

    assert view.data is not None
    assert late in view.data


async def test_very_late_row_appears_at_the_next_full_rebuild():
    newest = T0 - timedelta(seconds=10)
    log = FakeLog([_event(newest)])
    clock = Clock()
    source = _audit_source(log, clock)
    await source.refresh()
    very_late = _event(newest - timedelta(hours=1))
    log.events.append(very_late)

    before = await source.refresh()
    clock.advance(minutes=15)
    after = await source.refresh()

    assert before.data is not None
    assert very_late not in before.data
    assert after.data is not None
    assert very_late in after.data


async def test_failed_rebuild_keeps_the_current_read_model():
    events = [_event(T0 - timedelta(minutes=1))]
    log = FakeLog(events)
    clock = Clock()
    source = _audit_source(log, clock)
    await source.refresh()
    clock.advance(minutes=15)
    log.fail = True

    failed = await source.refresh()
    log.fail = False
    recovered = await source.refresh()

    assert failed.available is False
    assert failed.stale is True
    assert failed.data == events
    assert failed.fetched_at == T0
    assert failed.reason is not None
    assert "disk I/O error" in failed.reason
    assert recovered.available is True
    assert recovered.data == events


async def test_events_are_ordered_by_timestamp_not_arrival():
    first = _event(T0 - timedelta(minutes=3))
    second = _event(T0 - timedelta(minutes=2))
    third = _event(T0 - timedelta(minutes=1))
    log = FakeLog([third, first])
    source = _audit_source(log, Clock())
    await source.refresh()
    log.events.append(second)

    view = await source.refresh()

    assert view.data == [first, second, third]


async def test_missing_audit_file_is_unavailable_and_never_created(tmp_path: Path):
    path = tmp_path / "audit.db"

    view = await AuditSource(str(path), clock=Clock()).refresh()

    assert view.available is False
    assert view.data is None
    assert view.reason is not None
    assert str(path) in view.reason
    assert view.remedy is not None
    assert view.remedy.startswith("Set SNAPL_AUDIT_DB")
    assert not path.exists()


async def test_opens_a_real_trail_read_only(tmp_path: Path):
    from snapl_orchestrator.audit.sqlite import SqliteAuditLog

    path = tmp_path / "audit.db"
    writer = SqliteAuditLog(database_url=str(path))
    await writer.initialize()
    event = _event(T0)
    await writer.append(event)
    await writer.close()

    view = await AuditSource(str(path), clock=Clock()).refresh()

    assert view.available is True
    assert view.data == [event]


# --- Cached (T020's helper) ----------------------------------------------------


async def test_cached_serves_one_load_per_ttl():
    clock = Clock()
    calls = 0

    async def load() -> int:
        nonlocal calls
        calls += 1
        return calls

    cached = Cached(SourceName.SOT, load, ttl=timedelta(seconds=30), clock=clock, remedy="uv run invoke dev.deps")

    first = await cached.get()
    clock.advance(seconds=29)
    second = await cached.get()
    clock.advance(seconds=1)
    third = await cached.get()

    assert (first.data, second.data, third.data) == (1, 1, 2)
    assert third.fetched_at == T0 + timedelta(seconds=30)


async def test_cached_is_single_flight():
    import asyncio

    gate = asyncio.Event()
    calls = 0

    async def load() -> int:
        nonlocal calls
        calls += 1
        await gate.wait()
        return calls

    cached = Cached(SourceName.SOT, load, ttl=timedelta(seconds=30), clock=Clock(), remedy="r")
    tasks = [asyncio.create_task(cached.get()) for _ in range(5)]
    await asyncio.sleep(0)
    gate.set()
    views = await asyncio.gather(*tasks)

    assert calls == 1
    assert {view.data for view in views} == {1}


# --- SotSource (T016) ----------------------------------------------------------


def _desired(name: str, role: str) -> DesiredState:
    return DesiredState(
        device=Device(id=uuid4(), name=name, management_address="10.0.0.1", role=role, use_case="dcfabric")
    )


class FakeStore:
    def __init__(self, states: list[DesiredState]) -> None:
        self.states = states
        self.calls: list[str | None] = []
        self.error: Exception | None = None
        self.client = MagicMock()

    async def get_desired_state(self, use_case: str | None = None) -> list[DesiredState]:
        self.calls.append(use_case)
        if self.error is not None:
            raise self.error
        return self.states


def _sot_source(store: FakeStore, clock: Clock, identity: IdentityCheck | None = None) -> SotSource:
    verdict = identity or IdentityCheck(SotIdentity.OURS, "carries snapl's schema")

    async def identify(store, *, address: str) -> IdentityCheck:
        return verdict

    return SotSource(
        CliSettings(),
        "dcfabric",
        build_store=lambda _settings: store,
        identify=identify,
        clock=clock,
    )


async def test_sot_devices_and_roles_come_from_desired_state():
    states = [_desired("spine-01", "spine"), _desired("leaf-01", "leaf")]
    store = FakeStore(states)
    source = _sot_source(store, Clock())

    view = await source.refresh()

    assert view.source is SourceName.SOT
    assert view.available is True
    assert view.data == states
    assert store.calls == ["dcfabric"]
    assert source.names == {state.device.id: state.device.name for state in states}


async def test_sot_is_cached_for_30_seconds():
    store = FakeStore([_desired("spine-01", "spine")])
    clock = Clock()
    source = _sot_source(store, clock)

    await source.refresh()
    clock.advance(seconds=29)
    await source.refresh()
    clock.advance(seconds=1)
    await source.refresh()

    assert len(store.calls) == 2


async def test_foreign_sot_is_unavailable_with_the_identity_detail():
    detail = "http://localhost:8000 has 40 devices but no snapl 'use_case' attribute"
    store = FakeStore([_desired("spine-01", "spine")])

    view = await _sot_source(store, Clock(), IdentityCheck(SotIdentity.FOREIGN, detail)).refresh()

    assert view.available is False
    assert view.reason == detail
    assert view.remedy is not None
    assert view.data is None
    assert store.calls == []


async def test_unidentifiable_sot_is_not_refused():
    """Only a positive "this is another project's" refuses (#107)."""
    store = FakeStore([_desired("spine-01", "spine")])

    view = await _sot_source(store, Clock(), IdentityCheck(SotIdentity.UNKNOWN, "could not be identified")).refresh()

    assert view.available is True


async def test_sot_error_keeps_the_last_good_data_as_stale():
    states = [_desired("spine-01", "spine")]
    store = FakeStore(states)
    clock = Clock()
    source = _sot_source(store, clock)
    await source.refresh()
    store.error = IntentError("connection refused")
    clock.advance(seconds=30)

    view = await source.refresh()

    assert view.available is False
    assert view.remedy == "uv run invoke dev.deps"
    assert view.reason is not None
    assert "connection refused" in view.reason
    assert view.stale is True
    assert view.data == states
    assert view.fetched_at == T0
    assert source.names == {states[0].device.id: "spine-01"}


async def test_sot_error_with_no_earlier_data():
    store = FakeStore([])
    store.error = IntentError("connection refused")

    view = await _sot_source(store, Clock()).refresh()

    assert view.available is False
    assert view.stale is False
    assert view.data is None
    assert view.remedy == "uv run invoke dev.deps"
