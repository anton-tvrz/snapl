"""The dashboard's HTTP app (spec 010, contracts/dashboard-api.md).

Read-only by construction (research R2): every route is declared with
``@router.get``, so any other method gets ``405``, ``HEAD`` included. The
Swagger and ReDoc pages are off, because they load scripts from a CDN (FR-008).
The OpenAPI document stays at ``/api/v1/openapi.json``.

A source failure is never an HTTP error. It is a view with ``available=False``
inside a ``200`` (FR-005, FR-007).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from importlib import resources
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import AwareDatetime  # noqa: TC002 — FastAPI resolves query types at runtime

from snapl_presentation.dashboard.models import (
    Bucket,
    CiStatus,
    DeviceView,
    Envelope,
    LabEvidence,
    Overview,
    RunView,
    ScheduleStatus,
    Series,
    SeriesName,
    SourceName,
    View,
    WorkerStatus,
)
from snapl_presentation.dashboard.projections import (
    RunFilters,
    latest_pause,
    project_devices,
    project_repeated_failures,
    project_running,
    project_runs,
    project_series,
)
from snapl_presentation.dashboard.sources import AUDIT_REMEDY, STACK_REMEDY, Sources, utcnow
from snapl_presentation.exceptions import first_line

if TYPE_CHECKING:
    from collections.abc import Callable

    from snapl_presentation.settings import CliSettings


# The function argument cannot be called `use_case` (the server's own), so the
# contract's name is an alias.
UseCaseQuery = Annotated[str | None, Query(alias="use_case", description="Defaults to the server's --use-case.")]


class DevicesResponse(Envelope):
    devices: View[list[DeviceView]]


class RunsResponse(Envelope):
    runs: View[list[RunView]]
    running: View[list[RunView]]


class CiResponse(Envelope):
    ci: View[CiStatus]


class LabRunsResponse(Envelope):
    lab: View[LabEvidence]


class HistoryResponse(Envelope):
    series: list[Series]


class ScheduleResponse(Envelope):
    schedule: View[ScheduleStatus]
    worker: View[WorkerStatus]


class SourcesResponse(Envelope):
    # One per SourceName: availability, age, reason and remedy, without data.
    sources: list[View[None]]


# What to run when a source raised or timed out instead of answering.
_FAILURE_REMEDY = {
    SourceName.AUDIT: AUDIT_REMEDY,
    SourceName.SOT: STACK_REMEDY,
    SourceName.TEMPORAL: STACK_REMEDY,
    SourceName.CI: "export GITHUB_TOKEN=…",
    SourceName.LAB_RUNS: "Check SNAPL_TEST_RUNS_DIR, or record a run: uv run invoke test-e2e",
}


# How long one request waits for a source before answering without it. The
# source's own fetch keeps running (bounded by SNAPL_CONNECT_TIMEOUT), and a
# later request picks up its answer (SC-003: the page answers within 2 s).
REQUEST_BUDGET_S = 1.5
# A source whose fetch is longer than one connection gets its own bound. A cold
# CI refresh is 3 + N GitHub calls in a row, each with its own 10 s timeout.
_FETCH_BOUND_S = {SourceName.CI: 60.0}


class _Reader:
    """Reads each source without letting one fail or stall the request.

    Each source has at most one fetch in flight, shared by every request, and
    bounded by ``SNAPL_CONNECT_TIMEOUT`` (CI by longer, ``_FETCH_BOUND_S``). A request waits for it only
    ``REQUEST_BUDGET_S``. If the fetch is still running, the request answers
    without it and the fetch carries on: cancelling it would make a slow
    source, such as GitHub on a cold start, start again on every poll.

    A source that raises or times out becomes an unavailable view with a
    reason and a remedy (FR-007). If it answered earlier in this process, that
    answer is kept, marked stale (FR-031).
    """

    def __init__(self, sources: Sources, timeout: float) -> None:
        self._sources = {
            SourceName.AUDIT: sources.audit,
            SourceName.SOT: sources.sot,
            SourceName.TEMPORAL: sources.temporal,
            SourceName.CI: sources.ci,
            SourceName.LAB_RUNS: sources.lab_runs,
        }
        self._timeout = timeout
        self._tasks: dict[SourceName, asyncio.Task[View[Any]]] = {}
        self._last: dict[SourceName, View[Any]] = {}

    def _bound(self, name: SourceName) -> float:
        return max(self._timeout, _FETCH_BOUND_S.get(name, 0.0))

    def _fetch(self, name: SourceName) -> asyncio.Task[View[Any]]:
        task = self._tasks.get(name)
        if task is None:
            task = asyncio.create_task(asyncio.wait_for(self._sources[name].refresh(), timeout=self._bound(name)))
            # Its outcome is read by the next request; this keeps an unread
            # failure from being logged as "never retrieved".
            task.add_done_callback(lambda done: done.cancelled() or done.exception())
            self._tasks[name] = task
        return task

    async def read(self, name: SourceName) -> View[Any]:
        task = self._fetch(name)
        budget = min(self._timeout, REQUEST_BUDGET_S)
        try:
            view = await asyncio.wait_for(asyncio.shield(task), timeout=budget)
        except TimeoutError:
            if not task.done():
                return self._without(name, f"still waiting for {name.value} (more than {budget:g}s so far)")
            del self._tasks[name]
            return self._without(name, f"{name.value} did not answer within {self._bound(name):g}s")
        except Exception as exc:
            del self._tasks[name]
            return self._without(name, f"{name.value} failed: {first_line(str(exc)) or type(exc).__name__}")
        del self._tasks[name]
        if view.data is not None and view.fetched_at is not None:
            self._last[name] = view
        return view

    def _without(self, name: SourceName, reason: str) -> View[Any]:
        """An unavailable view, carrying the last answer as stale if there was one."""
        last = self._last.get(name)
        return View(
            source=name,
            available=False,
            stale=last is not None,
            fetched_at=last.fetched_at if last else None,
            reason=reason,
            remedy=_FAILURE_REMEDY[name],
            data=last.data if last else None,
        )

    async def read_all(self, *names: SourceName) -> list[View[Any]]:
        # `read` turns every failure into a view, so one slow source costs the
        # request its timeout, never the other sources' answers.
        return list(await asyncio.gather(*(self.read(name) for name in names)))


def compose_devices(audit: View[Any], sot: View[Any], names: dict) -> View[list[DeviceView]]:
    """The device view from its two sources (contracts/dashboard-api.md).

    - SoT unavailable: the degraded, audit-only list (research R13), with the
      SoT's reason. Its last good device list is not reused: the list must say
      plainly that roles and membership are unknown right now.
    - Audit unavailable with no earlier read: every device as never scanned,
      with the audit trail's reason. With an earlier read, that data, stale.
    """
    events = audit.data or []
    if not sot.available:
        devices = project_devices(events, None, names)
        return View(
            source=SourceName.SOT,
            available=False,
            fetched_at=audit.fetched_at,
            reason=sot.reason,
            remedy=sot.remedy,
            data=devices,
        )
    devices = project_devices(events, sot.data or [], names)
    if not audit.available:
        return View(
            source=SourceName.AUDIT,
            available=False,
            stale=audit.stale,
            fetched_at=audit.fetched_at,
            reason=f"the audit trail could not be read: {audit.reason}",
            remedy=audit.remedy,
            data=devices,
        )
    return View(source=SourceName.SOT, available=True, fetched_at=sot.fetched_at, data=devices)


def _from_temporal[T](temporal: View[Any], data: T | None) -> View[T]:
    """A view of Temporal-derived data, carrying Temporal's availability and age."""
    return View(
        source=SourceName.TEMPORAL,
        available=temporal.available,
        stale=temporal.stale and data is not None,
        fetched_at=temporal.fetched_at,
        reason=temporal.reason,
        remedy=temporal.remedy,
        data=data,
    )


