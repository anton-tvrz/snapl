# Quickstart: NAF Executor — gNMI Config Deployment

**Feature**: 002-executor-gnmi
**Date**: 2026-05-07

## Prerequisites

- Python 3.12+
- `uv sync --all-groups` (installs all workspace dependencies)
- A running Containerlab SR Linux node (for integration tests)
- A seeded Infrahub instance (for end-to-end testing, optional for unit tests)

## Running Unit Tests

No infrastructure required.

```bash
uv run invoke test-unit
# or directly:
uv run pytest tests/unit/test_executor/ -m unit -v
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
uv run pytest tests/integration/test_executor/ -m integration -v
```

If no SR Linux node is reachable, tests skip automatically.

## Basic Usage

```python
import asyncio
from snapl_intent.infrahub.store import InfrahubIntentStore
from snapl_intent.infrahub.client import build_client
from snapl_executor.gnmi.executor import GnmiExecutor

async def main():
    # 1. Retrieve desired state from Intent
    client = build_client(address="http://localhost:8001", api_token="...")
    store = InfrahubIntentStore(client=client)
    states = await store.get_desired_state(use_case="dcfabric", role="spine")

    # 2. Deploy to each device
    for desired in states:
        executor = GnmiExecutor(
            host=desired.device.management_address,
            port=57400,
            username="admin",
            password="<lab-password>",  # pragma: allowlist secret
            insecure=True,
        )
        # Dry-run first
        dry = await executor.dry_run(desired)
        if not dry.success:
            print(f"Render failed for {desired.device.name}: {dry.render_error}")
            continue
        print(f"Would send to {desired.device.name}:\n{dry.payload}")

        # Apply
        result = await executor.apply(desired)
        if result.success:
            print(f"Applied {desired.device.name} in {result.duration_ms}ms")
        else:
            print(f"Failed {desired.device.name}: {result.error}")

asyncio.run(main())
```

## Batch Apply

```python
executor_map = {
    desired.device.id: GnmiExecutor(
        host=desired.device.management_address,
        port=57400,
        username="admin",
        password="<lab-password>",  # pragma: allowlist secret
        insecure=True,
    )
    for desired in states
}

# Each GnmiExecutor is scoped to one device — batch dispatches across them
from snapl_executor.gnmi.executor import apply_batch_parallel

batch = await apply_batch_parallel(executor_map, states)
print(f"Succeeded: {batch.succeeded}/{batch.total}")
for device_id, result in batch.results.items():
    if not result.success:
        print(f"  FAILED {result.device_name}: {result.error}")
```

## Package Structure

```text
packages/executor/
├── pyproject.toml
└── snapl_executor/
    ├── __init__.py          # Exports: GnmiExecutor, ApplyResult, DryRunResult, BatchResult
    ├── abc.py               # Executor ABC
    ├── models.py            # ApplyResult, DryRunResult, BatchResult
    ├── exceptions.py        # ExecutorError, ExecutorRenderError, ExecutorConfigError
    ├── gnmi/
    │   ├── __init__.py
    │   ├── client.py        # gNMIclient wrapper (connect, timeout, to_thread)
    │   ├── executor.py      # GnmiExecutor (Executor ABC implementation)
    │   └── renderer.py      # ConfigRenderer (Jinja2 template loading and rendering)
    └── templates/
        └── dcfabric/
            ├── interfaces.j2
            ├── bgp.j2
            └── system.j2

tests/
├── unit/test_executor/
│   ├── __init__.py
│   ├── test_abc.py          # ABC enforcement (can't instantiate without impl)
│   ├── test_models.py       # Pydantic/dataclass model validation
│   ├── test_renderer.py     # ConfigRenderer — template loading, rendering, error paths
│   └── test_executor.py     # GnmiExecutor with mocked gNMIclient
└── integration/test_executor/
    ├── __init__.py
    ├── conftest.py          # SR Linux fixture (skip if unreachable)
    └── test_gnmi_apply.py   # Live apply/dry_run/rollback against SR Linux node
```

## Environment Variables (Integration Tests)

| Variable | Default | Description |
|----------|---------|-------------|
| `SRLINUX_HOST` | `clab-dcfabric-spine-01` | SR Linux node hostname |
| `SRLINUX_PORT` | `57400` | gNMI port |
| `SRLINUX_USERNAME` | `admin` | gNMI username |
| `SRLINUX_PASSWORD` | *(required)* | gNMI password |

## Linting and Formatting

```bash
uv run invoke lint     # ruff check
uv run invoke format   # ruff format
```
