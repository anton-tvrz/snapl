"""The dashboard's HTTP app (spec 010, contracts/dashboard-api.md).

Read-only by construction (research R2): every route is declared with
``@router.get``, so any other method gets ``405``, ``HEAD`` included. The
Swagger and ReDoc pages are off, because they load scripts from a CDN (FR-008).
The OpenAPI document stays at ``/api/v1/openapi.json``.

A source failure is never an HTTP error. It is a view with ``available=False``
inside a ``200`` (FR-005, FR-007).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — FastAPI resolves annotations at runtime
from importlib import resources
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from snapl_presentation.dashboard.models import DeviceView, Envelope, SourceName, View
from snapl_presentation.dashboard.projections import project_devices
from snapl_presentation.dashboard.sources import Sources, utcnow

if TYPE_CHECKING:
    from collections.abc import Callable

    from snapl_presentation.settings import CliSettings


# The function argument cannot be called `use_case` (the server's own), so the
# contract's name is an alias.
UseCaseQuery = Annotated[str | None, Query(alias="use_case", description="Defaults to the server's --use-case.")]


class DevicesResponse(Envelope):
    devices: View[list[DeviceView]]


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


def _page() -> str:
    return resources.files("snapl_presentation.dashboard").joinpath("static/index.html").read_text(encoding="utf-8")


def create_app(
    sources: Sources,
    settings: CliSettings,
    use_case: str,
    *,
    clock: Callable[[], datetime] = utcnow,
) -> FastAPI:
    """The dashboard for one use case, served from ``sources``.

    ``settings`` is unused by the routes PR B serves; the runs view takes the
    Temporal UI address from it (research R12).
    """
    del settings
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

    app.include_router(router)

    page = _page()

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> HTMLResponse:
        return HTMLResponse(page)

    return app
