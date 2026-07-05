# Quickstart: NAF Orchestrator — Temporal Workflows

**Feature**: 005-orchestrator-temporal
**Date**: 2026-05-21

## Prerequisites

- Python 3.12+
- `uv sync --all-groups` (installs all workspace dependencies)
- Docker / OrbStack (for the Temporal dev cluster and Containerlab)
- A running Containerlab SR Linux node (for integration tests against live devices)

## Running Unit Tests

No live Temporal cluster or live devices required. The `WorkflowEnvironment` runs an in-process Temporal cluster for unit tests; activities are mocked.

```bash
uv run invoke test-unit
# or directly:
uv run pytest tests/unit/test_orchestrator/ -m unit -v
```

## Bring Up the Dev Stack

The integration tests need a Temporal cluster and (optionally) Infrahub + Containerlab.

```bash
# Starts Temporal, Infrahub, and Infrahub's backing stores
uv run invoke dev.deps

# Bring up the dcfabric Containerlab topology (dockerized clab, no native install)
uv run invoke dev.lab-deploy
```

## Running Integration Tests

```bash
TEMPORAL_HOST=localhost:7233 \
TEMPORAL_NAMESPACE=default \
TEMPORAL_TASK_QUEUE=snapl-orchestrator \
SNAPL_AUDIT_DB=./snapl-audit.sqlite \
SRLINUX_HOST=clab-dcfabric-spine-01 \
SRLINUX_PORT=57400 \
SRLINUX_USERNAME=admin \
SRLINUX_PASSWORD=<lab-password> \
uv run pytest tests/integration/test_orchestrator/ -m integration -v
```

If Temporal or the SR Linux node is unreachable, the relevant tests skip automatically.

## Starting the Worker

The Orchestrator worker hosts the workflows and activities. Run it in its own terminal.

```bash
uv run invoke orchestrator.start
```

Environment configuration (with sensible defaults):

| Variable | Default | Description |
|----------|---------|-------------|
| `TEMPORAL_HOST` | `localhost:7233` | Temporal frontend gRPC endpoint |
| `TEMPORAL_NAMESPACE` | `default` | Temporal namespace |
| `TEMPORAL_TASK_QUEUE` | `snapl-orchestrator` | Task queue for snapl workers |
| `SNAPL_AUDIT_DB` | `./snapl-audit.sqlite` | SQLite file path for the durable audit log |
| `INFRAHUB_ADDRESS` | `http://localhost:8001` | Infrahub endpoint for the Intent block |
| `INFRAHUB_API_TOKEN` | *(required)* | Infrahub API token |
| `SRLINUX_USERNAME` | `admin` | gNMI username (used by Executor / Collector activities) |
| `SRLINUX_PASSWORD` | *(required)* | gNMI password |

The worker logs each workflow and activity start/completion. Stop it with Ctrl-C.

## Basic Usage — Deploy Intent for One Device

```python
import asyncio
from uuid import UUID

from temporalio.client import Client, WorkflowIDConflictPolicy

from snapl_orchestrator.workflows.deploy_intent import DeployIntentWorkflow


async def main() -> None:
    client = await Client.connect("localhost:7233", namespace="default")

    device_id = UUID("11111111-1111-1111-1111-111111111111")

    result = await client.execute_workflow(
        DeployIntentWorkflow.run,
        device_id,
        id=f"deploy-intent-{device_id}",
        task_queue="snapl-orchestrator",
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )

    if result.success:
        print(f"Deployed {device_id} in {result.ended_at - result.started_at}")
    else:
        print(f"Deploy failed [{result.reason.value}]: {result.detail}")


asyncio.run(main())
```

## Scan an Entire Use Case for Drift

```python
import asyncio
from uuid import uuid4

from temporalio.client import Client

from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow


async def main() -> None:
    client = await Client.connect("localhost:7233", namespace="default")

    scan = await client.execute_workflow(
        ScanDriftWorkflow.run,
        "dcfabric",
        id=f"scan-drift-dcfabric-{uuid4()}",
        task_queue="snapl-orchestrator",
    )

    print(f"Scanned {scan.total} devices: {scan.clean} clean, {scan.drifted} drifted, {scan.errored} errored")
    for device_id, report in scan.reports.items():
        if report.status.value == "drifted":
            print(f"  DRIFT on {report.device_name}: {len(report.items)} paths differ")


asyncio.run(main())
```

## Reconcile a List of Drifted Devices

