"""FR-019 (spec 015 T042): a scan's new step events change no existing answer.

The captured arc is written again with the events a scan records since spec 015
added (``with_scan_steps``). Every request the UI's fixtures make is asked of a
server on each trail, and the answers must be the same. Only a scan's own steps
may differ: that is the point of the events.

To run every existing suite on the augmented trail, write it with
``write_arc_with_scan_steps`` and set ``SNAPL_ARC_FIXTURE`` to its path.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from snapl_orchestrator.models import AuditEventType
from tests.unit.test_presentation.dashboard_fakes import (
    ARC_FIXTURE,
    arc_events,
    with_scan_steps,
    write_arc_with_scan_steps,
)
from tests.unit.test_presentation.test_ui_fixtures import REQUESTS, ROOT, UP_REQUESTS, _server

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

# Every answer the UI's fixtures hold, and the runs list in full.
ANSWERS = {
    **REQUESTS,
    **{name: path for name, path in UP_REQUESTS.items() if "/metrics/" not in path},
    "runs": "/api/v1/runs?limit=200",
    # The runs that touched one device: a scan's steps target the device too.
    "runs-of-a-device": "/api/v1/runs?limit=200&device=spine-01",
}


@pytest.fixture(scope="module")
def augmented(tmp_path_factory: pytest.TempPathFactory) -> Path:
    import asyncio

    return asyncio.run(write_arc_with_scan_steps(tmp_path_factory.mktemp("arc") / "arc-with-scan-steps.sqlite"))


def _answer(path: Path, request: str, *, up: bool) -> object:
    server = _server()
    with TestClient(server.build_app(ui_dir=ROOT / "no-built-ui", up=up, audit_path=path)) as client:
        response = client.get(request)
    return response.status_code, json.loads(response.text)


async def test_the_augmented_trail_adds_one_intent_step_per_scan_and_one_collect_per_check() -> None:
    events = await arc_events()
    scans = [e for e in events if e.workflow_type == "ScanDrift" and e.event_type is AuditEventType.WORKFLOW_STARTED]
    checks = [e for e in events if e.activity_name == "detect_drift" and e.workflow_type == "ScanDrift"]

    added = [e for e in with_scan_steps(events) if e.event_id not in {old.event_id for old in events}]

    assert scans
    assert sorted(e.activity_name for e in added) == sorted(
        ["fetch_devices_for_use_case"] * len(scans) + ["collect_drift_state"] * len(checks)
    )


@pytest.mark.parametrize("name", sorted(ANSWERS))
def test_every_existing_answer_is_the_same_with_the_scan_steps(augmented: Path, name: str) -> None:
    up = name.startswith("up/")
    status, _ = _answer(ARC_FIXTURE, ANSWERS[name], up=up)

    assert status == 200
    assert _answer(augmented, ANSWERS[name], up=up) == _answer(ARC_FIXTURE, ANSWERS[name], up=up)


async def test_a_scan_of_the_augmented_arc_shows_its_steps(augmented: Path) -> None:
    """The events are seen where they should be: on the scan's own loop strip."""
    events = await arc_events()
    scan = min(
        (e for e in events if e.workflow_type == "ScanDrift" and e.event_type is AuditEventType.WORKFLOW_STARTED),
        key=lambda e: e.timestamp,
    )
    started = scan.timestamp.isoformat().replace("+00:00", "Z")
    request = f"/api/v1/runs/steps?workflow_id={scan.workflow_id}&started_at={started}"

    _, before = _answer(ARC_FIXTURE, request, up=False)
    _, after = _answer(augmented, request, up=False)

    assert before["steps"]["data"]["recorded"] is False
    assert after["steps"]["data"]["recorded"] is True
    assert [b["status"] for b in after["steps"]["data"]["blocks"][:4]] == ["done", "not_reached", "done", "done"]
