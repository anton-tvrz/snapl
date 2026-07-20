"""Live closed-loop validation against real Temporal + Infrahub + SR Linux (#74).

Implements the 005 spec's success criteria, previously permanent skip-scaffolds:

- SC-001: DeployIntent against a real Temporal cluster completes < 60s
- SC-003: a worker restart mid-workflow resumes without work loss
- SC-005: audit events survive a worker restart (SQLite-backed log)
- SC-007: induced drift → scan DRIFTED → reconcile → scan CLEAN

Prerequisites and skip behavior are documented in conftest.py. Each test uses
its own task queue and workflow ids so runs never collide with a production
worker or with each other.
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from temporalio.worker import Worker

from snapl_observability.models import DriftStatus
from snapl_orchestrator.activities.audit import record_audit_event
from snapl_orchestrator.activities.collector import collect_running_state
from snapl_orchestrator.activities.executor import apply_config
from snapl_orchestrator.activities.intent import fetch_desired_state, fetch_devices_for_use_case
from snapl_orchestrator.activities.observability import detect_drift
from snapl_orchestrator.models import AuditEventType, WorkflowReason
from snapl_orchestrator.worker.client import build_client
from snapl_orchestrator.worker.sandbox import build_workflow_runner
from snapl_orchestrator.workflows.deploy_intent import DeployIntentWorkflow
from snapl_orchestrator.workflows.reconcile_devices import ReconcileDevicesWorkflow
from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow

if TYPE_CHECKING:
    from snapl_intent.models import DesiredState

pytestmark = pytest.mark.integration

_WORKFLOWS = [DeployIntentWorkflow, ScanDriftWorkflow, ReconcileDevicesWorkflow]
_ACTIVITIES = [
    fetch_desired_state,
    fetch_devices_for_use_case,
    apply_config,
    collect_running_state,
    detect_drift,
    record_audit_event,
]
_EXECUTION_TIMEOUT = timedelta(seconds=180)


def _worker(client, task_queue: str) -> Worker:
    """An in-process worker mirroring the production bootstrap (run.py)."""
    return Worker(
        client,
        task_queue=task_queue,
        workflows=_WORKFLOWS,
        activities=_ACTIVITIES,
        workflow_runner=build_workflow_runner(),
    )


def _pick(states: list[DesiredState], name: str) -> DesiredState:
    match = [s for s in states if s.device.name == name]
    assert match, f"device {name} not in seeded dcfabric SoT"
    return match[0]


def _inject_drift(state: DesiredState, credentials: tuple[str, str, int], *, description: str) -> None:
    """Change an interface description on the device out-of-band via raw gNMI —
    real config drift the closed loop must detect and heal."""
    from pygnmi.client import gNMIclient

    username, password, port = credentials
    target = state.device.lab_node_name or state.device.management_address
    with gNMIclient(target=(target, port), username=username, password=password, insecure=True) as gc:
        gc.set(
            update=[("/interface[name=ethernet-1/1]", {"description": description})],
            encoding="json_ietf",
        )


@pytest.mark.asyncio
async def test_deploy_intent_live_against_real_temporal(
    skip_if_temporal_unreachable,
    skip_if_lab_unreachable,
    live_desired_states,
    live_activities,
    temporal_endpoint,
    temporal_namespace,
) -> None:
    """SC-001: deploy of a seeded device completes successfully within 60s."""
    target = _pick(live_desired_states, "spine-02")
    client = await build_client(target=temporal_endpoint, namespace=temporal_namespace)
    task_queue = f"snapl-it-{uuid4()}"

    started = time.monotonic()
    async with _worker(client, task_queue):
        result = await client.execute_workflow(
            DeployIntentWorkflow.run,
            target.device.id,
            id=f"it-deploy-{uuid4()}",
            task_queue=task_queue,
            execution_timeout=_EXECUTION_TIMEOUT,
        )
    elapsed = time.monotonic() - started

    assert result.success, f"deploy failed: {result.reason} {result.detail} drift={result.drift_items}"
    assert result.reason == WorkflowReason.SUCCEEDED
    assert elapsed < 60, f"SC-001 violated: deploy took {elapsed:.1f}s"


@pytest.mark.asyncio
async def test_deploy_intent_worker_restart_resumes(
    skip_if_temporal_unreachable,
    skip_if_lab_unreachable,
    live_desired_states,
    live_activities,
    temporal_endpoint,
    temporal_namespace,
) -> None:
    """SC-003: work started while no worker is available, then interrupted by a
    worker hand-over mid-run, still completes without loss.

    The workflow is started before any worker polls the queue (the task sits
    durably in Temporal), a first worker runs briefly and is shut down, and a
    second worker must resume from history and finish the job.
    """
    target = _pick(live_desired_states, "leaf-01")
    client = await build_client(target=temporal_endpoint, namespace=temporal_namespace)
    task_queue = f"snapl-it-{uuid4()}"

    handle = await client.start_workflow(
        DeployIntentWorkflow.run,
        target.device.id,
        id=f"it-restart-{uuid4()}",
        task_queue=task_queue,
        execution_timeout=_EXECUTION_TIMEOUT,
    )
    await asyncio.sleep(1)  # no worker yet — the start must not be lost

    async with _worker(client, task_queue):
        await asyncio.sleep(1.5)  # partial progress, then the worker goes away

    async with _worker(client, task_queue):
        result = await handle.result()

    assert result.success, f"deploy did not survive worker restart: {result.reason} {result.detail}"


@pytest.mark.asyncio
async def test_scan_drift_then_reconcile_loop(
    skip_if_temporal_unreachable,
    skip_if_lab_unreachable,
    live_desired_states,
    live_activities,
    srlinux_credentials,
    temporal_endpoint,
    temporal_namespace,
) -> None:
    """SC-007: the full closed loop — converge, induce real drift on the device,
    scan detects it, reconcile heals it, scan comes back clean."""
    target = _pick(live_desired_states, "spine-01")
    device_id = target.device.id
    client = await build_client(target=temporal_endpoint, namespace=temporal_namespace)
    task_queue = f"snapl-it-{uuid4()}"

    async with _worker(client, task_queue):
        # Converge the device first so the induced change is the only drift.
        deploy = await client.execute_workflow(
            DeployIntentWorkflow.run,
            device_id,
            id=f"it-loop-deploy-{uuid4()}",
            task_queue=task_queue,
            execution_timeout=_EXECUTION_TIMEOUT,
        )
        assert deploy.success, f"pre-loop deploy failed: {deploy.reason} {deploy.detail}"

        await asyncio.to_thread(_inject_drift, target, srlinux_credentials, description="drift-injected-by-sc007")

        scan = await client.execute_workflow(
            ScanDriftWorkflow.run,
            "dcfabric",
            id=f"it-loop-scan1-{uuid4()}",
            task_queue=task_queue,
            execution_timeout=_EXECUTION_TIMEOUT,
        )
        report = scan.reports[device_id]
        assert report.status == DriftStatus.DRIFTED, f"induced drift not detected: {report}"
        drift_paths = {item.path for item in report.items}
        assert any("ethernet-1/1" in p and p.endswith("/description") for p in drift_paths), drift_paths

        reconcile = await client.execute_workflow(
            ReconcileDevicesWorkflow.run,
            [device_id],
            id=f"it-loop-reconcile-{uuid4()}",
            task_queue=task_queue,
            execution_timeout=_EXECUTION_TIMEOUT,
        )
        assert reconcile.succeeded == 1, f"reconcile did not heal: {reconcile}"

        rescan = await client.execute_workflow(
            ScanDriftWorkflow.run,
            "dcfabric",
            id=f"it-loop-scan2-{uuid4()}",
            task_queue=task_queue,
            execution_timeout=_EXECUTION_TIMEOUT,
        )
        assert rescan.reports[device_id].status == DriftStatus.CLEAN, rescan.reports[device_id]


@pytest.mark.asyncio
async def test_audit_log_persists_across_worker_restart(
    skip_if_temporal_unreachable,
    skip_if_lab_unreachable,
    live_desired_states,
    live_activities,
    audit_db_path,
    temporal_endpoint,
    temporal_namespace,
) -> None:
    """SC-005: audit events written before a worker restart are readable by a
    brand-new audit-log instance on the same database afterwards."""
    from snapl_orchestrator.audit.sqlite import SqliteAuditLog

    target = _pick(live_desired_states, "leaf-02")
    client = await build_client(target=temporal_endpoint, namespace=temporal_namespace)
    task_queue = f"snapl-it-{uuid4()}"
    workflow_id = f"it-audit-{uuid4()}"

    async with _worker(client, task_queue):
        result = await client.execute_workflow(
            DeployIntentWorkflow.run,
            target.device.id,
            id=workflow_id,
            task_queue=task_queue,
            execution_timeout=_EXECUTION_TIMEOUT,
        )
    assert result.success, f"deploy failed: {result.reason} {result.detail}"

    # "Restart": close the writing log, open a fresh instance on the same file
    # — the events must still be there.
    await live_activities.audit_log.close()
    reopened = SqliteAuditLog(database_url=audit_db_path)
    await reopened.initialize()
    try:
        events = await reopened.query_by_workflow(result.workflow_id)
        types = {e.event_type for e in events}
        assert AuditEventType.WORKFLOW_STARTED in types, types
        assert AuditEventType.WORKFLOW_TERMINATED in types, types
        by_device = await reopened.query_by_device(target.device.id)
        assert by_device, "device-scoped audit query empty after restart"
    finally:
        await reopened.close()
