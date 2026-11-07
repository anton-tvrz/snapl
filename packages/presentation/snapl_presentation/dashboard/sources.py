"""The dashboard's data sources (spec 010 research R3, R13).

Each source turns one dependency into a ``View``. A dependency that fails is a
view with ``available=False``, a reason and a remedy, never an exception
(FR-005, FR-007). If an earlier read succeeded, its data stays in the view,
marked ``stale`` with the time it was read (FR-031).

Every source only reads. The audit trail is opened in SQLite's read-only mode,
and the SoT is asked for desired state and its identity, nothing else
(research R2).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from snapl_presentation import probes, resolve
from snapl_presentation.dashboard.models import SourceName, View
from snapl_presentation.exceptions import CliError, first_line

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from snapl_intent.abc import IntentStore
    from snapl_intent.models import DesiredState
    from snapl_orchestrator.audit.abc import AuditLog
    from snapl_orchestrator.models import AuditEvent, DriftScheduleView
    from snapl_orchestrator.worker.client import RunningWorkflowInfo
    from snapl_presentation.settings import CliSettings

    Clock = Callable[[], datetime]

STACK_REMEDY = "uv run invoke dev.deps"
AUDIT_REMEDY = "Set SNAPL_AUDIT_DB to the path the worker writes, or run a workflow first."
FOREIGN_SOT_REMEDY = "Point INFRAHUB_ADDRESS at snapl's Infrahub."

# Every instant an audit row can carry: the bounds of a full read.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_END_OF_TIME = datetime(9999, 1, 1, tzinfo=UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


class SourceUnavailable(Exception):  # noqa: N818 — reads as the view it becomes
    """A source could not be read, with what to tell the operator."""

    def __init__(self, reason: str, remedy: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.remedy = remedy


class ViewSource(Protocol):
    async def refresh(self) -> View[Any]: ...


class NamedViewSource(ViewSource, Protocol):
    # Device names from the last successful read, for the degraded list.
    names: dict[UUID, str]


@dataclass(frozen=True)
class Sources:
    """The sources one app serves from. Later phases add CI and lab runs."""

    audit: ViewSource
    sot: NamedViewSource
    temporal: ViewSource


def _failure(exc: Exception, remedy: str) -> SourceUnavailable:
    if isinstance(exc, SourceUnavailable):
        return exc
    return SourceUnavailable(first_line(str(exc)) or type(exc).__name__, remedy)


class Cached[T]:
    """One source's last answer, refreshed at most once per ``ttl``.

    Single-flight: however many requests arrive while a load is running, the
    dependency is asked once. A failed load keeps the last good value, marked
    stale. Failures are cached for the same ``ttl``, so a dead dependency is not
    hammered by every poll.
    """

    def __init__(
        self,
        source: SourceName,
        load: Callable[[], Awaitable[T]],
        *,
        ttl: timedelta,
        clock: Clock = utcnow,
        remedy: str,
    ) -> None:
        self._source = source
        self._load = load
        self._ttl = ttl
        self._clock = clock
        self._remedy = remedy
        self._lock = asyncio.Lock()
        self._view: View[T] | None = None
        self._attempted_at: datetime | None = None
        self._good: tuple[T, datetime] | None = None

    async def get(self) -> View[T]:
        async with self._lock:
            now = self._clock()
            if self._view is not None and self._attempted_at is not None and now - self._attempted_at < self._ttl:
                return self._view
            self._attempted_at = now
            try:
                data = await self._load()
            except Exception as exc:
                failure = _failure(exc, self._remedy)
                self._view = _unavailable(self._source, failure, self._good)
            else:
                self._good = (data, now)
                self._view = View(source=self._source, available=True, fetched_at=now, data=data)
            return self._view


def _unavailable[T](source: SourceName, failure: SourceUnavailable, good: tuple[T, datetime] | None) -> View[T]:
    data, fetched_at = good if good is not None else (None, None)
    return View(
        source=source,
        available=False,
        stale=good is not None,
        fetched_at=fetched_at,
        reason=failure.reason,
        remedy=failure.remedy,
        data=data,
    )


# --- audit ---------------------------------------------------------------------


async def open_read_only(path: str) -> AuditLog:
    """The worker's audit trail, opened so SQLite itself refuses writes (R2).

    A missing file is reported, never created: for a reader, an absent trail is
    not an empty one.
    """
    from snapl_orchestrator.audit.sqlite import SqliteAuditLog  # noqa: PLC0415

    if not Path(path).exists():
        raise SourceUnavailable(f"no audit log at {path}", AUDIT_REMEDY)
    log = SqliteAuditLog(database_url=path, read_only=True)
    await log.initialize()
    return log


class AuditSource:
    """An in-memory read model of the audit trail (research R3).

    The first refresh reads the whole trail. Each later one re-reads a trailing
    window from ``window`` before the newest timestamp seen, because rows are
    inserted after their timestamp, by up to a minute on a healthy worker.
    Every ``rebuild_every`` the model is rebuilt from a full read, which
    catches rows held back longer, for example across a worker restart. A
    rebuild replaces the model only once its read has succeeded.
    """

    def __init__(
        self,
        path: str,
        *,
        open_log: Callable[[str], Awaitable[AuditLog]] = open_read_only,
        clock: Clock = utcnow,
        window: timedelta = timedelta(minutes=5),
        rebuild_every: timedelta = timedelta(minutes=15),
    ) -> None:
        self._path = path
        self._open_log = open_log
        self._clock = clock
        self._window = window
        self._rebuild_every = rebuild_every
        self._lock = asyncio.Lock()
        self._log: AuditLog | None = None
        self._events: dict[UUID, AuditEvent] | None = None
        self._ordered: list[AuditEvent] = []
        self._built_at: datetime | None = None
        self._fetched_at: datetime | None = None

    async def refresh(self) -> View[list[AuditEvent]]:
        async with self._lock:
            now = self._clock()
            try:
                if self._log is None:
                    self._log = await self._open_log(self._path)
                if self._events is None or self._built_at is None or now - self._built_at >= self._rebuild_every:
                    events = await self._log.query_by_time_range(_EPOCH, _END_OF_TIME)
                    self._replace(events)
                    self._built_at = now
                else:
                    newest = self._ordered[-1].timestamp if self._ordered else _EPOCH
                    self._merge(await self._log.query_by_time_range(newest - self._window, _END_OF_TIME))
            except Exception as exc:
                good = (self._ordered, self._fetched_at) if self._events is not None and self._fetched_at else None
                return _unavailable(SourceName.AUDIT, _failure(exc, AUDIT_REMEDY), good)
            self._fetched_at = now
            return View(source=SourceName.AUDIT, available=True, fetched_at=now, data=self._ordered)

    def _replace(self, events: list[AuditEvent]) -> None:
        self._events = {event.event_id: event for event in events}
        self._ordered = sorted(self._events.values(), key=lambda e: e.timestamp)

    def _merge(self, events: list[AuditEvent]) -> None:
        assert self._events is not None
        new = [event for event in events if event.event_id not in self._events]
        if not new:
            return
        self._events.update((event.event_id, event) for event in new)
        # A new list, so a view already handed out never changes under its reader.
        self._ordered = sorted([*self._ordered, *new], key=lambda e: e.timestamp)


# --- Source of Truth -----------------------------------------------------------


class SotSource:
    """The use case's devices from the Source of Truth, cached for 30 s (R13).

    The identity check runs first: a reachable Infrahub that is another
    project's is not ours to describe (FR-015). Only a positive "foreign"
    refuses. An Infrahub that could not be identified is not evidence (#107).
    """

    def __init__(
        self,
        settings: CliSettings,
        use_case: str,
        *,
        build_store: Callable[[CliSettings], IntentStore] = resolve.build_store,
        identify: Callable[..., Awaitable[Any]] = probes.identify_sot,
        clock: Clock = utcnow,
        ttl: timedelta = timedelta(seconds=30),
    ) -> None:
        self._settings = settings
        self._use_case = use_case
        self._build_store = build_store
        self._identify = identify
        self._store: IntentStore | None = None
        self.names: dict[UUID, str] = {}
        self._cached: Cached[list[DesiredState]] = Cached(
            SourceName.SOT, self._load, ttl=ttl, clock=clock, remedy=STACK_REMEDY
        )

    async def refresh(self) -> View[list[DesiredState]]:
        return await self._cached.get()

    async def _load(self) -> list[DesiredState]:
        if self._store is None:
            self._store = self._build_store(self._settings)
        try:
            identity = await self._identify(self._store, address=self._settings.infrahub_address)
        except Exception:
            identity = None
        if identity is not None and identity.is_foreign:
            raise SourceUnavailable(identity.detail, FOREIGN_SOT_REMEDY)
        try:
            states = await resolve.load_states(self._store, self._settings, use_case=self._use_case)
        except CliError as exc:
            raise SourceUnavailable(exc.message, STACK_REMEDY) from exc
        self.names = {state.device.id: state.device.name for state in states}
        return states


# --- Temporal ------------------------------------------------------------------


@dataclass(frozen=True)
class TemporalSnapshot:
    """What Temporal says now: running workflows, the worker, the schedule."""

    running: list[RunningWorkflowInfo]
    pollers: int
    schedule: DriftScheduleView | None


async def _list_running(client: Any, *, task_queue: str | None = None) -> list[RunningWorkflowInfo]:
    from snapl_orchestrator.worker.client import list_running_workflows  # noqa: PLC0415

    return await list_running_workflows(client, task_queue=task_queue)


async def _find_schedule(client: Any, *, use_case_id: str) -> DriftScheduleView | None:
    from snapl_orchestrator.schedules import find_drift_schedule  # noqa: PLC0415

    return await find_drift_schedule(client, use_case_id=use_case_id)


class TemporalSource:
    """Running workflows, the worker's pollers and the schedule, cached 5 s.

    Every call is a read (research R2): list workflows, describe the task
    queue, describe the schedule. A dead or stalled Temporal must not freeze
    the server, whose other views share its event loop. So each refresh
    probes TCP first, even with a client already connected, and every read
    has the connect timeout (``probes.connect`` explains why).
    """

    def __init__(
        self,
        settings: CliSettings,
        use_case: str,
        *,
        probe: Callable[[str, float], Awaitable[str | None]] = probes.probe,
        connect: Callable[[CliSettings], Awaitable[Any]] = probes.connect,
        list_running: Callable[..., Awaitable[list[RunningWorkflowInfo]]] = _list_running,
        count_pollers: Callable[[Any, CliSettings], Awaitable[int]] = probes.count_pollers,
        find_schedule: Callable[..., Awaitable[DriftScheduleView | None]] = _find_schedule,
        clock: Clock = utcnow,
        ttl: timedelta = timedelta(seconds=5),
    ) -> None:
        self._settings = settings
        self._use_case = use_case
        self._probe = probe
        self._connect = connect
        self._list_running = list_running
        self._count_pollers = count_pollers
        self._find_schedule = find_schedule
        self._client: Any = None
        self._cached: Cached[TemporalSnapshot] = Cached(
            SourceName.TEMPORAL, self._load, ttl=ttl, clock=clock, remedy=STACK_REMEDY
        )

    async def refresh(self) -> View[TemporalSnapshot]:
        return await self._cached.get()

    async def _load(self) -> TemporalSnapshot:
        settings = self._settings
        failure = await self._probe(settings.temporal_host, settings.connect_timeout)
        if failure is not None:
            self._client = None
            raise SourceUnavailable(f"Temporal unreachable at {settings.temporal_host}: {failure}", STACK_REMEDY)
        try:
            if self._client is None:
                self._client = await self._connect(settings)
            client = self._client
            running = await self._bounded(self._list_running(client, task_queue=settings.task_queue))
            pollers = await self._bounded(self._count_pollers(client, settings))
            schedule = await self._bounded(self._find_schedule(client, use_case_id=self._use_case))
        except CliError as exc:
            self._client = None
            raise SourceUnavailable(exc.message, STACK_REMEDY) from exc
        except Exception:
            # A fresh client next time: this one may be the problem.
            self._client = None
            raise
        return TemporalSnapshot(running=running, pollers=pollers, schedule=schedule)

    async def _bounded[R](self, read: Awaitable[R]) -> R:
        timeout = self._settings.connect_timeout
        try:
            return await asyncio.wait_for(read, timeout=timeout)
        except TimeoutError as exc:
            raise SourceUnavailable(f"Temporal did not answer within {timeout:g}s", STACK_REMEDY) from exc
