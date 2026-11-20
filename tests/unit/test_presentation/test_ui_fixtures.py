"""The UI's test fixtures are what the server really answers (spec 011).

The UI's component tests render API responses saved under
``packages/presentation/ui/src/test/fixtures``. They are written from the real
app on the captured demo arc (dev/scripts/serve_dashboard_fixtures.py), and this
test fails when a file no longer matches what the app answers, so a UI test can
never pass against a response shape the server has stopped sending.

To rewrite them after an intended change:

    SNAPL_WRITE_UI_FIXTURES=1 uv run pytest tests/unit/test_presentation/test_ui_fixtures.py
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from snapl_presentation.dashboard.models import MetricsViewName
from snapl_presentation.dashboard.prometheus import VIEWS

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "packages" / "presentation" / "ui" / "src" / "test" / "fixtures"
SERVER = ROOT / "dev" / "scripts" / "serve_dashboard_fixtures.py"

# file name -> the request that produces it.
REQUESTS = {
    "mode.json": "/api/v1/mode",
    "sources.json": "/api/v1/sources",
    "overview.json": "/api/v1/overview",
    "topology.json": "/api/v1/topology",
    "devices.json": "/api/v1/devices",
    "timeline.json": "/api/v1/drift-timeline",
    # The "as of" stops just after each scan of the arc: the latest point of the
    # scan, rounded up to the next millisecond (the UI's lib/lanes.ts scanStops).
    # spine-01 is drifted after scans 1 and 2 and in sync after scan 3.
    "devices-at-scan-1.json": "/api/v1/devices?at=2026-11-07T21:20:13.666Z",
    "devices-at-scan-2.json": "/api/v1/devices?at=2026-11-07T21:20:15.330Z",
    "devices-at-scan-3.json": "/api/v1/devices?at=2026-11-07T21:20:18.853Z",
    "runs-at-scan-2.json": "/api/v1/runs?at=2026-11-07T21:20:15.330Z",
    # The headline as of scan 2: nothing repaired yet, two episodes open.
    "timeline-at-scan-2.json": "/api/v1/drift-timeline?start=2026-11-06T21:20:48.919877Z&end=2026-11-07T21:20:15.330Z",
    # The loop strip: the reconcile's deploy of spine-01 (every step recorded)
    # and the reconcile itself (its steps are not recorded one by one).
    "steps-deploy.json": "/api/v1/runs/steps?workflow_id=deploy-intent-18e29d6d-e0c5-0ba7-32d1-c510fc3ef077"
    "&started_at=2026-11-07T21:20:15.648265Z",
    "steps-reconcile.json": "/api/v1/runs/steps?workflow_id=reconcile-217b6000-0e6c-4919-81d9-9f6a1075cd1d"
    "&started_at=2026-11-07T21:20:15.472996Z",
    "links.json": "/api/v1/links",
    # 010's history over the server's default range, hourly: the plain page's table.
    "history.json": "/api/v1/history",
    # Spec 015: drift by device over the server's default range.
    "drift-summary.json": "/api/v1/drift-summary",
    # Spec 016: each device's health and its unhealthy episodes over the default range.
    "health.json": "/api/v1/health",
    # Spec 015: the fabric history's four series, named.
    "fabric-history.json": (
        "/api/v1/history?series=in_sync_devices&series=open_drifts&series=runs&series=repair_duration"
    ),
}

# The same arc with the other sources up (the server's --sources up), for the
# panels that read them: an active schedule, one worker, a scan running, CI on
# main and two PRs, and lab runs of both suites.
UP_REQUESTS = {
    "up/overview.json": "/api/v1/overview",
    "up/sources.json": "/api/v1/sources",
    # Spec 016: intent's interfaces and sessions with the store's synthetic figures.
    "up/network.json": "/api/v1/network",
    # Each metrics view over the server's default range: the hour before NOW.
    **{f"up/metrics-{view.value}.json": f"/api/v1/metrics/{view.value}" for view in MetricsViewName},
}


def _server():
    spec = importlib.util.spec_from_file_location("serve_dashboard_fixtures", SERVER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def client() -> TestClient:
    # No UI directory: only the API is asked.
    return TestClient(_server().build_app(ui_dir=ROOT / "no-built-ui"))


# The arc with the step events a scan records since spec 015 (--scan-steps): the
# loop strip of the third scan, every device collected and checked.
SCAN_STEPS_REQUESTS = {
    "scan-steps/steps-scan.json": "/api/v1/runs/steps"
    "?workflow_id=scan-drift-dcfabric-68101896-6a9c-4c65-8a84-24af2b98b12a"
    "&started_at=2026-11-07T21:20:17.860408Z",
}


@pytest.fixture(scope="module")
def client_scan_steps(tmp_path_factory: pytest.TempPathFactory):
    import asyncio

    from tests.unit.test_presentation.dashboard_fakes import write_arc_with_scan_steps

    path = asyncio.run(write_arc_with_scan_steps(tmp_path_factory.mktemp("scan-steps") / "arc.sqlite"))
    return TestClient(_server().build_app(ui_dir=ROOT / "no-built-ui", audit_path=path))


# The same arc as a history archive opened by `snapl dashboard --archive`
# (test_dashboard_archive.build_archive), for the UI's archive mode.
ARCHIVE_REQUESTS = {
    "archive/mode.json": "/api/v1/mode",
    "archive/overview.json": "/api/v1/overview",
}


@pytest.fixture(scope="module")
def client_archive(tmp_path_factory: pytest.TempPathFactory):
    from snapl_presentation.dashboard.archive import create_archive_app, open_archive
    from tests.unit.test_presentation.test_dashboard_archive import build_archive

    parts = [{"name": "metrics_snapshot", "path": "prometheus", "present": False, "source": "x", "reason": "down"}]
    path = build_archive(tmp_path_factory.mktemp("archive"), manifest={"parts": parts}, prometheus=False)
    with open_archive(path) as opened:
        yield TestClient(create_archive_app(opened, ui_dir=ROOT / "no-built-ui"))


@pytest.fixture(scope="module")
def client_up() -> TestClient:
    return TestClient(_server().build_app(ui_dir=ROOT / "no-built-ui", up=True))


def _body(client: TestClient, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200, response.text
    body = response.json()
    return json.dumps(body, indent=2, sort_keys=True) + "\n"


def _check(expected: str, name: str) -> None:
    path = FIXTURES / name

    if os.environ.get("SNAPL_WRITE_UI_FIXTURES"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected)

    assert path.is_file(), f"{name} is missing: see this module's docstring"
    assert path.read_text() == expected, f"{name} is out of date: see this module's docstring"


@pytest.mark.parametrize("name", sorted(REQUESTS))
def test_ui_fixture_is_what_the_server_answers(client: TestClient, name: str):
    _check(_body(client, REQUESTS[name]), name)


@pytest.mark.parametrize("name", sorted(UP_REQUESTS))
def test_ui_fixture_with_every_source_up_is_what_the_server_answers(client_up: TestClient, name: str):
    _check(_body(client_up, UP_REQUESTS[name]), name)


def test_ui_fixture_snapshot_bundle_is_what_the_command_collects(client_up: TestClient):
    """A day of the arc with every source up, as `snapl dashboard --snapshot --days 1` bundles it."""
    from snapl_presentation.dashboard.snapshot import collect_bundle

    bundle = collect_bundle(client_up.app, days=1, commit="6baff39", source="live")
    _check(json.dumps(bundle, indent=2, sort_keys=True) + "\n", "snapshot/bundle.json")


@pytest.mark.parametrize("name", sorted(SCAN_STEPS_REQUESTS))
def test_ui_fixture_with_scan_steps_is_what_the_server_answers(client_scan_steps: TestClient, name: str):
    _check(_body(client_scan_steps, SCAN_STEPS_REQUESTS[name]), name)


@pytest.mark.parametrize("name", sorted(ARCHIVE_REQUESTS))
def test_ui_fixture_in_archive_mode_is_what_the_server_answers(client_archive: TestClient, name: str):
    _check(_body(client_archive, ARCHIVE_REQUESTS[name]), name)


def test_the_up_server_has_temporal_up_and_the_default_server_does_not(client: TestClient, client_up: TestClient):
    assert client.get("/api/v1/overview").json()["schedule"]["available"] is False
    up = client_up.get("/api/v1/overview").json()
    assert up["schedule"]["available"] is True
    assert up["worker"]["data"]["pollers"] == 1
    assert [run["workflow_type"] for run in up["running"]["data"]] == ["ScanDrift"]
    assert [target["kind"] for target in up["ci"]["data"]["targets"]] == ["main", "pull_request", "pull_request"]
    assert set(up["lab"]["data"]["latest"]) == {"e2e", "integration"}
    metrics = client_up.get("/api/v1/metrics/workflow-duration").json()["metrics"]
    assert metrics["available"] is True
    assert {series["labels"]["workflow_type"] for series in metrics["data"]["series"]} == {"DeployIntent", "ScanDrift"}
    assert client.get("/api/v1/metrics/workflow-duration").json()["metrics"]["available"] is False


@pytest.mark.parametrize("view", list(MetricsViewName))
def test_the_up_server_answers_every_view_with_its_own_labels_and_unit(client_up: TestClient, view: MetricsViewName):
    """Spec 015 T023: synthetic series for every view, with research R2's label sets."""
    data = client_up.get(f"/api/v1/metrics/{view.value}").json()["metrics"]["data"]

    assert data["unit"] == VIEWS[view].unit
    assert data["series"]
    assert all(set(series["labels"]) == set(VIEWS[view].labels) for series in data["series"])
    assert len(data["series"]) <= _SERIES_ON_DCFABRIC.get(view, 6)  # the palette's limit (research R2)


