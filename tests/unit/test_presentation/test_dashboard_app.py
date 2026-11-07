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
from snapl_orchestrator.models import AuditEvent, AuditEventType
from snapl_presentation.dashboard.app import create_app
from snapl_presentation.dashboard.models import SourceName, View
from snapl_presentation.dashboard.sources import Sources
from snapl_presentation.settings import CliSettings

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 7, 21, 0, tzinfo=UTC)
SNAPSHOT = Path(__file__).parent / "fixtures" / "openapi_v1.json"

# The routes PR B serves. Later phases add runs, schedule, history, ci,
# lab-runs, overview and sources (contracts/dashboard-api.md).
CONTRACT_ROUTES = {
    ("/", "GET"),
    ("/api/v1/devices", "GET"),
    ("/api/v1/openapi.json", "GET"),
    ("/api/v1/openapi.json", "HEAD"),
}


class FakeSource:
    def __init__(self, view: View[Any], names: dict | None = None) -> None:
        self.view = view
        self.names = names or {}

    async def refresh(self) -> View[Any]:
        return self.view


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


def _client(audit: View[Any] | None = None, sot: View[Any] | None = None, names: dict | None = None) -> TestClient:
    sources = Sources(audit=FakeSource(audit or _audit_ok()), sot=FakeSource(sot or _sot_ok(), names))
    app = create_app(sources, CliSettings(), "dcfabric", clock=lambda: T0 + timedelta(seconds=5))
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


@pytest.mark.parametrize("method", ["HEAD", "POST", "PUT", "PATCH", "DELETE"])
def test_other_methods_on_devices_are_refused(method: str):
    assert _client().request(method, "/api/v1/devices").status_code == 405


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
    assert "/api/v1/devices" in response.text
    assert re.findall(r"https?://", response.text) == []


def test_page_never_renders_through_innerhtml():
    """Audit payloads hold device-supplied strings (research R11)."""
    assert "innerHTML" not in _client().get("/").text
