"""The dashboard's HTTP surface (spec 010 T017, contracts/dashboard-api.md).

The app is built over fake sources, so these tests pin the contract alone:
which routes exist, that they only read (SC-006), what the device view says when
a source is down (FR-007), and that the page loads nothing from elsewhere
(FR-008).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from snapl_intent.models import DesiredState, Device
from snapl_orchestrator.models import (
    AuditEvent,
    AuditEventType,
    DriftResponseMode,
    DriftScheduleView,
    WorkflowReason,
)
from snapl_orchestrator.worker.client import RunningWorkflowInfo
from snapl_presentation.dashboard.app import create_app
from snapl_presentation.dashboard.models import (
    CiStatus,
    CiTarget,
    JobResult,
    LabEvidence,
    LabRun,
    LabRunStatus,
    MetricsData,
    MetricsViewName,
    SourceName,
    View,
)
from snapl_presentation.dashboard.sources import Sources, TemporalSnapshot
from snapl_presentation.settings import CliSettings

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 7, 21, 0, tzinfo=UTC)
SNAPSHOT = Path(__file__).parent / "fixtures" / "openapi_v1.json"

# Every route in contracts/dashboard-api.md.
CONTRACT_ROUTES = {
    ("/", "GET"),
    ("/api/v1/ci", "GET"),
    ("/api/v1/devices", "GET"),
    ("/api/v1/history", "GET"),
    ("/api/v1/lab-runs", "GET"),
    ("/api/v1/links", "GET"),
    ("/api/v1/metrics/{view}", "GET"),
    ("/api/v1/overview", "GET"),
    ("/api/v1/runs", "GET"),
    ("/api/v1/schedule", "GET"),
    ("/api/v1/sources", "GET"),
    ("/api/v1/openapi.json", "GET"),
    ("/api/v1/openapi.json", "HEAD"),
    # Prometheus text format for scrapers, outside /api/v1 (spec 012 R8).
    ("/metrics", "GET"),
}


class FakeSource:
    def __init__(self, view: View[Any], names: dict | None = None) -> None:
        self.view = view
        self.names = names or {}

    async def refresh(self) -> View[Any]:
        return self.view


class FakePrometheus:
    """A metrics source: ready for /sources, and canned or failing range queries."""

    def __init__(self, ready: View[Any] | None = None, *, fail: Exception | None = None) -> None:
        self.ready = ready or View(source=SourceName.PROMETHEUS, available=True, fetched_at=T0)
        self.fail = fail
        self.plans: list = []

    async def refresh(self) -> View[Any]:
        return self.ready

    async def query(self, view: MetricsViewName, plan) -> MetricsData:
        self.plans.append((view, plan))
        if self.fail is not None:
            raise self.fail
        return MetricsData(view=view, unit="ms", start=plan.start, end=plan.end, step=plan.step, series=[])


SPINE = DesiredState(
    device=Device(id=uuid4(), name="spine-01", management_address="10.0.0.1", role="spine", use_case="dcfabric")
)
LEAF = DesiredState(
    device=Device(id=uuid4(), name="leaf-01", management_address="10.0.0.11", role="leaf", use_case="dcfabric")
)


def _drifted_scan() -> AuditEvent:
    return AuditEvent(
        event_id=uuid4(),
        workflow_id="scan-drift-dcfabric-1",
        workflow_type="ScanDrift",
        target_id=LEAF.device.id,
        event_type=AuditEventType.ACTIVITY_COMPLETED,
        activity_name="detect_drift",
        outcome="success",
        payload={"status": "drifted", "drift_items": 1, "paths": ["/a"], "undesired_paths": []},
        timestamp=T0,
    )


def _audit_ok() -> View[Any]:
    return View(source=SourceName.AUDIT, available=True, fetched_at=T0, data=[_drifted_scan()])


def _sot_ok() -> View[Any]:
    return View(source=SourceName.SOT, available=True, fetched_at=T0, data=[LEAF, SPINE])


def _temporal_ok(schedule: DriftScheduleView | None = None, running: list | None = None) -> View[Any]:
    return View(
        source=SourceName.TEMPORAL,
        available=True,
        fetched_at=T0,
        data=TemporalSnapshot(running=running or [], pollers=1, schedule=schedule),
    )


def _ci_ok() -> View[Any]:
    main = CiTarget(kind="main", head_sha="a" * 40, gate=JobResult.SUCCESS, jobs=[], coverage_pct=None)
    return View(
        source=SourceName.CI,
        available=True,
        fetched_at=T0,
        data=CiStatus(repo="anton-tvrz/snapl", authenticated=False, targets=[main]),
    )


def _lab_ok() -> View[Any]:
    run = LabRun(id="20261107T101500Z-e2e", suite="e2e", status=LabRunStatus.PASSED, commit="b" * 40, passed=14)
    return View(
        source=SourceName.LAB_RUNS,
        available=True,
        fetched_at=T0,
        data=LabEvidence(
            latest={"e2e": run}, commits_on_main_since={"e2e": 3}, matched_by={"e2e": "ancestor"}, runs=[run]
        ),
    )


def _client(
    audit: View[Any] | None = None,
    sot: View[Any] | None = None,
    names: dict | None = None,
    temporal: View[Any] | None = None,
    ci: View[Any] | None = None,
    lab: View[Any] | None = None,
    settings: CliSettings | None = None,
    repo: str | None = "anton-tvrz/snapl",
    prometheus: Any = None,
) -> TestClient:
    sources = Sources(
        audit=FakeSource(audit or _audit_ok()),
        sot=FakeSource(sot or _sot_ok(), names),
        temporal=FakeSource(temporal or _temporal_ok()),
        ci=FakeSource(ci or _ci_ok()),
        lab_runs=FakeSource(lab or _lab_ok()),
        prometheus=prometheus or FakePrometheus(),
    )
    app = create_app(sources, settings or CliSettings(), "dcfabric", clock=lambda: T0 + timedelta(seconds=5), repo=repo)
    return TestClient(app)


# --- /api/v1/devices -----------------------------------------------------------


def test_devices_returns_the_envelope_and_a_devices_view():
    response = _client().get("/api/v1/devices")

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "1"
    assert body["use_case"] == "dcfabric"
    assert body["generated_at"] == "2026-11-07T21:00:05Z"
    view = body["devices"]
    assert view["available"] is True
    assert [device["name"] for device in view["data"]] == ["spine-01", "leaf-01"]
    assert view["data"][1]["drift_state"] == "missing"
    assert view["data"][1]["suggested_command"] == "snapl reconcile leaf-01"


def test_devices_rejects_a_use_case_the_server_was_not_started_for():
    assert _client().get("/api/v1/devices", params={"use_case": "campus"}).status_code == 422
    assert _client().get("/api/v1/devices", params={"use_case": "dcfabric"}).status_code == 200


def test_sot_down_gives_the_degraded_audit_only_list():
    sot = View(source=SourceName.SOT, available=False, reason="Infrahub unreachable", remedy="uv run invoke dev.deps")

    view = _client(sot=sot, names={LEAF.device.id: "leaf-01"}).get("/api/v1/devices").json()["devices"]

    assert view["available"] is False
    assert view["source"] == "sot"
    assert view["reason"] == "Infrahub unreachable"
    assert view["remedy"] == "uv run invoke dev.deps"
    assert [(device["name"], device["role"]) for device in view["data"]] == [("leaf-01", None)]
    assert view["data"][0]["drift_state"] == "missing"


def test_audit_down_lists_every_device_as_never_scanned():
    audit = View(
        source=SourceName.AUDIT,
        available=False,
        reason="no audit log at /var/snapl/audit.db",
        remedy="Set SNAPL_AUDIT_DB to the path the worker writes",
    )

    view = _client(audit=audit).get("/api/v1/devices").json()["devices"]

    assert view["available"] is False
    assert view["source"] == "audit"
    assert "audit" in view["reason"]
    assert view["remedy"].startswith("Set SNAPL_AUDIT_DB")
    assert [device["drift_state"] for device in view["data"]] == ["never_scanned", "never_scanned"]


def test_stale_audit_data_is_still_shown_and_marked_stale():
    audit = View(
        source=SourceName.AUDIT,
        available=False,
        stale=True,
        fetched_at=T0,
        reason="disk I/O error",
        remedy="Set SNAPL_AUDIT_DB to the path the worker writes",
        data=[_drifted_scan()],
    )

    view = _client(audit=audit).get("/api/v1/devices").json()["devices"]

    assert view["stale"] is True
    assert view["fetched_at"] == "2026-11-07T21:00:00Z"
    assert view["data"][1]["drift_state"] == "missing"


# --- read-only (SC-006, research R1 and R2) ------------------------------------


def _collect_routes(routes) -> set[tuple[str, str]]:
    """Every (path, method), descending into included routers.

    An included router appears in ``app.routes`` as one entry with no path and
    an empty method set, so a flat walk would pass whatever it holds.
    """
    found: set[tuple[str, str]] = set()
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            prefix = getattr(route, "prefix", "") or ""
            found |= {(prefix + path, method) for path, method in _collect_routes(original.routes)}
            continue
        for method in getattr(route, "methods", None) or ():
            found.add((route.path, method))
    return found


def test_every_route_only_reads_and_matches_the_contract():
    routes = _collect_routes(_client().app.routes)

    assert routes == CONTRACT_ROUTES
    assert {method for _, method in routes} <= {"GET", "HEAD"}
    assert {path for path, method in routes if method == "HEAD"} == {"/api/v1/openapi.json"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/devices",
        "/api/v1/runs",
        "/api/v1/schedule",
        "/api/v1/history",
        "/api/v1/ci",
        "/api/v1/lab-runs",
        "/api/v1/overview",
        "/api/v1/sources",
        "/api/v1/metrics/gnmi-duration",
    ],
)
@pytest.mark.parametrize("method", ["HEAD", "POST", "PUT", "PATCH", "DELETE"])
def test_other_methods_are_refused(method: str, path: str):
    assert _client().request(method, path).status_code == 405


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_cdn_backed_doc_pages_are_off(path: str):
    assert _client().get(path).status_code == 404


def test_openapi_document_matches_the_snapshot():
    """An accidental contract change fails here; an intended one updates the
    snapshot in the same PR (research R15)."""
    document = _client().get("/api/v1/openapi.json").json()

    assert document == json.loads(SNAPSHOT.read_text())


# --- the page (FR-008) ---------------------------------------------------------


def test_page_is_html_with_no_external_urls():
    response = _client().get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "/api/v1/overview" in response.text
    assert "/api/v1/sources" in response.text
    assert "/api/v1/links" in response.text
    assert re.findall(r"https?://", response.text) == []


def test_page_never_renders_through_innerhtml():
    """Audit payloads hold device-supplied strings (research R11)."""
    assert "innerHTML" not in _client().get("/").text


# --- /api/v1/links (#173) -------------------------------------------------------

LINK_SETTINGS = CliSettings(
    infrahub_address="http://infrahub.example:18000",
    temporal_ui_address="http://temporal.example:18034/",
    temporal_namespace="lab",
    project_board_url="https://github.com/users/someone/projects/9",
)


def _links(client: TestClient) -> dict:
    response = client.get("/api/v1/links")
    assert response.status_code == 200
    return response.json()


def test_links_point_at_the_stack_and_repo_the_dashboard_reads():
    body = _links(_client(settings=LINK_SETTINGS, repo="someone/fork"))

    assert body["use_case"] == "dcfabric"
    assert body["reason"] is None
    assert body["remedy"] is None
    assert [(link["group"], link["label"], link["url"]) for link in body["links"]] == [
        ("stack", "Infrahub", "http://infrahub.example:18000"),
        ("stack", "Temporal", "http://temporal.example:18034/namespaces/lab/workflows"),
        ("stack", "Drift schedule", "http://temporal.example:18034/namespaces/lab/schedules/drift-response-dcfabric"),
        ("github", "Repository", "https://github.com/someone/fork"),
        ("github", "Pull requests", "https://github.com/someone/fork/pulls"),
        ("github", "Actions", "https://github.com/someone/fork/actions"),
        ("github", "Project board", "https://github.com/users/someone/projects/9"),
        ("api", "OpenAPI", "/api/v1/openapi.json"),
        ("api", "Overview JSON", "/api/v1/overview"),
        (
            "docs",
            "Quickstart",
            "https://github.com/someone/fork/blob/main/specs/010-dashboard-read-api/quickstart.md",
        ),
        ("docs", "Demo scenarios", "https://github.com/someone/fork/blob/main/docs/demo-scenarios.md"),
    ]


def test_without_a_repo_the_repo_links_are_left_out_and_it_says_what_to_set():
    settings = CliSettings(project_board_url=None)

    body = _links(_client(settings=settings, repo=None))

    assert {link["group"] for link in body["links"]} == {"stack", "api"}
    assert "GitHub repo" in body["reason"]
    assert body["remedy"] == "export SNAPL_GITHUB_REPO=owner/name"


def test_the_docs_linked_from_the_dashboard_exist():
    """A moved doc would leave a dead link on the page."""
    root = Path(__file__).resolve().parents[3]
    body = _links(_client(settings=LINK_SETTINGS, repo="someone/fork"))

    for link in body["links"]:
        if link["group"] == "docs":
            path = link["url"].split("/blob/main/", 1)[1]
            assert (root / path).is_file(), path


# --- /api/v1/runs (T027) --------------------------------------------------------


def _deploy_run() -> list[AuditEvent]:
    deploy = f"deploy-intent-{LEAF.device.id}"
    common = {"workflow_id": deploy, "workflow_type": "DeployIntent", "target_id": LEAF.device.id}
    return [
        AuditEvent(
            event_id=uuid4(),
            event_type=AuditEventType.WORKFLOW_STARTED,
            timestamp=T0,
            payload={"parent_workflow_id": None},
            **common,
        ),
        AuditEvent(
            event_id=uuid4(),
            event_type=AuditEventType.WORKFLOW_TERMINATED,
            outcome="success",
            reason=WorkflowReason.SUCCEEDED,
            timestamp=T0 + timedelta(seconds=4),
            **common,
        ),
    ]


def _scan_start(workflow_id: str = "scan-drift-dcfabric-2") -> AuditEvent:
    return AuditEvent(
        event_id=uuid4(),
        workflow_id=workflow_id,
        workflow_type="ScanDrift",
        target_id="dcfabric",
        event_type=AuditEventType.WORKFLOW_STARTED,
        timestamp=T0 + timedelta(seconds=1),
    )


def _runs_audit() -> View[Any]:
    return View(source=SourceName.AUDIT, available=True, fetched_at=T0, data=[*_deploy_run(), _scan_start()])


def _temporal_down() -> View[Any]:
    return View(
        source=SourceName.TEMPORAL, available=False, reason="Temporal unreachable", remedy="uv run invoke dev.deps"
    )


def test_runs_returns_runs_and_what_is_running():
    temporal = _temporal_ok(
        running=[
            RunningWorkflowInfo(
                workflow_id="scan-drift-dcfabric-2",
                workflow_type="ScanDrift",
                start_time=T0 + timedelta(seconds=1),
                task_queue="snapl",
            )
        ]
    )

    body = _client(audit=_runs_audit(), temporal=temporal).get("/api/v1/runs").json()

    runs = body["runs"]["data"]
    assert [(r["workflow_type"], r["state"], r["trigger"]) for r in runs] == [
        ("ScanDrift", "running", "operator"),
        ("DeployIntent", "succeeded", "operator"),
    ]
    assert runs[1]["target"] == "leaf-01"
    assert runs[1]["web_ui_url"].startswith("http://localhost:18034/namespaces/default/workflows/deploy-intent-")
    assert [r["workflow_id"] for r in body["running"]["data"]] == ["scan-drift-dcfabric-2"]


@pytest.mark.parametrize(("limit", "status"), [(0, 422), (1, 200), (500, 200), (501, 422)])
def test_runs_limit_bounds(limit: int, status: int):
    assert _client().get("/api/v1/runs", params={"limit": limit}).status_code == status


def test_runs_filters_are_repeatable_and_by_device():
    client = _client(audit=_runs_audit())

    both = client.get("/api/v1/runs", params=[("workflow_type", "DeployIntent"), ("workflow_type", "ScanDrift")])
    deploys = client.get("/api/v1/runs", params={"workflow_type": "DeployIntent"})
    leaf = client.get("/api/v1/runs", params={"device": "leaf-01"})

    assert len(both.json()["runs"]["data"]) == 2
    assert [r["workflow_type"] for r in deploys.json()["runs"]["data"]] == ["DeployIntent"]
    assert [r["target"] for r in leaf.json()["runs"]["data"]] == ["leaf-01"]


def test_runs_with_temporal_down_still_lists_the_trail():
    audit = View(source=SourceName.AUDIT, available=True, fetched_at=T0, data=[_scan_start()])

    body = _client(audit=audit, temporal=_temporal_down()).get("/api/v1/runs").json()

    assert body["runs"]["available"] is True
    assert [r["state"] for r in body["runs"]["data"]] == ["unfinished"]
    assert body["running"]["available"] is False
    assert body["running"]["remedy"] == "uv run invoke dev.deps"


# --- /api/v1/schedule (T027) ----------------------------------------------------


def _schedule(*, paused: bool = False) -> DriftScheduleView:
    return DriftScheduleView(
        schedule_id="drift-response-dcfabric",
        use_case_id="dcfabric",
        interval=timedelta(minutes=5),
        mode=DriftResponseMode.REMEDIATE,
        paused=paused,
        note="maintenance window" if paused else None,
        next_runs=[T0 + timedelta(minutes=5)],
    )


def _failed_repairs() -> list[AuditEvent]:
    return [
        AuditEvent(
            event_id=uuid4(),
            workflow_id=f"drift-response-dcfabric-{i}",
            workflow_type="DriftResponse",
            target_id=LEAF.device.id,
            event_type=AuditEventType.DRIFT_RESPONSE,
            payload={"decision": "remediated", "result": "failed", "scan_workflow_id": "s", "paths": []},
            timestamp=T0 + timedelta(minutes=5 * i),
            actor="schedule:drift-response-dcfabric",
        )
        for i in range(3)
    ]


def _pause() -> AuditEvent:
    return AuditEvent(
        event_id=uuid4(),
        workflow_id="drift-response-dcfabric",
        workflow_type="DriftSchedule",
        target_id="dcfabric",
        event_type=AuditEventType.SCHEDULE_CHANGED,
        payload={"change": "paused"},
        timestamp=T0,
        actor="alice",
    )


def test_schedule_with_temporal_down_still_reports_repeated_failures_as_stale():
    audit = View(source=SourceName.AUDIT, available=True, fetched_at=T0, data=_failed_repairs())

    body = _client(audit=audit, temporal=_temporal_down()).get("/api/v1/schedule").json()

    schedule = body["schedule"]
    assert schedule["available"] is False
    assert schedule["remedy"] == "uv run invoke dev.deps"
    assert schedule["stale"] is True
    [failure] = schedule["data"]["repeated_failures"]
    assert failure["name"] == "leaf-01"
    assert failure["consecutive_failures"] == 3
    # Temporal never answered: nothing suggests creating a schedule that may exist.
    assert schedule["data"]["suggested_command"] is None
    assert body["worker"]["available"] is False


def test_schedule_paused_names_who_paused_it():
    audit = View(source=SourceName.AUDIT, available=True, fetched_at=T0, data=[_pause()])

    body = _client(audit=audit, temporal=_temporal_ok(_schedule(paused=True))).get("/api/v1/schedule").json()

    data = body["schedule"]["data"]
    assert data["exists"] is True
    assert data["schedule"]["paused"] is True
    assert data["schedule"]["note"] == "maintenance window"
    assert data["schedule"]["interval"] == 300
    assert data["paused_by"] == "alice"
    assert data["paused_at"] == "2026-11-07T21:00:00Z"
    assert body["worker"]["data"] == {"pollers": 1, "task_queue": "snapl-orchestrator"}


def test_running_schedule_does_not_carry_an_old_pause():
    audit = View(source=SourceName.AUDIT, available=True, fetched_at=T0, data=[_pause()])

    body = _client(audit=audit, temporal=_temporal_ok(_schedule())).get("/api/v1/schedule").json()

    assert body["schedule"]["data"]["paused_by"] is None
    assert body["schedule"]["data"]["paused_at"] is None


def test_no_schedule_suggests_creating_one():
    data = _client().get("/api/v1/schedule").json()["schedule"]["data"]

    assert data["exists"] is False
    assert data["suggested_command"] == "snapl schedule create --use-case dcfabric"


# --- /api/v1/history (T035) -----------------------------------------------------


def _history_client() -> TestClient:
    audit = View(source=SourceName.AUDIT, available=True, fetched_at=T0, data=[*_deploy_run(), _scan_start()])
    return _client(audit=audit)


def test_history_defaults_to_the_last_24_hours_hourly_and_all_four_series():
    body = _history_client().get("/api/v1/history").json()

    series = {s["name"]: s for s in body["series"]}
    assert set(series) == {"drifted_devices", "undesired_paths", "deploys", "repairs"}
    deploys = series["deploys"]
    # The app's clock is T0 + 5 s.
    assert deploys["end"] == "2026-11-07T21:00:05Z"
    assert deploys["start"] == "2026-11-06T21:00:05Z"
    assert deploys["bucket"] == "1h"
    assert series["drifted_devices"]["bucket"] is None
    assert deploys["points"][-1] == {
        "t": "2026-11-07T21:00:00Z",
        "value": None,
        "by_outcome": {"succeeded": 1, "failed": 0},
    }


def test_history_series_is_repeatable():
    body = _history_client().get("/api/v1/history", params=[("series", "deploys"), ("series", "repairs")]).json()

    assert [s["name"] for s in body["series"]] == ["deploys", "repairs"]


def test_history_range_is_never_clamped_to_the_data():
    params = {"series": "deploys", "start": "2020-01-01T00:00:00Z", "end": "2020-01-02T00:00:00Z"}

    [series] = _history_client().get("/api/v1/history", params=params).json()["series"]

    assert series["start"] == "2020-01-01T00:00:00Z"
    assert len(series["points"]) == 24
    assert all(p["by_outcome"] == {"succeeded": None, "failed": None} for p in series["points"])


@pytest.mark.parametrize(
    "params",
    [
        {"start": "2026-11-07T00:00:00", "end": "2026-11-07T10:00:00Z"},
        {"start": "2026-11-07T00:00:00Z", "end": "2026-11-07T10:00:00"},
        {"start": "2026-11-07T10:00:00Z", "end": "2026-11-07T10:00:00Z"},
        {"start": "2026-11-07T11:00:00Z", "end": "2026-11-07T10:00:00Z"},
        {"start": "2026-01-01T00:00:00Z", "end": "2026-11-07T00:00:00Z", "bucket": "5m"},
        {"bucket": "2h"},
        {"series": "uptime"},
    ],
)
def test_history_rejects_bad_ranges(params: dict):
    assert _history_client().get("/api/v1/history", params=params).status_code == 422


# --- /api/v1/lab-runs and /api/v1/ci (T044) -------------------------------------


def test_lab_runs_returns_the_evidence_view():
    body = _client().get("/api/v1/lab-runs").json()

    assert body["contract_version"] == "1"
    lab = body["lab"]
    assert lab["source"] == "lab_runs"
    assert lab["data"]["latest"]["e2e"]["status"] == "passed"
    assert lab["data"]["commits_on_main_since"] == {"e2e": 3}
    assert lab["data"]["matched_by"] == {"e2e": "ancestor"}


def test_ci_returns_the_ci_view():
    body = _client().get("/api/v1/ci").json()

    ci = body["ci"]
    assert ci["source"] == "ci"
    assert ci["data"]["repo"] == "anton-tvrz/snapl"
    assert ci["data"]["targets"][0]["gate"] == "success"
    assert ci["data"]["omitted_prs"] == 0


def test_ci_down_is_a_view_not_an_error():
    down = View(
        source=SourceName.CI, available=False, reason="GitHub could not be reached", remedy="export GITHUB_TOKEN=…"
    )

    response = _client(ci=down).get("/api/v1/ci")

    assert response.status_code == 200
    assert response.json()["ci"]["available"] is False
    assert response.json()["ci"]["remedy"] == "export GITHUB_TOKEN=…"


# =============================================================================
# Failure is data (US5): /api/v1/overview and /api/v1/sources
# =============================================================================


class RaisingSource:
    def __init__(self) -> None:
        self.names: dict = {}

    async def refresh(self) -> View[Any]:
        raise RuntimeError("connection reset by peer")


class HangingSource:
    def __init__(self) -> None:
        self.names: dict = {}

    async def refresh(self) -> View[Any]:
        import asyncio

        await asyncio.sleep(30)
        raise AssertionError("unreachable")


FAST = CliSettings(connect_timeout=0.2)

# Which overview views each source feeds (contracts/dashboard-api.md).
DEPENDENT = {
    SourceName.AUDIT: {"devices", "runs", "schedule"},
    SourceName.SOT: {"devices"},
    SourceName.TEMPORAL: {"running", "schedule", "worker"},
    SourceName.CI: {"ci"},
    SourceName.LAB_RUNS: {"lab"},
    # Feeds /sources and the metrics routes, never the overview (spec 012).
    SourceName.PROMETHEUS: set(),
}
VIEWS = ("devices", "runs", "running", "schedule", "worker", "ci", "lab")
FIELD = {
    SourceName.AUDIT: "audit",
    SourceName.SOT: "sot",
    SourceName.TEMPORAL: "temporal",
    SourceName.CI: "ci",
    SourceName.LAB_RUNS: "lab_runs",
    SourceName.PROMETHEUS: "prometheus",
}


def _app_with(failing: dict[SourceName, Any], settings: CliSettings = FAST) -> TestClient:
    healthy = {
        "audit": FakeSource(_audit_ok()),
        "sot": FakeSource(_sot_ok()),
        "temporal": FakeSource(_temporal_ok(_schedule())),
        "ci": FakeSource(_ci_ok()),
        "lab_runs": FakeSource(_lab_ok()),
        "prometheus": FakePrometheus(),
    }
    for name, source in failing.items():
        healthy[FIELD[name]] = source
    app = create_app(Sources(**healthy), settings, "dcfabric", clock=lambda: T0 + timedelta(seconds=5))
    return TestClient(app)


def _overview(client: TestClient) -> tuple[dict, float]:
    import time

    started = time.perf_counter()
    response = client.get("/api/v1/overview")
    elapsed = time.perf_counter() - started
    assert response.status_code == 200
    return response.json(), elapsed


@pytest.mark.parametrize("failure", [RaisingSource, HangingSource], ids=["raises", "hangs"])
@pytest.mark.parametrize("source", list(SourceName), ids=[name.value for name in SourceName])
def test_one_failing_source_affects_only_its_own_views(source: SourceName, failure):
    healthy, _ = _overview(_app_with({}))

    body, elapsed = _overview(_app_with({source: failure()}))

    assert elapsed < 2
    for view in VIEWS:
        if view in DEPENDENT[source]:
            assert body[view]["available"] is False, view
            assert body[view]["reason"], view
            assert body[view]["remedy"], view
        else:
            assert body[view] == healthy[view], view


def test_every_source_down_is_still_200():
    body, elapsed = _overview(_app_with({name: HangingSource() for name in SourceName}))

    assert elapsed < 2
    assert all(body[view]["available"] is False for view in VIEWS)
    assert all(body[view]["remedy"] for view in VIEWS)


def test_overview_carries_the_envelope_and_honours_runs_limit():
    client = _app_with({})

    body, _ = _overview(client)

    assert body["contract_version"] == "1"
    assert body["use_case"] == "dcfabric"
    assert client.get("/api/v1/overview", params={"runs_limit": 0}).status_code == 422
    assert client.get("/api/v1/overview", params={"runs_limit": 501}).status_code == 422


def test_a_source_that_fails_after_answering_serves_its_last_data_as_stale():
    flaky = FakeSource(_ci_ok())
    client = _app_with({SourceName.CI: flaky})
    first, _ = _overview(client)

    flaky.view = None  # type: ignore[assignment]

    async def boom() -> View[Any]:
        raise RuntimeError("TLS handshake failed")

    flaky.refresh = boom  # type: ignore[method-assign]
    second, _ = _overview(client)

    assert second["ci"]["available"] is False
    assert second["ci"]["stale"] is True
    assert second["ci"]["data"] == first["ci"]["data"]
    assert second["ci"]["fetched_at"] == first["ci"]["fetched_at"]
    assert "TLS handshake failed" in second["ci"]["reason"]


# --- /api/v1/sources (T052) ----------------------------------------------------


def test_sources_lists_every_source_without_data():
    body = _app_with({}).get("/api/v1/sources").json()

    assert [entry["source"] for entry in body["sources"]] == [name.value for name in SourceName]
    assert all(entry["data"] is None for entry in body["sources"])
    assert all(entry["available"] for entry in body["sources"])


def test_sources_reports_the_last_successful_read_of_a_failing_source():
    last_good = T0 - timedelta(minutes=3)
    temporal = View(
        source=SourceName.TEMPORAL,
        available=False,
        stale=True,
        fetched_at=last_good,
        reason="Temporal unreachable",
        remedy="uv run invoke dev.deps",
        data=TemporalSnapshot(running=[], pollers=1, schedule=None),
    )

    body = _app_with({SourceName.TEMPORAL: FakeSource(temporal)}).get("/api/v1/sources").json()

    entry = {e["source"]: e for e in body["sources"]}["temporal"]
    assert entry["available"] is False
    assert entry["fetched_at"] == "2026-11-07T20:57:00Z"
    assert entry["reason"] == "Temporal unreachable"
    assert entry["remedy"] == "uv run invoke dev.deps"
    assert entry["data"] is None


def test_sources_reports_a_source_that_raised():
    body = _app_with({SourceName.SOT: RaisingSource()}).get("/api/v1/sources").json()

    entry = {e["source"]: e for e in body["sources"]}["sot"]
    assert entry["available"] is False
    assert "connection reset by peer" in entry["reason"]
    assert entry["remedy"] == "uv run invoke dev.deps"


# --- the dashboard and the CLI agree (T053, SC-002) -----------------------------


def test_devices_and_schedule_agree_with_snapl_status_and_schedule_show():
    """The same inputs, through the CLI's own commands and through the API."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from typer.testing import CliRunner

    from snapl_intent.infrahub.identity import IdentityCheck, SotIdentity
    from snapl_presentation import cli

    running = [
        RunningWorkflowInfo(
            workflow_id="scan-drift-dcfabric-7",
            workflow_type="ScanDrift",
            start_time=T0,
            task_queue="snapl-orchestrator",
        )
    ]
    schedule = _schedule(paused=True)
    states = [LEAF, SPINE]
    with (
        patch.object(cli, "_connect", AsyncMock(return_value=MagicMock())),
        patch.object(cli, "count_pollers", AsyncMock(return_value=1)),
        patch("snapl_orchestrator.worker.client.list_running_workflows", AsyncMock(return_value=running)),
        patch("snapl_orchestrator.schedules.find_drift_schedule", AsyncMock(return_value=schedule)),
        patch("snapl_orchestrator.schedules.describe_drift_schedule", AsyncMock(return_value=schedule)),
        patch.object(cli, "build_store", return_value=MagicMock()),
        patch.object(cli, "load_states", AsyncMock(return_value=states)),
        patch.object(cli, "identify_sot", AsyncMock(return_value=IdentityCheck(SotIdentity.OURS, "ours"))),
    ):
        status = json.loads(CliRunner().invoke(cli.app, ["status", "--json"]).stdout)
        shown = json.loads(CliRunner().invoke(cli.app, ["schedule", "show", "--json"]).stdout)

    temporal = View(
        source=SourceName.TEMPORAL,
        available=True,
        fetched_at=T0,
        data=TemporalSnapshot(running=running, pollers=1, schedule=schedule),
    )
    sot = View(source=SourceName.SOT, available=True, fetched_at=T0, data=states)
    client = _app_with({SourceName.TEMPORAL: FakeSource(temporal), SourceName.SOT: FakeSource(sot)})
    overview, _ = _overview(client)

    checks = {check["name"]: check for check in status["checks"]}
    assert checks["'dcfabric' seeded"]["detail"] == f"{len(overview['devices']['data'])} devices"
    assert {d["name"] for d in overview["devices"]["data"]} == {s.device.name for s in states}
    assert checks["worker"]["detail"].startswith(f"{overview['worker']['data']['pollers']} polling")
    assert [w["workflow_id"] for w in status["running_workflows"]] == [
        r["workflow_id"] for r in overview["running"]["data"]
    ]
    assert overview["schedule"]["data"]["schedule"] == shown


