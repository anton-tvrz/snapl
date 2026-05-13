# Quickstart: NAF Collector — gNMI Live Data Retrieval

**Feature**: 003-collector-gnmi
**Date**: 2026-05-13

## Prerequisites

- Python 3.12+
- `uv sync --all-groups` (installs all workspace dependencies)
- A running Containerlab SR Linux node (for integration tests)

## Running Unit Tests

No infrastructure required.

```bash
uv run invoke test-unit
# or directly:
uv run pytest tests/unit/test_collector/ -m unit -v
```

## Running Integration Tests

Bring up the Containerlab lab:

```bash
# From repo root — starts the dcfabric lab topology
cd containerlab && sudo containerlab deploy -t dcfabric.yml
```

Then run integration tests with the SR Linux node address:

```bash
SRLINUX_HOST=clab-dcfabric-spine-01 \
SRLINUX_PORT=57400 \
SRLINUX_USERNAME=admin \
SRLINUX_PASSWORD=<lab-password> \
uv run pytest tests/integration/test_collector/ -m integration -v
```

If no SR Linux node is reachable, tests skip automatically.

## Basic Usage

```python
import asyncio
from snapl_intent.infrahub.store import InfrahubIntentStore
from snapl_intent.infrahub.client import build_client
from snapl_collector.gnmi.collector import GnmiCollector

async def main():
    # 1. Retrieve device list from Intent
    client = build_client(address="http://localhost:8001", api_token="...")
    store = InfrahubIntentStore(client=client)
    states = await store.get_desired_state(use_case="dcfabric", role="spine")

    # 2. Collect running config from each device
    for state in states:
        collector = GnmiCollector(
            host=state.device.management_address,
            port=57400,
            username="admin",
            password="<lab-password>",  # pragma: allowlist secret
            insecure=True,
        )

        # Get full running config
        result = await collector.get_running_config(state.device)
        if result.success:
            print(f"Collected {state.device.name} in {result.duration_ms}ms")
            print(f"  Data keys: {list(result.data.get('/', {}).keys())}")
        else:
            print(f"Failed {state.device.name}: {result.error}")

asyncio.run(main())
```

## Targeted Path Collection

```python
import asyncio
from snapl_collector.gnmi.collector import GnmiCollector

async def main():
    collector = GnmiCollector(
        host="clab-dcfabric-spine-01",
        port=57400,
        username="admin",
        password="<lab-password>",  # pragma: allowlist secret
        insecure=True,
    )

    # Collect only BGP neighbor state and interface status
    result = await collector.collect(
        device=device,
        paths=[
            "/interface",
            "/network-instance[name=default]/protocols/bgp/neighbor",
        ],
    )

    if result.success:
        bgp_neighbors = result.data.get("/network-instance[name=default]/protocols/bgp/neighbor", [])
        print(f"BGP neighbors: {len(bgp_neighbors)}")
    else:
        print(f"Collection failed: {result.error}")

asyncio.run(main())
```

## Batch Collect

```python
import asyncio
from snapl_collector.gnmi.collector import GnmiCollector

async def collect_fabric(devices, paths):
    # Each GnmiCollector is scoped to one device
    collectors = {
        device.id: GnmiCollector(
            host=device.management_address,
            port=57400,
            username="admin",
            password="<lab-password>",  # pragma: allowlist secret
            insecure=True,
        )
        for device in devices
    }

    # collect_batch dispatches concurrent GETs across all devices
    # Use any collector — collect_batch takes the full device list
    collector = next(iter(collectors.values()))
    batch = await collector.collect_batch(
        devices=devices,
        paths=["/interface", "/network-instance[name=default]/protocols/bgp/neighbor"],
    )

    print(f"Collected: {batch.succeeded}/{batch.total}")
    for device_id, result in batch.results.items():
        if not result.success:
            print(f"  FAILED {result.device_name}: {result.error}")

asyncio.run(collect_fabric(devices, paths))
```

## Package Structure

```text
packages/collector/
├── pyproject.toml
└── snapl_collector/
    ├── __init__.py          # Exports: GnmiCollector, CollectResult, BatchCollectResult
    ├── abc.py               # Collector ABC
    ├── models.py            # CollectResult, BatchCollectResult
    ├── exceptions.py        # CollectorError, CollectorConfigError
    └── gnmi/
        ├── __init__.py
        ├── client.py        # gNMIclient wrapper (connect, timeout, to_thread)
        └── collector.py     # GnmiCollector (Collector ABC implementation)

tests/
├── unit/test_collector/
│   ├── __init__.py
│   ├── test_abc.py          # ABC enforcement (can't instantiate without impl)
│   ├── test_models.py       # Dataclass model field validation and invariants
│   ├── test_client.py       # gnmi_get — mock gNMIclient, error propagation
│   └── test_collector.py   # GnmiCollector with mocked gNMIclient
└── integration/test_collector/
    ├── __init__.py
    ├── conftest.py          # SR Linux fixture (skip if unreachable)
    └── test_gnmi_collect.py # Live collect/get_running_config against SR Linux node
```

## Environment Variables (Integration Tests)

| Variable | Default | Description |
|----------|---------|-------------|
| `SRLINUX_HOST` | `clab-dcfabric-spine-01` | SR Linux node hostname |
| `SRLINUX_PORT` | `57400` | gNMI port |
| `SRLINUX_USERNAME` | `admin` | gNMI username |
| `SRLINUX_PASSWORD` | *(required)* | gNMI password |

These are the same variables used by the Executor integration tests — the same running lab node serves both.

## Linting and Formatting

```bash
uv run invoke lint     # ruff check
uv run invoke format   # ruff format
```
