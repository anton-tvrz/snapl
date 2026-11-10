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
}

# The same arc with the other sources up (the server's --sources up), for the
# panels that read them: an active schedule, one worker, a scan running, CI on
# main and two PRs, and lab runs of both suites.
UP_REQUESTS = {
    "up/overview.json": "/api/v1/overview",
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


def test_the_up_server_has_temporal_up_and_the_default_server_does_not(client: TestClient, client_up: TestClient):
    assert client.get("/api/v1/overview").json()["schedule"]["available"] is False
    up = client_up.get("/api/v1/overview").json()
    assert up["schedule"]["available"] is True
    assert up["worker"]["data"]["pollers"] == 1
    assert [run["workflow_type"] for run in up["running"]["data"]] == ["ScanDrift"]
    assert [target["kind"] for target in up["ci"]["data"]["targets"]] == ["main", "pull_request", "pull_request"]
    assert set(up["lab"]["data"]["latest"]) == {"e2e", "integration"}


def test_fixture_answers_are_the_same_on_every_run(client: TestClient):
    """Nothing in them depends on when the test runs."""
    assert _body(client, "/api/v1/overview") == _body(client, "/api/v1/overview")
    assert "2026-11-07" in _body(client, "/api/v1/sources")


def test_there_is_no_fixture_file_without_a_request():
    found = {str(path.relative_to(FIXTURES)) for path in FIXTURES.rglob("*.json")}
    assert found == set(REQUESTS) | set(UP_REQUESTS)
