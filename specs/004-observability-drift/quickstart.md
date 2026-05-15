# Quickstart: NAF Observability — Drift Detection & Audit

**Feature**: 004-observability-drift
**Date**: 2026-05-14

## Prerequisites

- Python 3.12+
- `uv sync --all-groups` (installs all workspace dependencies)
- No infrastructure required — Observability has no external services

## Running Unit Tests

```bash
uv run invoke test-unit
# or directly:
uv run pytest tests/unit/test_observability/ -m unit -v
```

There are no integration tests for this block. End-to-end validation lives in the Orchestrator block, which wires Intent → Executor → Collector → Observability into a single flow.

## Basic Usage — Single-Device Drift Check

```python
import asyncio

from snapl_collector.gnmi.collector import GnmiCollector
from snapl_intent.infrahub.client import build_client
from snapl_intent.infrahub.store import InfrahubIntentStore
from snapl_observability.structural.observer import StructuralObserver


async def main():
    # 1. Pull desired state from Intent
    intent = InfrahubIntentStore(client=build_client(address="http://localhost:8001", api_token="..."))
    states = await intent.get_desired_state(use_case="dcfabric", role="spine")
    desired = states[0]

    # 2. Pull live state from Collector
    collector = GnmiCollector(
        host=desired.device.management_address,
        port=57400,
        username="admin",
        password="<lab-password>",  # pragma: allowlist secret
        insecure=True,
    )
    actual = await collector.get_running_config(desired.device)

    # 3. Detect drift
    observer = StructuralObserver()
    report = await observer.detect_drift(desired, actual)

    print(f"{desired.device.name}: {report.status.value} — {len(report.items)} discrepancies")
    for item in report.items:
        print(f"  {item.path}: desired={item.desired!r} actual={item.actual!r}")


asyncio.run(main())
```

## Emitting Drift Events

```python
import asyncio
import logging

from snapl_observability.events import EventBus
from snapl_observability.models import ObservabilityEvent
from snapl_observability.structural.observer import StructuralObserver


def log_handler(event: ObservabilityEvent) -> None:
    logging.warning("drift event: %s on %s", event.event_type.value, event.device_name)


async def main(desired, actual):
    bus = EventBus()
    bus.register(log_handler)

    observer = StructuralObserver(event_bus=bus)
    report = await observer.detect_drift(desired, actual)
    event = await observer.emit_event(report)
    print(f"emitted: {event.event_type.value} at {event.timestamp.isoformat()}")
```

## Querying the Audit Log

```python
import asyncio

from snapl_observability.audit import AuditLog
from snapl_observability.structural.observer import StructuralObserver


async def main(desired, actual):
    log = AuditLog()
    observer = StructuralObserver(audit_log=log)

    await observer.detect_drift(desired, actual)
    await observer.detect_drift(desired, actual)  # second check, second audit entry

    entries = log.query_by_device(desired.device.id)
    print(f"{len(entries)} audit entries for {desired.device.name}")
    for entry in entries:
        print(f"  {entry.timestamp.isoformat()} {entry.operation.value}: {entry.outcome.value}")
```

## Batch Drift Detection

```python
import asyncio

from snapl_observability.structural.observer import StructuralObserver


async def main(desired_list, collect_results):
    # desired_list: list[DesiredState] from Intent
    # collect_results: list[CollectResult] from Collector.collect_batch
    pairs = list(zip(desired_list, collect_results, strict=True))

    observer = StructuralObserver()
    batch = await observer.detect_drift_batch(pairs)

    print(f"Total: {batch.total} | clean: {batch.clean} | drifted: {batch.drifted} | error: {batch.errored}")
    for device_id, report in batch.reports.items():
        if report.status.value == "drifted":
            print(f"  {report.device_name} has {len(report.items)} discrepancies")
```

## Package Structure

```text
packages/observability/
├── pyproject.toml
└── snapl_observability/
    ├── __init__.py          # Exports: StructuralObserver, EventBus, AuditLog, all models
    ├── abc.py               # Observer ABC
    ├── models.py            # DriftItem, DriftReport, BatchDriftReport, ObservabilityEvent, AuditEntry, enums
    ├── exceptions.py        # ObserverError
    ├── audit.py             # AuditLog (in-memory append-only)
    ├── events.py            # EventBus (synchronous in-process)
    └── structural/
        ├── __init__.py
        ├── diff.py          # diff_desired_vs_actual() pure function + ENTITY_FIELD_MAP
        └── observer.py      # StructuralObserver — concrete Observer implementation

tests/
└── unit/test_observability/
    ├── __init__.py
    ├── test_abc.py          # ABC enforcement (cannot instantiate without impl)
    ├── test_models.py       # Pydantic model invariants and immutability
    ├── test_audit.py        # AuditLog append, query, ordering, list-copy isolation
    ├── test_events.py       # EventBus register/emit, multi-handler, isolated failures
    ├── test_diff.py         # Pure diff function: every entity kind, missing keys, value mismatches
    └── test_observer.py     # StructuralObserver: drift, clean, error, batch, audit side effects
```

## Linting and Formatting

```bash
uv run invoke lint     # ruff check
uv run invoke format   # ruff format
```

## What This Block Does Not Do

- It does not call the Collector or talk to any device. Inputs are pre-fetched.
- It does not export Prometheus / OpenTelemetry metrics (deferred — see research R8).
- It does not persist audit entries across process restarts (deferred — see research R4).
- It does not dispatch async event handlers or integrate with Temporal signals (Orchestrator's job — see research R3).
- It does not perform semantic comparison (e.g., `10.0.0.1/24` vs `10.0.0.0/24` are different strings → different values). Listed as an explicit Assumption in the spec.