```python
import asyncio
from uuid import UUID, uuid4

from temporalio.client import Client

from snapl_orchestrator.workflows.reconcile_devices import ReconcileDevicesWorkflow


async def main() -> None:
    client = await Client.connect("localhost:7233", namespace="default")

    drifted = [
        UUID("11111111-1111-1111-1111-111111111111"),
        UUID("22222222-2222-2222-2222-222222222222"),
    ]

    result = await client.execute_workflow(
        ReconcileDevicesWorkflow.run,
        drifted,
        id=f"reconcile-devices-{uuid4()}",
        task_queue="snapl-orchestrator",
    )

    print(f"Reconciled {result.succeeded}/{result.total} successfully ({result.failed} failed, {result.skipped} skipped)")
    for device_id, wf in result.device_results.items():
        if not wf.success:
            print(f"  FAILED {device_id} [{wf.reason.value}]: {wf.detail}")


asyncio.run(main())
```

## Query the Durable Audit Log

```python
import asyncio
from uuid import UUID

from snapl_orchestrator.audit.sqlite import SqliteAuditLog


async def main() -> None:
    log = SqliteAuditLog(database_url="./snapl-audit.sqlite")
    await log.initialize()

    # All events for a given workflow
    events = await log.query_by_workflow("deploy-intent-11111111-1111-1111-1111-111111111111")
    for e in events:
        print(f"[{e.timestamp}] {e.event_type.value} {e.activity_name or ''} {e.outcome or ''}")

    # All events for a device across all workflows
    device_events = await log.query_by_device(UUID("11111111-1111-1111-1111-111111111111"))
    print(f"Total events for device: {len(device_events)}")


asyncio.run(main())
```

## Cancel an In-Flight Workflow

```python
import asyncio

from temporalio.client import Client


async def main() -> None:
    client = await Client.connect("localhost:7233", namespace="default")
    handle = client.get_workflow_handle("deploy-intent-11111111-1111-1111-1111-111111111111")
    await handle.cancel()

    # Await the terminal state — workflow writes a CANCELLED audit event before returning
    result = await handle.result()
    print(f"Workflow terminated with reason: {result.reason.value}")


asyncio.run(main())
```

## Package Structure

```text
packages/orchestrator/
├── pyproject.toml
└── snapl_orchestrator/
    ├── __init__.py                 # Exports: workflows, models
    ├── models.py                   # WorkflowResult, DriftScanResult, ReconcileResult, AuditEvent, enums
    ├── exceptions.py               # OrchestratorError, OrchestratorConfigError, AuditLogError
    ├── activities/
    │   ├── __init__.py
    │   ├── intent.py               # fetch_desired_state
    │   ├── executor.py             # apply_config
    │   ├── collector.py            # collect_running_state
    │   ├── observability.py        # detect_drift
    │   └── audit.py                # record_audit_event
    ├── workflows/
    │   ├── __init__.py
    │   ├── deploy_intent.py        # DeployIntentWorkflow
    │   ├── scan_drift.py           # ScanDriftWorkflow
    │   └── reconcile_devices.py    # ReconcileDevicesWorkflow
    ├── audit/
    │   ├── __init__.py
    │   ├── abc.py                  # AuditLog ABC
    │   ├── sqlite.py               # SqliteAuditLog
    │   └── schema.sql              # SQLite DDL
    └── worker/
        ├── __init__.py
        ├── client.py               # Temporal client factory
        └── run.py                  # Worker entry point

tests/
├── unit/test_orchestrator/
│   ├── __init__.py
│   ├── test_models.py
│   ├── test_audit_sqlite.py
│   ├── test_activity_intent.py
│   ├── test_activity_executor.py
│   ├── test_activity_collector.py
│   ├── test_activity_observability.py
│   ├── test_activity_audit.py
│   ├── test_workflow_deploy_intent.py
│   ├── test_workflow_scan_drift.py
│   └── test_workflow_reconcile_devices.py
└── integration/test_orchestrator/
    ├── __init__.py
    ├── conftest.py                 # Temporal + SR Linux fixtures
    └── test_deploy_intent_live.py
```

## Linting and Formatting

```bash
uv run invoke lint     # ruff check
uv run invoke format   # ruff format
```

## Troubleshooting

**`ConnectionRefusedError: [Errno 61] localhost:7233`** — Temporal isn't running. Start it with `uv run invoke dev.deps`.

**`WorkflowAlreadyStartedError`** — Another deploy is in flight for the same device. With `id_conflict_policy=USE_EXISTING`, this should not occur; if it does, you likely omitted that policy. Switch to `USE_EXISTING` to join the running workflow rather than start a new one.

**Workflow tests hang** — The `WorkflowEnvironment.start_time_skipping()` fixture has not been entered. Make sure your test uses `async with WorkflowEnvironment.start_time_skipping() as env:` and reuses `env.client` for the workflow start.

**SQLite "database is locked"** — Concurrent writers from outside the worker process. The audit log assumes a single writer (the worker). Stop other processes that have the SQLite file open, or use a different `SNAPL_AUDIT_DB` path per worker.
