"""The dashboard against a real Temporal dev server and a real audit trail (spec 010 T057).

The unit tests build the app over fake sources. This one runs a real
`ScanDrift` on a local Temporal dev server, with the four downstream blocks
mocked and the audit trail written to a temporary SQLite file, then serves the
dashboard over those real sources and reads the overview. It needs no lab:
`start_local` downloads the dev-server binary on first use, and the test skips
if that is not possible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pydantic_core  # noqa: F401 — pre-import to keep the workflow sandbox quiet
import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from snapl_collector.models import CollectResult
from snapl_intent.infrahub.identity import IdentityCheck, SotIdentity
from snapl_intent.models import DesiredState, Device
from snapl_observability.models import DriftItem, DriftReport, DriftStatus
from snapl_orchestrator.activities import Activities, set_activities
from snapl_orchestrator.activities.audit import record_audit_event
from snapl_orchestrator.activities.collector import collect_drift_state
from snapl_orchestrator.activities.intent import fetch_desired_state, fetch_devices_for_use_case
from snapl_orchestrator.activities.observability import detect_drift
from snapl_orchestrator.audit.sqlite import SqliteAuditLog
from snapl_orchestrator.worker.sandbox import build_workflow_runner
from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow
from snapl_presentation.dashboard.app import create_app
from snapl_presentation.dashboard.lab_runs import LabRunSource
from snapl_presentation.dashboard.models import SourceName, View
from snapl_presentation.dashboard.prometheus import PrometheusSource
from snapl_presentation.dashboard.sources import AuditSource, SotSource, Sources, TemporalSource
from snapl_presentation.settings import CliSettings

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

TASK_QUEUE = "snapl-dashboard-it"
USE_CASE = "dcfabric-it"
UNDESIRED = "/interface[name=ethernet-1/7]/description"


def _state(name: str, role: str) -> DesiredState:
    return DesiredState(
        device=Device(id=uuid4(), name=name, management_address="10.0.0.1", role=role, use_case=USE_CASE)
    )


class _NoCi:
    names: dict = {}  # noqa: RUF012 — never mutated

    async def refresh(self) -> View:
        return View(source=SourceName.CI, available=False, reason="not asked in this test", remedy="none")


@pytest.fixture
async def local_env():
    try:
        env = await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter)
    except Exception as exc:
        pytest.skip(f"Temporal dev server unavailable: {exc}")
    async with env:
        yield env


async def test_overview_shows_a_real_scan_and_its_device_state(local_env, tmp_path: Path):
    spine, leaf = _state("spine-01", "spine"), _state("leaf-01", "leaf")
    states = [spine, leaf]
    audit_path = tmp_path / "audit.db"
    writer = SqliteAuditLog(database_url=str(audit_path))
    await writer.initialize()

    store = MagicMock()
    by_id = {s.device.id: s for s in states}

    async def get_desired_state(*, use_case=None, device_id=None):
        if device_id is not None:
            return [by_id[device_id]]
        return states

    store.get_desired_state = AsyncMock(side_effect=get_desired_state)
    collector = MagicMock()
    collected = lambda device, *_: CollectResult(  # noqa: E731
        device_id=device.id, device_name=device.name, success=True, data={}, paths=["/interface"]
    )
    collector.get_running_config = AsyncMock(side_effect=collected)
    collector.collect = AsyncMock(side_effect=collected)

    def detect(desired, _collected) -> DriftReport:
        drifted = desired.device.id == leaf.device.id
        items = [DriftItem(path=UNDESIRED, desired=None, actual="hand-added", entity_kind="Interface", undesired=True)]
        return DriftReport(
            device_id=desired.device.id,
            device_name=desired.device.name,
            status=DriftStatus.DRIFTED if drifted else DriftStatus.CLEAN,
            items=items if drifted else [],
            timestamp=datetime.now(UTC),
        )

    observer = MagicMock()
    observer.detect_drift = AsyncMock(side_effect=detect)
    observer.emit_event = AsyncMock()
    set_activities(
        Activities(intent_store=store, executor=MagicMock(), collector=collector, observer=observer, audit_log=writer)
    )

    settings = CliSettings(
        temporal_host=local_env.client.service_client.config.target_host,
        temporal_namespace=local_env.client.namespace,
        task_queue=TASK_QUEUE,
        audit_db=str(audit_path),
        connect_timeout=5,
    )

    async def identify(_store, *, address: str) -> IdentityCheck:
        return IdentityCheck(SotIdentity.OURS, f"{address} carries snapl's schema")

    sources = Sources(
        audit=AuditSource(str(audit_path)),
        sot=SotSource(settings, USE_CASE, build_store=lambda _settings: store, identify=identify),
        temporal=TemporalSource(settings, USE_CASE),
        ci=_NoCi(),
        lab_runs=LabRunSource(tmp_path / "test-runs", tmp_path),
        # Nothing listens on port 1: the overview must not depend on metrics (spec 012).
        prometheus=PrometheusSource("http://127.0.0.1:1", settings.temporal_namespace, USE_CASE, timeout=1),
    )
    app = create_app(sources, settings, USE_CASE)

    async with Worker(
        local_env.client,
        task_queue=TASK_QUEUE,
        workflows=[ScanDriftWorkflow],
        workflow_runner=build_workflow_runner(),
        activities=[
            fetch_devices_for_use_case,
            fetch_desired_state,
            collect_drift_state,
            detect_drift,
            record_audit_event,
        ],
    ):
        result = await local_env.client.execute_workflow(
            ScanDriftWorkflow.run, USE_CASE, id=f"scan-drift-{USE_CASE}-{uuid4()}", task_queue=TASK_QUEUE
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dashboard") as http:
            overview = (await http.get("/api/v1/overview")).json()
    await writer.close()

    assert result.drifted == 1
    runs = overview["runs"]["data"]
    assert [(r["workflow_type"], r["state"], r["trigger"]) for r in runs] == [("ScanDrift", "succeeded", "operator")]
    assert runs[0]["workflow_id"] == result.workflow_id
    devices = {d["name"]: d for d in overview["devices"]["data"]}
    assert list(devices) == ["spine-01", "leaf-01"]
    assert devices["spine-01"]["drift_state"] == "in_sync"
    assert devices["leaf-01"]["drift_state"] == "undesired"
    assert devices["leaf-01"]["undesired_paths"] == [UNDESIRED]
    assert overview["devices"]["available"] is True
    assert overview["worker"]["available"] is True
    assert overview["worker"]["data"]["pollers"] >= 1
    assert overview["schedule"]["data"]["exists"] is False
    assert overview["ci"]["available"] is False