def compose_schedule(
    temporal: View[Any],
    audit: View[Any],
    names: dict,
    *,
    use_case: str,
    task_queue: str,
) -> tuple[View[ScheduleStatus], View[WorkerStatus]]:
    """The schedule and worker views (contracts/dashboard-api.md).

    Repeated failures come from the audit trail, so they are reported even when
    Temporal is down, marked stale. When the audit trail is down instead, the
    view is unavailable: its failure list could not be read. When Temporal has never answered, nothing
    is known about the schedule: ``exists`` is false only because the model
    needs a value, ``available`` is false, and no command suggests creating a
    schedule that may well exist.
    """
    events = audit.data or []
    failures = project_repeated_failures(events, names, use_case=use_case)
    snapshot = temporal.data
    if snapshot is None:
        status = ScheduleStatus(exists=False, repeated_failures=failures)
        fetched_at = audit.fetched_at
        schedule_view: View[ScheduleStatus] = View(
            source=SourceName.TEMPORAL,
            available=False,
            stale=fetched_at is not None,
            fetched_at=fetched_at,
            reason=temporal.reason,
            remedy=temporal.remedy,
            data=status,
        )
        return schedule_view, _from_temporal(temporal, None)

    schedule = snapshot.schedule
    pause = latest_pause(events, use_case=use_case) if schedule is not None and schedule.paused else None
    worker = WorkerStatus(pollers=snapshot.pollers, task_queue=task_queue)
    status = ScheduleStatus(
        exists=schedule is not None,
        schedule=schedule,
        paused_by=pause[0] if pause else None,
        paused_at=pause[1] if pause else None,
        repeated_failures=failures,
        suggested_command=None if schedule is not None else f"snapl schedule create --use-case {use_case}",
    )
    if not audit.available:
        # The pause actor and the repeated failures come from the audit trail:
        # without it, an empty failure list would claim repairs are fine.
        schedule_view = View(
            source=SourceName.AUDIT,
            available=False,
            stale=audit.stale,
            fetched_at=temporal.fetched_at,
            reason=f"the audit trail could not be read: {audit.reason}",
            remedy=audit.remedy,
            data=status,
        )
        return schedule_view, _from_temporal(temporal, worker)
    return _from_temporal(temporal, status), _from_temporal(temporal, worker)


