"""The worker's three counters, read from a real metrics endpoint (spec 015, T033, US3).

A test-owned Temporal dev server (``start_local``) runs on a runtime built by
``build_runtime`` on a free port, as spec 012's worker metrics test does. The
real ``ScanDrift`` and ``DeployIntent`` run over fake blocks, then the test
reads the endpoint over HTTP. Neither the lab nor the maintainer's port 18091
is touched. ``start_local`` downloads the dev-server binary on first use; the
test skips if that is not possible.
"""

from __future__ import annotations

import asyncio
import re
import socket
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pydantic_core  # noqa: F401 — pre-import to keep workflow sandbox quiet
import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from snapl_collector.models import CollectResult
from snapl_executor.models import ApplyResult
from snapl_observability.models import DriftItem, DriftReport, DriftStatus
from snapl_orchestrator.activities import Activities, set_activities
from snapl_orchestrator.activities.audit import record_audit_event
from snapl_orchestrator.activities.collector import collect_drift_state
from snapl_orchestrator.activities.executor import apply_config
from snapl_orchestrator.activities.intent import fetch_desired_state, fetch_devices_for_use_case
from snapl_orchestrator.activities.observability import detect_drift
from snapl_orchestrator.audit.memory import InMemoryAuditLog
from snapl_orchestrator.models import WorkflowReason
from snapl_orchestrator.worker.metrics import build_runtime
from snapl_orchestrator.worker.sandbox import build_workflow_runner
from snapl_orchestrator.workflows.deploy_intent import DeployIntentWorkflow
from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow

pytestmark = pytest.mark.unit

TASK_QUEUE = "snapl-counters-test"
USE_CASE = "dcfabric"
# What the Temporal runtime adds to every series; everything else is ours.
RUNTIME_LABELS = {"namespace", "task_queue", "service_name", "activity_type", "workflow_type"}
_LINE = re.compile(r"^(?P<name>[a-z_]+)\{(?P<labels>[^}]*)\} (?P<value>\S+)$")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _series(body: str, name: str) -> list[tuple[dict[str, str], float]]:
    found = []
    for line in body.splitlines():
        match = _LINE.match(line)
        if match and match["name"] == name:
            labels = dict(re.findall(r'(\w+)="([^"]*)"', match["labels"]))
            found.append((labels, float(match["value"])))
    return found


def _ours(labels: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in labels.items() if k not in RUNTIME_LABELS}


async def _scrape_until(url: str, *names: str, timeout: float = 10.0) -> str:
    """The exporter may lag the run a little; poll until every family shows."""
    deadline = asyncio.get_running_loop().time() + timeout
    body = ""
    async with httpx.AsyncClient(timeout=2.0) as http:
        while asyncio.get_running_loop().time() < deadline:
            body = (await http.get(url)).text
            if all(_series(body, n) for n in names):
                return body
            await asyncio.sleep(0.25)
    return body


@pytest.fixture
def metrics_url() -> str:
    return f"http://127.0.0.1:{_free_port()}/metrics"


@pytest.fixture
async def local_env(metrics_url: str):
    port = int(metrics_url.rsplit(":", 1)[1].split("/", 1)[0])
    runtime = build_runtime(port=port, bind="127.0.0.1")
    assert runtime is not None, "a free port must give a metrics runtime"
    try:
        env = await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter, runtime=runtime)
    except Exception as exc:
        pytest.skip(f"Temporal dev server unavailable: {exc}")
    async with env:
        yield env


def teardown_function() -> None:
    import snapl_orchestrator.activities as a

    a._activities = None


def _report(device, items: list[DriftItem]) -> DriftReport:
    return DriftReport(
        device_id=device.id,
        device_name=device.name,
        status=DriftStatus.DRIFTED if items else DriftStatus.CLEAN,
        items=items,
        timestamp=datetime.now(UTC),
    )


def _collect(device) -> CollectResult:
    return CollectResult(
        device_id=device.id, device_name=device.name, success=True, data={"/interface": []}, paths=["/interface"]
    )


def _activities(states, *, observe, executor=None) -> Activities:
    by_device = {s.device.id: s for s in states}

    async def _get(*, use_case=None, device_id=None):
        if use_case is not None:
            return states
        return [by_device[device_id]] if device_id in by_device else []

    intent_store = MagicMock()
    intent_store.get_desired_state = AsyncMock(side_effect=_get)
    collector = MagicMock()
    collector.collect = AsyncMock(side_effect=lambda device, _paths: _collect(device))
    observer = MagicMock()
    observer.detect_drift = AsyncMock(side_effect=lambda desired, _c: observe(desired.device))
    observer.emit_event = AsyncMock()
    return Activities(
        intent_store=intent_store,
        executor=executor if executor is not None else MagicMock(),
        collector=collector,
        observer=observer,
        audit_log=InMemoryAuditLog(),
    )


