"""State at a past time (spec 011 T003, research R11).

``at`` projects the device and run views from the audit events recorded up to
that time. The captured demo arc (fixtures/README.md) gives the expected states
at each of its three scans.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from snapl_orchestrator.models import AuditEvent, AuditEventType, WorkflowReason
from snapl_presentation.dashboard.app import create_app
from snapl_presentation.dashboard.models import SourceName, View
from snapl_presentation.dashboard.projections import events_until
from snapl_presentation.dashboard.sources import Sources, TemporalSnapshot
from snapl_presentation.settings import CliSettings
from tests.unit.test_presentation.dashboard_fakes import (
    ARC,
    SCAN_1_END,
    SCAN_2_END,
    SCAN_3_END,
    arc_events,
    arc_states,
)

pytestmark = pytest.mark.unit

NOW = SCAN_3_END + timedelta(minutes=1)
SPINE_01 = ARC["spine-01"][1]


class _Source:
    def __init__(self, view: View[Any], names: dict | None = None) -> None:
        self.view = view
        self.names = names or {}

    async def refresh(self) -> View[Any]:
        return self.view


def _client(events: list[AuditEvent], *, running: list | None = None) -> TestClient:
    states = arc_states()

    def ok(source: SourceName, data: Any) -> View[Any]:
        return View(source=source, available=True, fetched_at=NOW, data=data)

    sources = Sources(
        audit=_Source(ok(SourceName.AUDIT, events)),
        sot=_Source(ok(SourceName.SOT, states), {s.device.id: s.device.name for s in states}),
        temporal=_Source(ok(SourceName.TEMPORAL, TemporalSnapshot(running=running or [], pollers=1, schedule=None))),
        ci=_Source(View(source=SourceName.CI, available=False, reason="not asked", remedy="-")),
        lab_runs=_Source(View(source=SourceName.LAB_RUNS, available=False, reason="not asked", remedy="-")),
        prometheus=_Source(View(source=SourceName.PROMETHEUS, available=False, reason="not asked", remedy="-")),
    )
    return TestClient(create_app(sources, CliSettings(), "dcfabric", clock=lambda: NOW))


def _states(client: TestClient, at: datetime | None) -> dict[str, str]:
    params = {"at": at.isoformat()} if at else {}
    response = client.get("/api/v1/devices", params=params)
    assert response.status_code == 200, response.text
    return {device["name"]: device["drift_state"] for device in response.json()["devices"]["data"]}


def _event(at: datetime, event_type: AuditEventType = AuditEventType.WORKFLOW_STARTED) -> AuditEvent:
    return AuditEvent(
        event_id=uuid4(),
        workflow_id="deploy-intent-x",
        workflow_type="DeployIntent",
        target_id=SPINE_01,
        event_type=event_type,
        outcome="success" if event_type is AuditEventType.WORKFLOW_TERMINATED else None,
        reason=WorkflowReason.SUCCEEDED if event_type is AuditEventType.WORKFLOW_TERMINATED else None,
        timestamp=at,
    )


# --- events_until ----------------------------------------------------------------


def test_events_until_keeps_events_at_or_before_the_time():
    t0 = datetime(2026, 11, 7, 21, 0, tzinfo=UTC)
    events = [_event(t0 + timedelta(seconds=offset)) for offset in (0, 1, 1, 2)]

    assert events_until(events, t0 + timedelta(seconds=1)) == events[:3]
    assert events_until(events, t0 - timedelta(seconds=1)) == []
    assert events_until(events, t0 + timedelta(hours=1)) == events


def test_events_until_does_not_depend_on_the_order_rows_arrived_in():
    t0 = datetime(2026, 11, 7, 21, 0, tzinfo=UTC)
    late, early = _event(t0 + timedelta(seconds=5)), _event(t0)

    # Rows land after their timestamp (010 research R3), so arrival order is not time order.
    assert events_until([late, early], t0 + timedelta(seconds=1)) == [early]


# --- /api/v1/devices?at= ---------------------------------------------------------


async def test_devices_at_each_scan_of_the_demo_arc():
    client = _client(await arc_events())

    after_1 = _states(client, SCAN_1_END)
    after_2 = _states(client, SCAN_2_END)
    after_3 = _states(client, SCAN_3_END)

    assert (after_1["spine-01"], after_2["spine-01"], after_3["spine-01"]) == ("missing", "missing", "in_sync")
    assert (after_1["leaf-01"], after_2["leaf-01"], after_3["leaf-01"]) == ("undesired", "undesired", "undesired")
    for name in ("spine-02", "leaf-02", "leaf-03", "leaf-04"):
        assert (after_1[name], after_2[name], after_3[name]) == ("in_sync", "in_sync", "in_sync"), name


async def test_devices_before_the_first_scan_are_never_scanned_but_already_deployed():
    client = _client(await arc_events())
    before = SCAN_1_END - timedelta(seconds=30)

    response = client.get("/api/v1/devices", params={"at": before.isoformat()})

    devices = response.json()["devices"]["data"]
    assert {device["drift_state"] for device in devices} == {"never_scanned"}
    # The six operator deploys had finished by then, and the reconcile's had not started.
    assert all(device["last_deploy"]["success"] for device in devices)
    assert all(device["last_deploy"]["ended_at"] < "2026-11-07T21:20:00Z" for device in devices)


async def test_devices_before_the_first_event_shows_every_device_with_nothing_recorded():
    client = _client(await arc_events())

    response = client.get("/api/v1/devices", params={"at": "2026-11-01T00:00:00Z"})

    devices = response.json()["devices"]["data"]
    assert len(devices) == 6
    assert all(d["drift_state"] == "never_scanned" and d["last_deploy"] is None for d in devices)


async def test_devices_without_at_is_the_latest_state():
    client = _client(await arc_events())

    assert _states(client, None) == _states(client, SCAN_3_END)


# --- /api/v1/runs?at= ------------------------------------------------------------


async def test_a_run_open_at_the_time_that_later_ended_is_running():
    client = _client(await arc_events())
    # The reconcile ran 21:20:15.47 to 21:20:17.44; spine-01's deploy inside it ended at 17.24.
    during = datetime(2026, 11, 7, 21, 20, 16, tzinfo=UTC)

    body = client.get("/api/v1/runs", params={"at": during.isoformat(), "limit": 500}).json()

    runs = {(run["workflow_type"], run["target"]): run for run in body["runs"]["data"][:3]}
    # The captured reconcile records no target of its own.
    assert runs[("ReconcileDevices", None)]["state"] == "running"
    assert runs[("DeployIntent", "spine-01")]["state"] == "running"
    assert runs[("DeployIntent", "spine-01")]["ended_at"] is None
    # Elapsed time is counted up to `at`, not to today.
    assert runs[("DeployIntent", "spine-01")]["duration_s"] == 0
    # Nothing later than `at` is listed: the third scan had not started.
    assert len(body["runs"]["data"]) == 11


async def test_a_run_with_no_end_in_the_trail_is_unfinished_at_a_past_time():
    t0 = datetime(2026, 11, 7, 21, 0, tzinfo=UTC)
    client = _client([_event(t0)])

    body = client.get("/api/v1/runs", params={"at": (t0 + timedelta(seconds=5)).isoformat()}).json()

    assert [run["state"] for run in body["runs"]["data"]] == ["unfinished"]


async def test_runs_at_a_past_time_does_not_report_what_temporal_runs_now():
    client = _client(await arc_events())

    body = client.get("/api/v1/runs", params={"at": SCAN_2_END.isoformat()}).json()

    running = body["running"]
    assert running["available"] is False
    assert running["source"] == "temporal"
    assert running["reason"] == "the workflow engine has no history"
    assert "without `at`" in running["remedy"]
    assert running["data"] is None


async def test_runs_without_at_is_unchanged():
    client = _client(await arc_events())

    body = client.get("/api/v1/runs", params={"limit": 500}).json()

    assert len(body["runs"]["data"]) == 12
    assert body["running"]["available"] is True
    assert {run["state"] for run in body["runs"]["data"]} == {"succeeded"}


# --- bad input -------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/v1/devices", "/api/v1/runs"])
@pytest.mark.parametrize(
    "at",
    [
        "2026-11-07T21:20:16",  # naive
        "2026-11-07T21:25:00Z",  # after the server's now
        "yesterday",
    ],
)
def test_a_naive_future_or_malformed_at_is_422(path: str, at: str):
    assert _client([]).get(path, params={"at": at}).status_code == 422


@pytest.mark.parametrize("path", ["/api/v1/devices", "/api/v1/runs"])
def test_at_equal_to_now_is_accepted(path: str):
    assert _client([]).get(path, params={"at": NOW.isoformat()}).status_code == 200