# Spec 016's network views over dcfabric, with the label sets contracts/telemetry.md
# gives: errors and discards have more series than the palette's six slots, and
# the page draws the six highest (#312, 2026-11-20).
_SERIES_ON_DCFABRIC = {
    MetricsViewName.INTERFACE_TRAFFIC: 6,  # one per device, both directions summed
    MetricsViewName.INTERFACE_ERRORS: 16,  # 8 links, both ends
    MetricsViewName.INTERFACE_DISCARDS: 16,
    MetricsViewName.BGP_ESTABLISHED: 6,
    MetricsViewName.DEVICE_CPU: 6,
    MetricsViewName.DEVICE_MEMORY: 6,
}


@pytest.mark.parametrize("view", sorted(_SERIES_ON_DCFABRIC))
def test_the_up_server_draws_the_network_views_with_the_contracts_series(client_up: TestClient, view):
    data = client_up.get(f"/api/v1/metrics/{view.value}").json()["metrics"]["data"]

    assert len(data["series"]) == _SERIES_ON_DCFABRIC[view]


def test_the_up_server_counts_each_step_with_no_window_note(client_up: TestClient):
    """A count's point covers its own step, so it names no longer window (#308)."""
    counted = client_up.get("/api/v1/metrics/workflows-completed").json()["metrics"]["data"]
    gauge = client_up.get("/api/v1/metrics/worker-slots").json()["metrics"]["data"]

    assert counted["note"] is None
    assert gauge["note"] is None