def _worker(env: WorkflowEnvironment) -> Worker:
    return Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[ScanDriftWorkflow, DeployIntentWorkflow],
        workflow_runner=build_workflow_runner(),
        activities=[
            fetch_devices_for_use_case,
            fetch_desired_state,
            apply_config,
            collect_drift_state,
            detect_drift,
            record_audit_event,
        ],
    )


UNDESIRED = DriftItem(
    path="/interface[name=ethernet-1/9]",
    desired=None,
    actual={"admin-state": "enable"},
    entity_kind="Interface",
    undesired=True,
)
FORWARD = DriftItem(
    path="/interface[name=ethernet-1/1]/admin-state", desired="enable", actual="disable", entity_kind="Interface"
)


@pytest.mark.asyncio
async def test_a_scan_counts_each_device_s_result_and_the_drifted_one_s_paths(
    local_env: WorkflowEnvironment, metrics_url: str, make_desired
) -> None:
    """Acceptance 1: leaf-01 with one undesired path is one scan in state undesired, one undesired path."""
    leaf, spine = make_desired("leaf-01"), make_desired("spine-01")
    set_activities(_activities([leaf, spine], observe=lambda d: _report(d, [UNDESIRED] if d.name == "leaf-01" else [])))

    async with _worker(local_env):
        await local_env.client.execute_workflow(
            ScanDriftWorkflow.run, USE_CASE, id="scan-drift-counters", task_queue=TASK_QUEUE
        )
    body = await _scrape_until(metrics_url, "snapl_scan_results_total", "snapl_drift_paths_total")

    scans = sorted(
        ((_ours(labels), value) for labels, value in _series(body, "snapl_scan_results_total")),
        key=lambda s: s[0]["device"],
    )
    assert scans == [
        ({"device": "leaf-01", "use_case": USE_CASE, "state": "undesired"}, 1.0),
        ({"device": "spine-01", "use_case": USE_CASE, "state": "in_sync"}, 1.0),
    ]
    paths = [(_ours(labels), value) for labels, value in _series(body, "snapl_drift_paths_total")]
    assert paths == [({"device": "leaf-01", "use_case": USE_CASE, "kind": "undesired"}, 1.0)]
    # The runtime's own labels on an activity's series, and nothing else beside ours.
    for labels, _ in _series(body, "snapl_scan_results_total"):
        assert labels["activity_type"] == "detect_drift"
        assert set(labels) - set(_ours(labels)) <= RUNTIME_LABELS


@pytest.mark.asyncio
async def test_a_failed_deploy_counts_its_end_with_its_reason(
    local_env: WorkflowEnvironment, metrics_url: str, make_desired
) -> None:
    """Acceptance 2: a deploy that fails verification counts failed, verification_failed."""
    leaf = make_desired("leaf-01")
    executor = MagicMock()
    executor.apply = AsyncMock(
        return_value=ApplyResult(
            device_id=leaf.device.id, device_name="leaf-01", success=True, payload={}, duration_ms=1
        )
    )
    set_activities(_activities([leaf], observe=lambda d: _report(d, [FORWARD]), executor=executor))

    async with _worker(local_env):
        result = await local_env.client.execute_workflow(
            DeployIntentWorkflow.run, leaf.device.id, id="deploy-intent-counters", task_queue=TASK_QUEUE
        )
    assert result.reason is WorkflowReason.VERIFICATION_FAILED
    body = await _scrape_until(metrics_url, "snapl_deploy_ends_total")

    ends = [(_ours(labels), value) for labels, value in _series(body, "snapl_deploy_ends_total")]
    assert ends == [
        (
            {"device": "leaf-01", "use_case": USE_CASE, "outcome": "failed", "reason": "verification_failed"},
            1.0,
        )
    ]
    for labels, _ in _series(body, "snapl_deploy_ends_total"):
        assert labels["workflow_type"] == "DeployIntent"
    # The deploy's own drift check is its verification, not a scan.
    assert _series(body, "snapl_scan_results_total") == []
    assert _series(body, "snapl_drift_paths_total") == []