def compose_runs(
    audit: View[Any],
    temporal: View[Any],
    names: dict,
    settings: CliSettings,
    now: datetime,
    *,
    limit: int,
    filters: RunFilters = RunFilters(),  # noqa: B008 — frozen, so sharing it is safe
) -> tuple[View[list[RunView]], View[list[RunView]]]:
    """The runs view (from the audit trail) and the running view (from Temporal)."""
    events = audit.data or []
    # Only a current answer says what is running; a stale one could be wrong.
    snapshot = temporal.data if temporal.available else None
    projected = project_runs(
        events,
        {info.workflow_id for info in snapshot.running} if snapshot else None,
        now,
        names=names,
        ui_address=settings.temporal_ui_address,
        namespace=settings.temporal_namespace,
        limit=limit,
        filters=filters,
    )
    runs_view: View[list[RunView]] = View(
        source=SourceName.AUDIT,
        available=audit.available,
        stale=audit.stale,
        fetched_at=audit.fetched_at,
        reason=audit.reason,
        remedy=audit.remedy,
        data=projected,
    )
    running = None
    if temporal.data is not None:
        # Running runs are always taken unfiltered: the panel shows all of them.
        every_run = project_runs(
            events,
            {info.workflow_id for info in temporal.data.running},
            now,
            names=names,
            ui_address=settings.temporal_ui_address,
            namespace=settings.temporal_namespace,
            limit=500,
        )
        running = project_running(
            temporal.data.running,
            every_run,
            now,
            ui_address=settings.temporal_ui_address,
            namespace=settings.temporal_namespace,
        )
    return runs_view, _from_temporal(temporal, running)


def _device_names(sot: View[Any], last_known: dict) -> dict:
    """Device names from the SoT's answer, or from its last good read in this process."""
    if sot.data:
        return {state.device.id: state.device.name for state in sot.data}
    return last_known


def _page() -> str:
    return resources.files("snapl_presentation.dashboard").joinpath("static/index.html").read_text(encoding="utf-8")