def test_fixture_answers_are_the_same_on_every_run(client: TestClient):
    """Nothing in them depends on when the test runs."""
    assert _body(client, "/api/v1/overview") == _body(client, "/api/v1/overview")
    assert "2026-11-07" in _body(client, "/api/v1/sources")


def test_there_is_no_fixture_file_without_a_request():
    found = {str(path.relative_to(FIXTURES)) for path in FIXTURES.rglob("*.json")}
    assert found == (
        set(REQUESTS) | set(UP_REQUESTS) | set(ARCHIVE_REQUESTS) | set(SCAN_STEPS_REQUESTS) | {"snapshot/bundle.json"}
    )


# --- Health (spec 016 T033) ---------------------------------------------------------


def test_the_fixture_server_shows_todays_lab_health(client: TestClient):
    """spine-01 and spine-02 unhealthy for their sessions, leaf-01 unhealthy for an
    hour then healthy, leaf-03 unknown for two minutes, the rest healthy."""
    health = client.get("/api/v1/health").json()["health"]["data"]
    by_name = {device["name"]: device for device in health["devices"]}

    assert list(by_name) == ["leaf-01", "leaf-02", "leaf-03", "leaf-04", "spine-01", "spine-02"]
    for spine, subnet in (("spine-01", "10.10.1"), ("spine-02", "10.10.2")):
        current = by_name[spine]["current"]
        assert current["state"] == "unhealthy"
        assert [r["text"] for r in current["reasons"]] == [
            f"BGP {subnet}.{n} active (expected established)" for n in (1, 3, 5, 7)
        ]
        [episode] = by_name[spine]["episodes"]
        assert episode["closed_at"] is None
    [episode] = by_name["leaf-01"]["episodes"]
    assert episode["duration_s"] == 3600
    assert [r["text"] for r in episode["reasons"]] == ["ethernet-1/49 down (expected up)"]
    for name in ("leaf-01", "leaf-02", "leaf-03", "leaf-04"):
        assert by_name[name]["current"]["state"] == "healthy", name
    assert by_name["leaf-03"]["episodes"] == []  # unknown opens no episode


