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
from snapl_observability.audit import BoundedAuditLog
from snapl_observability.events import EventBus
from snapl_observability.models import EventType, ObservabilityEvent
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
from snapl_orchestrator.worker.client import DEFAULT_NAMESPACE, DEFAULT_TEMPORAL_HOST
from snapl_orchestrator.worker.sandbox import build_workflow_runner
from snapl_orchestrator.workflows.deploy_intent import DeployIntentWorkflow
from snapl_orchestrator.workflows.reconcile_devices import ReconcileDevicesWorkflow
from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_AUDIT_DB", "DEFAULT_NAMESPACE", "DEFAULT_TASK_QUEUE", "DEFAULT_TEMPORAL_HOST", "run_worker"]

# Connection defaults live in worker.client (importable without pulling in
# every workflow) and are re-exported here, so the demo tasks (#97), the CLI
# (#63) and this module can never disagree about where the cluster is.
DEFAULT_TASK_QUEUE = "snapl-orchestrator"
DEFAULT_AUDIT_DB = "./snapl-audit.sqlite"


async def run_worker(*, activities: Activities | None = None) -> None:
    """Bootstrap the Temporal worker.

    Args:
        activities: Pre-built Activities container. When None, the worker
            reads env vars and constructs concrete downstream blocks. Tests
            pass their own container with mocks/stubs.

    Env vars (when activities is None):
        TEMPORAL_HOST           — frontend gRPC endpoint (default localhost:18033)
        TEMPORAL_NAMESPACE      — Temporal namespace (default 'default')
        TEMPORAL_TASK_QUEUE     — task queue (default 'snapl-orchestrator')
        SNAPL_AUDIT_DB          — SQLite path for the durable audit log
        INFRAHUB_ADDRESS        — Source of Truth address (default: the intent
                                  client's DEFAULT_ADDRESS, http://localhost:18000)
        INFRAHUB_API_TOKEN      — required
        SRLINUX_USERNAME        — gNMI username (default 'admin')
        SRLINUX_PASSWORD        — required
        SRLINUX_PORT            — gNMI port (default 57400)
        SRLINUX_INSECURE        — plaintext gNMI (default 'true'; set 'false' for TLS)
    """
    temporal_host = os.environ.get("TEMPORAL_HOST", DEFAULT_TEMPORAL_HOST)
    namespace = os.environ.get("TEMPORAL_NAMESPACE", DEFAULT_NAMESPACE)
    task_queue = os.environ.get("TEMPORAL_TASK_QUEUE", DEFAULT_TASK_QUEUE)
    audit_db = os.environ.get("SNAPL_AUDIT_DB", DEFAULT_AUDIT_DB)

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
        workflow_runner=build_workflow_runner(),
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
    token = os.environ.get("INFRAHUB_API_TOKEN")
    if not token:
        raise OrchestratorConfigError("INFRAHUB_API_TOKEN is required")
    # No worker-level address default: build_client resolves INFRAHUB_ADDRESS
    # itself and falls back to the intent client's DEFAULT_ADDRESS, so there is
    # a single source of truth for where the SoT lives (#61).
    client = build_infrahub_client(api_token=token)
    return InfrahubIntentStore(client=client)


def _srlinux_conn_env() -> dict:
    """Shared gNMI connection settings for the executor and collector builders."""
    password = os.environ.get("SRLINUX_PASSWORD")
    if not password:
        raise OrchestratorConfigError("SRLINUX_PASSWORD is required")
    raw_port = os.environ.get("SRLINUX_PORT", "57400")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise OrchestratorConfigError(f"SRLINUX_PORT must be an integer, got {raw_port!r}") from exc
    insecure = os.environ.get("SRLINUX_INSECURE", "true").strip().lower() not in ("false", "0", "no")
    return {
        "username": os.environ.get("SRLINUX_USERNAME", "admin"),
        "password": password,
        "port": port,
        "insecure": insecure,
    }


def _build_executor():
    # No fixed host: the executor resolves the dial target per device
    # (lab_node_name, then management_address).
    return GnmiExecutor(**_srlinux_conn_env())


def _build_collector():
    return GnmiCollector(**_srlinux_conn_env())


def _log_drift_event(event: ObservabilityEvent) -> None:
    """Structured-log handler for drift events — the worker's minimal real
    subscriber, so the notification surface is exercised in production (#67)."""
    if event.event_type is EventType.STATE_CLEAN:
        logger.info("drift check clean: device=%s", event.device_name)
    else:
        logger.warning(
            "drift event %s: device=%s items=%d error=%s",
            event.event_type.value,
            event.device_name,
            len(event.report.items),
            event.report.error,
        )


def _build_observer():
    # Not a bare StructuralObserver(): that self-provisions an unbounded
    # in-memory audit log nothing reads (the Orchestrator's AuditLog is the
    # durable sink) and an EventBus nothing subscribes to (#67).
    event_bus = EventBus()
    event_bus.register(_log_drift_event)
    return StructuralObserver(event_bus=event_bus, audit_log=BoundedAuditLog())