class SlowSource:
    """Answers after ``delay`` seconds; counts how often it was asked."""

    def __init__(self, view: View[Any], delay: float) -> None:
        self.view = view
        self.delay = delay
        self.calls = 0
        self.names: dict = {}

    async def refresh(self) -> View[Any]:
        import asyncio

        self.calls += 1
        await asyncio.sleep(self.delay)
        return self.view


def test_a_slow_source_is_fetched_once_in_the_background(monkeypatch: pytest.MonkeyPatch):
    """A request waits only a short budget for a slow source. The fetch keeps
    running, and a later request gets its answer, instead of cancelling it and
    starting again on every poll (which would spend GitHub's rate limit)."""
    import time

    from snapl_presentation.dashboard import app as app_module

    monkeypatch.setattr(app_module, "REQUEST_BUDGET_S", 0.2)
    slow = SlowSource(_ci_ok(), delay=0.5)
    # One event loop for every request, as under uvicorn: a TestClient used
    # outside `with` runs each request in a loop of its own.
    with _app_with({SourceName.CI: slow}, settings=CliSettings()) as client:
        first, elapsed = _overview(client)
        time.sleep(0.6)
        second, _ = _overview(client)

    assert elapsed < 2
    assert first["ci"]["available"] is False
    assert "still" in first["ci"]["reason"]
    assert second["ci"]["available"] is True
    assert second["ci"]["data"]["repo"] == "anton-tvrz/snapl"
    assert slow.calls == 1


