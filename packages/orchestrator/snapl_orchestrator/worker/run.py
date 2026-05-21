"""Worker entry point — wire concrete downstream blocks and run the Temporal worker."""

from __future__ import annotations

import logging
import os

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from snapl_collector.gnmi.collector import GnmiCollector
from snapl_executor.gnmi.executor import GnmiExecutor
from snapl_intent.infrahub.client import build_client as build_infrahub_client
from snapl_intent.infrahub.store import InfrahubIntentStore
from snapl_observability.structural.observer import StructuralObserver
from snapl_orchestrator.activities import Activities, set_activities
from snapl_orchestrator.activities.audit import record_audit_event
from snapl_orchestrator.activities.collector import collect_running_state
from snapl_orchestrator.activities.executor import apply_config
from snapl_orchestrator.activities.intent import (
    fetch_desired_state,
    fetch_devices_for_use_case,
)
from snapl_orchestrator.activities.observability import detect_drift
from snapl_orchestrator.audit.sqlite import SqliteAuditLog
from snapl_orchestrator.exceptions import OrchestratorConfigError
from snapl_orchestrator.workflows.deploy_intent import DeployIntentWorkflow
from snapl_orchestrator.workflows.reconcile_devices import ReconcileDevicesWorkflow
from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow

logger = logging.getLogger(__name__)


async def run_worker(*, activities: Activities | None = None) -> None:
    """Bootstrap the Temporal worker.

    Args:
        activities: Pre-built Activities container. When None, the worker
            reads env vars and constructs concrete downstream blocks. Tests
            pass their own container with mocks/stubs.

    Required env vars (when activities is None):
        TEMPORAL_HOST           — frontend gRPC endpoint (default localhost:7233)
        TEMPORAL_NAMESPACE      — Temporal namespace (default 'default')
        TEMPORAL_TASK_QUEUE     — task queue (default 'snapl-orchestrator')
        SNAPL_AUDIT_DB          — SQLite path for the durable audit log
    """
    temporal_host = os.environ.get("TEMPORAL_HOST", "localhost:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    task_queue = os.environ.get("TEMPORAL_TASK_QUEUE", "snapl-orchestrator")
    audit_db = os.environ.get("SNAPL_AUDIT_DB", "./snapl-audit.sqlite")

    if activities is None:
        activities = await _build_default_activities(audit_db=audit_db)

    set_activities(activities)

    client = await Client.connect(
        temporal_host,
        namespace=namespace,
        data_converter=pydantic_data_converter,
    )

    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[
            DeployIntentWorkflow,
            ScanDriftWorkflow,
            ReconcileDevicesWorkflow,
        ],
        activities=[
            fetch_desired_state,
            fetch_devices_for_use_case,
            apply_config,
            collect_running_state,
            detect_drift,
            record_audit_event,
        ],
    )

    logger.info(
        "snapl-orchestrator worker starting: host=%s namespace=%s task_queue=%s audit_db=%s",
        temporal_host,
        namespace,
        task_queue,
        audit_db,
    )
    await worker.run()


async def _build_default_activities(*, audit_db: str) -> Activities:
    """Build the concrete Activities container from env-driven configuration."""
    # Lazy imports keep this module testable without the full downstream stack.
    audit_log = SqliteAuditLog(database_url=audit_db)
    await audit_log.initialize()

    try:
        intent_store = _build_intent_store()
        executor = _build_executor()
        collector = _build_collector()
        observer = _build_observer()
    except Exception as exc:
        raise OrchestratorConfigError(f"failed to bootstrap downstream blocks: {exc}") from exc

    return Activities(
        intent_store=intent_store,
        executor=executor,
        collector=collector,
        observer=observer,
        audit_log=audit_log,
    )


def _build_intent_store():
    address = os.environ.get("INFRAHUB_ADDRESS", "http://localhost:8001")
    token = os.environ.get("INFRAHUB_API_TOKEN")
    if not token:
        raise OrchestratorConfigError("INFRAHUB_API_TOKEN is required")
    client = build_infrahub_client(address=address, api_token=token)
    return InfrahubIntentStore(client=client)


def _build_executor():
    username = os.environ.get("SRLINUX_USERNAME", "admin")
    password = os.environ.get("SRLINUX_PASSWORD")
    if not password:
        raise OrchestratorConfigError("SRLINUX_PASSWORD is required")
    return GnmiExecutor(
        host="placeholder",  # GnmiExecutor expects per-device construction; orchestrator wraps it.
        port=57400,
        username=username,
        password=password,
        insecure=True,
    )


def _build_collector():
    username = os.environ.get("SRLINUX_USERNAME", "admin")
    password = os.environ.get("SRLINUX_PASSWORD")
    if not password:
        raise OrchestratorConfigError("SRLINUX_PASSWORD is required")
    return GnmiCollector(
        host="placeholder",
        port=57400,
        username=username,
        password=password,
        insecure=True,
    )


def _build_observer():
    return StructuralObserver()