def test_the_fixture_health_has_leaf_03_unknown_for_two_minutes():
    from tests.unit.test_presentation.dashboard_fakes import ARC, health_events

    leaf_03 = [e for e in health_events() if e.target_id == ARC["leaf-03"][1]]

    assert [e.payload["after"] for e in leaf_03] == ["healthy", "unknown", "healthy"]
    assert (leaf_03[2].timestamp - leaf_03[1].timestamp).total_seconds() == 120
    assert [r["kind"] for r in leaf_03[1].payload["reasons"]] == ["no_telemetry"]


def test_the_fixture_health_is_shaped_like_the_runners():
    """Each event is what TelemetryRunner records: its workflow, actor and payload keys."""
    from tests.unit.test_presentation.dashboard_fakes import health_events

    for event in health_events():
        assert event.workflow_type == "DeviceTelemetry"
        assert event.workflow_id.startswith("telemetry:")
        assert event.actor == "worker"
        assert set(event.payload) == {"before", "after", "reasons", "last_sample_at"}


# --- The network (spec 016 T039) ------------------------------------------------------


def _network(client: TestClient) -> dict:
    return {d["name"]: d for d in client.get("/api/v1/network").json()["network"]["data"]["devices"]}


def test_the_up_server_lists_spine_01s_five_interfaces_and_four_sessions(client_up: TestClient):
    """The phase's checkpoint."""
    spine = _network(client_up)["spine-01"]

    assert [i["name"] for i in spine["interfaces"]] == [
        "ethernet-1/1",
        "ethernet-1/2",
        "ethernet-1/3",
        "ethernet-1/4",
        "lo0",
    ]
    assert [s["peer"] for s in spine["sessions"]] == ["10.10.1.1", "10.10.1.3", "10.10.1.5", "10.10.1.7"]


def test_the_up_server_has_traffic_on_every_link_and_errors_on_one_interface(client_up: TestClient):
    network = _network(client_up)
    links = [(d, i) for d, device in network.items() for i in device["interfaces"] if i["name"] != "lo0"]

    assert len(links) == 16  # 8 links, both ends
    assert all(i["up"] and i["in_bps"] > 0 and i["out_bps"] > 0 for _, i in links)
    erring = [(d, i["name"]) for d, i in links if (i["in_errors_ps"] or 0) > 0 or (i["out_errors_ps"] or 0) > 0]
    assert erring == [("leaf-02", "ethernet-1/49")]
    loopbacks = [i for device in network.values() for i in device["interfaces"] if i["name"] == "lo0"]
    assert all(i["up"] and i["in_bps"] is None for i in loopbacks)  # a loopback has no counters


def test_the_up_server_has_the_spines_sessions_down(client_up: TestClient):
    network = _network(client_up)

    for name, device in network.items():
        established = {s["established"] for s in device["sessions"]}
        assert established == ({False} if name.startswith("spine") else {True}), name
    assert all(s["prefixes_received"] == 0 for s in network["spine-01"]["sessions"])


def test_the_up_server_has_cpu_and_memory_as_the_lab_reports_them(client_up: TestClient):
    """The containers report the host VM: memory 94 % on every node, CPU 3 to 5 (research R1)."""
    network = _network(client_up)

    assert {d["memory_percent"] for d in network.values()} == {94.0}
    assert all(3 <= d["cpu_percent"] <= 5 for d in network.values())


def test_the_up_server_draws_the_spines_at_zero_sessions(client_up: TestClient):
    series = client_up.get("/api/v1/metrics/bgp-established").json()["metrics"]["data"]["series"]
    last = {s["labels"]["device"]: s["points"][-1]["value"] for s in series}

    assert last == {"spine-01": 0, "spine-02": 0, "leaf-01": 2, "leaf-02": 2, "leaf-03": 2, "leaf-04": 2}


def test_the_default_server_has_no_network(client: TestClient):
    view = client.get("/api/v1/network").json()["network"]

    assert (view["available"], view["data"]) == (False, None)
    assert view["reason"] == "prometheus is not part of the fixture server"