def test_requests_that_share_one_fetch_all_get_its_answer():
    """The page asks for the overview and the sources at the same moment, so
    several requests wait on one fetch. Each of them gets a 200 with the
    answer; found live in T032, where the second request to finish got a 500."""
    from concurrent.futures import ThreadPoolExecutor

    slow = SlowSource(_ci_ok(), delay=0.1)

    with _app_with({SourceName.CI: slow}) as client, ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.get("/api/v1/overview"), range(4)))

    assert [r.status_code for r in responses] == [200] * 4
    assert all(r.json()["ci"]["available"] for r in responses)
    assert slow.calls == 1


def test_ci_is_not_cut_off_by_the_connect_timeout(monkeypatch: pytest.MonkeyPatch):
    """A cold CI refresh is several GitHub calls in a row, each with its own
    timeout. SNAPL_CONNECT_TIMEOUT bounds connecting to the stack, not that."""
    import time

    from snapl_presentation.dashboard import app as app_module

    monkeypatch.setattr(app_module, "REQUEST_BUDGET_S", 0.1)
    slow = SlowSource(_ci_ok(), delay=0.5)

    with _app_with({SourceName.CI: slow}, settings=CliSettings(connect_timeout=0.3)) as client:
        _overview(client)
        time.sleep(0.6)
        second, _ = _overview(client)

    assert second["ci"]["available"] is True
    assert slow.calls == 1


