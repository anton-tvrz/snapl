"""The dashboard's HTTP app (spec 010, contracts/dashboard-api.md).

Read-only by construction (research R2): every route is declared with
``@router.get``, so any other method gets ``405``, ``HEAD`` included. The
Swagger and ReDoc pages are off, because they load scripts from a CDN (FR-008).
The OpenAPI document stays at ``/api/v1/openapi.json``.

A source failure is never an HTTP error. It is a view with ``available=False``
inside a ``200`` (FR-005, FR-007).
"""

from __future__ import annotations

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
from snapl_presentation.dashboard.sources import Sources, utcnow

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
    Temporal is down, marked stale. When Temporal has never answered, nothing
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
    status = ScheduleStatus(
        exists=schedule is not None,
        schedule=schedule,
        paused_by=pause[0] if pause else None,
        paused_at=pause[1] if pause else None,
        repeated_failures=failures,
        suggested_command=None if schedule is not None else f"snapl schedule create --use-case {use_case}",
    )
    worker = WorkerStatus(pollers=snapshot.pollers, task_queue=task_queue)
    return _from_temporal(temporal, status), _from_temporal(temporal, worker)


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
        audit = await sources.audit.refresh()
        sot = await sources.sot.refresh()
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
        audit = await sources.audit.refresh()
        temporal = await sources.temporal.refresh()
        names = _device_names(await sources.sot.refresh(), sources.sot.names)
        # Only a current answer says what is running; a stale one could be wrong.
        snapshot = temporal.data if temporal.available else None
        projected = project_runs(
            audit.data or [],
            {info.workflow_id for info in snapshot.running} if snapshot else None,
            clock(),
            names=names,
            ui_address=settings.temporal_ui_address,
            namespace=settings.temporal_namespace,
            limit=limit,
            filters=RunFilters(workflow_types=frozenset(workflow_type or ()), device=device),
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
                audit.data or [],
                {info.workflow_id for info in temporal.data.running},
                clock(),
                names=names,
                ui_address=settings.temporal_ui_address,
                namespace=settings.temporal_namespace,
                limit=500,
            )
            running = project_running(
                temporal.data.running,
                every_run,
                clock(),
                ui_address=settings.temporal_ui_address,
                namespace=settings.temporal_namespace,
            )
        return RunsResponse(
            use_case=served,
            generated_at=clock(),
            runs=runs_view,
            running=_from_temporal(temporal, running),
        )

    @router.get("/schedule", response_model=ScheduleResponse)
    async def schedule(requested: UseCaseQuery = None) -> ScheduleResponse:
        served = _check_use_case(requested)
        audit = await sources.audit.refresh()
        temporal = await sources.temporal.refresh()
        names = _device_names(await sources.sot.refresh(), sources.sot.names)
        schedule_view, worker_view = compose_schedule(
            temporal, audit, names, use_case=served, task_queue=settings.task_queue
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
        audit = await sources.audit.refresh()
        try:
            projected = [project_series(audit.data or [], name, range_start, range_end, bucket) for name in names]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return HistoryResponse(use_case=served, generated_at=clock(), series=projected)

    @router.get("/ci", response_model=CiResponse)
    async def ci(requested: UseCaseQuery = None) -> CiResponse:
        """`main` and the open PRs: quality gate, jobs and coverage (research R9)."""
        served = _check_use_case(requested)
        return CiResponse(use_case=served, generated_at=clock(), ci=await sources.ci.refresh())

    @router.get("/lab-runs", response_model=LabRunsResponse)
    async def lab_runs(requested: UseCaseQuery = None) -> LabRunsResponse:
        """The recorded live lab runs and how current they are (research R10)."""
        served = _check_use_case(requested)
        return LabRunsResponse(use_case=served, generated_at=clock(), lab=await sources.lab_runs.refresh())

    app.include_router(router)

    page = _page()

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> HTMLResponse:
        return HTMLResponse(page)

    return app
