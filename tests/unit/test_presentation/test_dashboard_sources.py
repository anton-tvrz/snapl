"""The dashboard's data sources (spec 010 T015, T016, research R3 and R13).

Each source turns one dependency into a ``View``: data plus whether the
dependency answered and how old the data is. A dependency that fails is a view
with a reason and a remedy, never an exception.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from temporalio.api.taskqueue.v1 import PollerInfo
from temporalio.api.workflowservice.v1 import DescribeTaskQueueResponse
from temporalio.service import RPCError, RPCStatusCode

from snapl_intent.exceptions import IntentError
from snapl_intent.infrahub.identity import IdentityCheck, SotIdentity
from snapl_intent.models import DesiredState, Device
from snapl_orchestrator.audit.abc import AuditLog
from snapl_orchestrator.models import AuditEvent, AuditEventType
from snapl_presentation.dashboard.models import SourceName
from snapl_presentation.dashboard.sources import AuditSource, Cached, SotSource, TemporalSource
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


# --- TemporalSource (T026, research R2 and R6) ---------------------------------


class StrictClient:
    """A Temporal client that answers only the calls the dashboard may make.

    Anything outside the read list fails the test: the dashboard never starts,
    signals, cancels or changes anything (research R2).
    """

    _READS = frozenset({"list_workflows", "service_client", "get_schedule_handle", "data_converter"})

    def __init__(self, running: list | None = None, pollers: int = 1) -> None:
        self._running = running or []
        self._pollers = pollers
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        raise AssertionError(f"the dashboard called Temporal's {name!r}, which is not a read")

    def list_workflows(self, query: str):
        self.calls.append("list_workflows")
        running = self._running

        async def _iterate():
            for item in running:
                yield item

        return _iterate()

    @property
    def service_client(self):
        self.calls.append("describe_task_queue")
        pollers = self._pollers

        async def describe_task_queue(request):
            return DescribeTaskQueueResponse(pollers=[_fresh_poller() for _ in range(pollers)])

        return SimpleNamespace(workflow_service=SimpleNamespace(describe_task_queue=describe_task_queue))

    def get_schedule_handle(self, schedule_id: str):
        self.calls.append("get_schedule_handle")

        class _Handle:
            async def describe(self):
                raise RPCError("schedule not found", RPCStatusCode.NOT_FOUND, b"")

            def __getattr__(self, name: str):
                raise AssertionError(f"the dashboard called the schedule's {name!r}, which is not a read")

        return _Handle()


def _execution(workflow_id: str, workflow_type: str = "ScanDrift"):
    return SimpleNamespace(id=workflow_id, workflow_type=workflow_type, start_time=T0, task_queue="snapl")


def _temporal_source(clock: Clock, *, client=None, probe_failure: str | None = None, **overrides) -> TemporalSource:
    connects: list[int] = []

    async def probe(host_port: str, timeout: float) -> str | None:
        return probe_failure

    async def connect(settings):
        connects.append(1)
        return client or StrictClient()

    source = TemporalSource(CliSettings(), "dcfabric", probe=probe, connect=connect, clock=clock, **overrides)
    source.connects = connects  # type: ignore[attr-defined]
    return source


def _fresh_poller() -> PollerInfo:
    poller = PollerInfo(identity="worker")
    poller.last_access_time.FromDatetime(datetime.now(UTC))
    return poller


async def test_temporal_reads_running_pollers_and_schedule_through_read_calls_only():
    client = StrictClient(running=[_execution("scan-drift-dcfabric-1")], pollers=2)

    view = await _temporal_source(Clock(), client=client).refresh()

    assert view.source is SourceName.TEMPORAL
    assert view.available is True
    assert view.data is not None
    assert [w.workflow_id for w in view.data.running] == ["scan-drift-dcfabric-1"]
    assert view.data.pollers == 2
    assert view.data.schedule is None
    assert set(client.calls) == {"list_workflows", "describe_task_queue", "get_schedule_handle"}


async def test_temporal_reads_each_at_most_once_per_5_seconds():
    calls: dict[str, int] = {"running": 0, "pollers": 0, "schedule": 0}

    async def list_running(client, *, task_queue=None):
        calls["running"] += 1
        return []

    async def count_pollers(client, settings):
        calls["pollers"] += 1
        return 1

    async def find_schedule(client, *, use_case_id):
        calls["schedule"] += 1

    clock = Clock()
    source = _temporal_source(
        clock, list_running=list_running, count_pollers=count_pollers, find_schedule=find_schedule
    )

    for _ in range(3):
        await source.refresh()
        clock.advance(seconds=2)
    # 6 s after the first read: one more.
    await source.refresh()

    assert calls == {"running": 2, "pollers": 2, "schedule": 2}
    assert len(source.connects) == 1  # type: ignore[attr-defined]


async def test_probe_failure_is_unavailable_without_connecting():
    source = _temporal_source(Clock(), probe_failure="[Errno 61] Connection refused")

    view = await source.refresh()

    assert view.available is False
    assert view.remedy == "uv run invoke dev.deps"
    assert view.reason is not None
    assert "Connection refused" in view.reason
    assert source.connects == []  # type: ignore[attr-defined]


async def test_no_schedule_is_reported_as_such():
    view = await _temporal_source(Clock()).refresh()

    assert view.data is not None
    assert view.data.schedule is None


async def test_a_read_that_hangs_times_out_into_an_unavailable_view():
    import asyncio

    async def list_running(client, *, task_queue=None):
        await asyncio.sleep(10)
        return []

    source = TemporalSource(
        CliSettings(connect_timeout=0.05),
        "dcfabric",
        probe=lambda _host_port, _timeout: _none(),
        connect=lambda _settings: _value(StrictClient()),
        list_running=list_running,
        clock=Clock(),
    )

    view = await source.refresh()

    assert view.available is False
    assert view.reason is not None
    assert "0.05" in view.reason


async def _none() -> None:
    return None


async def _value(value):
    return value