# --- /api/v1/metrics/{view} (spec 012, T012) -----------------------------------


def test_metrics_returns_the_envelope_and_a_prometheus_view():
    prometheus = FakePrometheus()

    response = _client(prometheus=prometheus).get(
        "/api/v1/metrics/gnmi-duration",
        params={"start": "2026-11-07T20:00:00Z", "end": "2026-11-07T21:00:00Z"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "1"
    assert body["use_case"] == "dcfabric"
    view = body["metrics"]
    assert view["source"] == "prometheus"
    assert view["available"] is True
    assert view["data"]["view"] == "gnmi-duration"
    assert view["data"]["step"] == 15
    [(name, plan)] = prometheus.plans
    assert name is MetricsViewName.GNMI_DURATION
    assert (plan.start, plan.end) == (T0 - timedelta(hours=1), T0)


def test_metrics_defaults_to_the_last_hour_at_15_s():
    prometheus = FakePrometheus()

    assert _client(prometheus=prometheus).get("/api/v1/metrics/worker-up").status_code == 200

    [(_, plan)] = prometheus.plans
    now = T0 + timedelta(seconds=5)
    assert plan.step == 15
    assert plan.end == now - timedelta(seconds=now.timestamp() % 15)
    assert plan.end - plan.start == timedelta(hours=1)


def test_metrics_default_step_keeps_a_long_range_to_about_240_points():
    prometheus = FakePrometheus()

    _client(prometheus=prometheus).get(
        "/api/v1/metrics/worker-up", params={"start": "2026-10-31T21:00:00Z", "end": "2026-11-07T21:00:00Z"}
    )

    [(_, plan)] = prometheus.plans
    assert plan.step == 7 * 24 * 3600 // 240


def test_metrics_unknown_view_is_404():
    assert _client().get("/api/v1/metrics/cpu").status_code == 404


@pytest.mark.parametrize(
    "params",
    [
        {"start": "2026-11-07T21:00:00Z", "end": "2026-11-07T21:00:00Z"},
        {"start": "2026-11-07T21:00:00Z", "end": "2026-11-07T20:00:00Z"},
        {"start": "2026-11-07T20:00:00", "end": "2026-11-07T21:00:00Z"},
        {"start": "2025-10-01T00:00:00Z", "end": "2026-11-07T21:00:00Z"},
        {"step": "0"},
        {"use_case": "campus"},
    ],
    ids=["empty", "reversed", "naive", "over-400-days", "zero-step", "other-use-case"],
)
def test_metrics_rejects_a_bad_request(params: dict[str, str]):
    prometheus = FakePrometheus()

    assert _client(prometheus=prometheus).get("/api/v1/metrics/gnmi-duration", params=params).status_code == 422
    assert prometheus.plans == []


def test_metrics_with_prometheus_down_is_200_unavailable_with_the_remedy():
    from snapl_presentation.dashboard.sources import SourceUnavailable

    failing = FakePrometheus(fail=SourceUnavailable("Prometheus not reachable at http://x:1", "uv run invoke dev.deps"))

    response = _client(prometheus=failing).get("/api/v1/metrics/gnmi-errors")

    assert response.status_code == 200
    view = response.json()["metrics"]
    assert view["available"] is False
    assert view["reason"] == "Prometheus not reachable at http://x:1"
    assert view["remedy"] == "uv run invoke dev.deps"
    assert view["data"] is None


def test_metrics_with_prometheus_raising_anything_else_is_still_a_view():
    response = _client(prometheus=FakePrometheus(fail=RuntimeError("boom"))).get("/api/v1/metrics/gnmi-errors")

    view = response.json()["metrics"]
    assert response.status_code == 200
    assert view["available"] is False
    assert "boom" in view["reason"]
    assert view["remedy"] == "uv run invoke dev.deps"


def test_metrics_keeps_the_last_good_answer_for_the_same_range_as_stale():
    from snapl_presentation.dashboard.sources import SourceUnavailable

    prometheus = FakePrometheus()
    client = _client(prometheus=prometheus)
    params = {"start": "2026-11-07T20:00:00Z", "end": "2026-11-07T21:00:00Z"}
    first = client.get("/api/v1/metrics/gnmi-duration", params=params).json()["metrics"]

    prometheus.fail = SourceUnavailable("bad_data: query timed out", "check Prometheus")
    second = client.get("/api/v1/metrics/gnmi-duration", params=params).json()["metrics"]
    other_view = client.get("/api/v1/metrics/gnmi-errors", params=params).json()["metrics"]

    assert second["available"] is False
    assert second["stale"] is True
    assert second["data"] == first["data"]
    assert second["reason"] == "bad_data: query timed out"
    assert other_view["stale"] is False
    assert other_view["data"] is None


def test_metrics_requests_never_choose_the_promql():
    """FR-011: only start, end and step come from the request. Through the real
    client, an extra `query` parameter changes nothing Prometheus sees."""
    import httpx

    from snapl_presentation.dashboard.prometheus import PrometheusSource

    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": []}})

    real = PrometheusSource(
        "http://prom.test:9090", "default", "dcfabric", transport=httpx.MockTransport(answer), clock=lambda: T0
    )
    client = _client(prometheus=real)

    client.get("/api/v1/metrics/worker-up", params={"query": "up", "match[]": "x"})

    [request] = seen
    assert dict(request.url.params)["query"] == 'up{job="snapl-worker"}'
    assert set(request.url.params) == {"query", "start", "end", "step"}


def test_overview_never_reads_prometheus_but_sources_does():
    calls: list[str] = []

    class Counting(FakePrometheus):
        async def refresh(self) -> View[Any]:
            calls.append("refresh")
            return await super().refresh()

    client = _app_with({SourceName.PROMETHEUS: Counting()})

    body, _ = _overview(client)
    assert "metrics" not in body
    assert calls == []

    entry = {e["source"]: e for e in client.get("/api/v1/sources").json()["sources"]}["prometheus"]
    assert calls == ["refresh"]
    assert entry["available"] is True


# --- GET /metrics, the dashboard's own exposition (spec 012, T018) -------------

_SAMPLE = re.compile(r'^(snapl_dashboard_source_[a-z_]+)\{source="([a-z_]+)"\} (\S+)$', re.MULTILINE)


def _samples(text: str) -> dict[tuple[str, str], float]:
    return {(name, source): float(value) for name, source, value in _SAMPLE.findall(text)}


def test_metrics_exposition_is_prometheus_text_and_reflects_the_last_reads():
    client = _app_with({SourceName.SOT: RaisingSource()})
    client.get("/api/v1/sources")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    samples = _samples(response.text)
    up = {source: value for (name, source), value in samples.items() if name == "snapl_dashboard_source_up"}
    assert up == {name.value: (0.0 if name is SourceName.SOT else 1.0) for name in SourceName}
    durations = [v for (name, _), v in samples.items() if name.endswith("_fetch_duration_milliseconds")]
    assert len(durations) == len(SourceName)
    assert all(v >= 0 for v in durations)


def test_metrics_exposition_omits_sources_not_yet_read():
    client = _app_with({})

    assert _samples(client.get("/metrics").text) == {}

    client.get("/api/v1/ci")
    assert {source for _, source in _samples(client.get("/metrics").text)} == {"ci"}


def test_metrics_exposition_costs_no_fetch():
    slow = SlowSource(_ci_ok(), delay=0)
    client = _app_with({SourceName.CI: slow})
    client.get("/api/v1/ci")

    client.get("/metrics")
    client.get("/metrics")

    assert slow.calls == 1


def test_a_metrics_query_counts_as_a_prometheus_read():
    from snapl_presentation.dashboard.sources import SourceUnavailable

    client = _client(prometheus=FakePrometheus(fail=SourceUnavailable("bad_data: nope", "check it")))
    client.get("/api/v1/metrics/worker-up")

    assert _samples(client.get("/metrics").text)[("snapl_dashboard_source_up", "prometheus")] == 0.0


@pytest.mark.parametrize("method", ["HEAD", "POST", "PUT", "PATCH", "DELETE"])
def test_metrics_exposition_only_answers_get(method: str):
    assert _client().request(method, "/metrics").status_code == 405


def test_metrics_exposition_is_not_in_the_openapi_document():
    paths = _client().get("/api/v1/openapi.json").json()["paths"]

    assert "/metrics" not in paths


# --- the page's Metrics section (spec 012, T019) --------------------------------


def test_page_fetches_every_metrics_view():
    page = _client().get("/").text

    assert "/api/v1/metrics/" in page
    for view in MetricsViewName:
        assert f'"{view.value}"' in page, view


def test_page_has_a_range_selector_for_1h_24h_and_7d():
    page = _client().get("/").text

    for value in ("1h", "24h", "7d"):
        assert f'<option value="{value}"' in page


def test_page_remembers_the_range_only_inside_try_catch():
    """localStorage can throw (private windows, blocked storage); the page must not."""
    page = _client().get("/").text

    uses = [m.start() for m in re.finditer(r"localStorage", page)]
    assert uses
    for at in uses:
        before = page[:at]
        assert before.rfind("try {") > before.rfind("}"), "every localStorage use sits directly in a try block"