def create_app(
    sources: Sources,
    settings: CliSettings,
    use_case: str,
    *,
    clock: Callable[[], datetime] = utcnow,
) -> FastAPI:
    """The dashboard for one use case, served from ``sources``."""
    app = FastAPI(
        title="snapl dashboard",
        version="1",
        openapi_url="/api/v1/openapi.json",
        docs_url=None,
        redoc_url=None,
    )
    router = APIRouter(prefix="/api/v1")
    reader = _Reader(sources, settings.connect_timeout)

    def _check_use_case(requested: str | None) -> str:
        if requested is not None and requested != use_case:
            raise HTTPException(
                status_code=422,
                detail=f"this dashboard serves use case {use_case!r}, not {requested!r}",
            )
        return use_case

    @router.get("/devices", response_model=DevicesResponse)
    async def devices(requested: UseCaseQuery = None) -> DevicesResponse:
        served = _check_use_case(requested)
        audit, sot = await reader.read_all(SourceName.AUDIT, SourceName.SOT)
        return DevicesResponse(
            use_case=served,
            generated_at=clock(),
            devices=compose_devices(audit, sot, sources.sot.names),
        )

    @router.get("/runs", response_model=RunsResponse)
    async def runs(
        requested: UseCaseQuery = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        workflow_type: Annotated[list[str] | None, Query(description="Repeatable.")] = None,
        device: Annotated[str | None, Query(description="A device name.")] = None,
    ) -> RunsResponse:
        served = _check_use_case(requested)
        audit, temporal, sot = await reader.read_all(SourceName.AUDIT, SourceName.TEMPORAL, SourceName.SOT)
        runs_view, running_view = compose_runs(
            audit,
            temporal,
            _device_names(sot, sources.sot.names),
            settings,
            clock(),
            limit=limit,
            filters=RunFilters(workflow_types=frozenset(workflow_type or ()), device=device),
        )
        return RunsResponse(use_case=served, generated_at=clock(), runs=runs_view, running=running_view)

    @router.get("/schedule", response_model=ScheduleResponse)
    async def schedule(requested: UseCaseQuery = None) -> ScheduleResponse:
        served = _check_use_case(requested)
        audit, temporal, sot = await reader.read_all(SourceName.AUDIT, SourceName.TEMPORAL, SourceName.SOT)
        schedule_view, worker_view = compose_schedule(
            temporal, audit, _device_names(sot, sources.sot.names), use_case=served, task_queue=settings.task_queue
        )
        return ScheduleResponse(use_case=served, generated_at=clock(), schedule=schedule_view, worker=worker_view)

    @router.get("/history", response_model=HistoryResponse)
    async def history(
        requested: UseCaseQuery = None,
        series: Annotated[list[SeriesName] | None, Query(description="Repeatable. Defaults to all four.")] = None,
        start: Annotated[AwareDatetime | None, Query(description="Defaults to end - 24h.")] = None,
        end: Annotated[AwareDatetime | None, Query(description="Defaults to now.")] = None,
        bucket: Annotated[Bucket, Query(description="Ignored for per-scan series.")] = Bucket.HOUR,
    ) -> HistoryResponse:
        """Audit-derived history over any range. Gaps are no data, not zero (research R8).

        The series come from the audit trail alone. If it cannot be read, they
        are computed from the last read, or are empty; `/api/v1/devices` and
        the sources view say why.
        """
        served = _check_use_case(requested)
        range_end = end or clock()
        range_start = start or range_end - timedelta(hours=24)
        names = series or list(SeriesName)
        audit = await reader.read(SourceName.AUDIT)
        try:
            projected = [project_series(audit.data or [], name, range_start, range_end, bucket) for name in names]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return HistoryResponse(use_case=served, generated_at=clock(), series=projected)

    @router.get("/ci", response_model=CiResponse)
    async def ci(requested: UseCaseQuery = None) -> CiResponse:
        """`main` and the open PRs: quality gate, jobs and coverage (research R9)."""
        served = _check_use_case(requested)
        return CiResponse(use_case=served, generated_at=clock(), ci=await reader.read(SourceName.CI))

    @router.get("/lab-runs", response_model=LabRunsResponse)
    async def lab_runs(requested: UseCaseQuery = None) -> LabRunsResponse:
        """The recorded live lab runs and how current they are (research R10)."""
        served = _check_use_case(requested)
        return LabRunsResponse(use_case=served, generated_at=clock(), lab=await reader.read(SourceName.LAB_RUNS))

    @router.get("/overview", response_model=Overview)
    async def overview(
        requested: UseCaseQuery = None,
        runs_limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> Overview:
        """Everything the page needs, in one request (US5).

        Every source is read at once, each within its timeout, so the slowest
        source bounds the request and a failing one only marks its own views.
        """
        served = _check_use_case(requested)
        audit, sot, temporal, ci_view, lab_view = await reader.read_all(
            SourceName.AUDIT, SourceName.SOT, SourceName.TEMPORAL, SourceName.CI, SourceName.LAB_RUNS
        )
        names = _device_names(sot, sources.sot.names)
        now = clock()
        runs_view, running_view = compose_runs(audit, temporal, names, settings, now, limit=runs_limit)
        schedule_view, worker_view = compose_schedule(
            temporal, audit, names, use_case=served, task_queue=settings.task_queue
        )
        return Overview(
            use_case=served,
            generated_at=now,
            devices=compose_devices(audit, sot, sources.sot.names),
            runs=runs_view,
            running=running_view,
            schedule=schedule_view,
            worker=worker_view,
            ci=ci_view,
            lab=lab_view,
        )

    @router.get("/sources", response_model=SourcesResponse)
    async def sources_status(requested: UseCaseQuery = None) -> SourcesResponse:
        """Each source's state and the age of its last good read, without the data.

        ``stale`` is always false here: the model ties it to the data, which this
        view leaves out. ``fetched_at`` carries the age instead.
        """
        served = _check_use_case(requested)
        views = await reader.read_all(*SourceName)
        return SourcesResponse(
            use_case=served,
            generated_at=clock(),
            sources=[
                View(
                    source=view.source,
                    available=view.available,
                    fetched_at=view.fetched_at,
                    reason=view.reason,
                    remedy=view.remedy,
                )
                for view in views
            ],
        )

    app.include_router(router)

    page = _page()

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> HTMLResponse:
        return HTMLResponse(page)

    return app
