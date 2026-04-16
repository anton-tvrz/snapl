# Quickstart: NAF Intent Module

**Feature**: 001-naf-intent-sot
**Date**: 2026-04-15

## Prerequisites

- Python 3.12+
- uv installed
- Docker or OrbStack installed
- `INFRAHUB_ADDRESS` and `INFRAHUB_API_TOKEN` environment variables set

## Setup

```bash
# Install all workspace dependencies
uv sync --all-groups

# Copy environment template
cp development/.env.example development/.env

# Start Infrahub infrastructure (Neo4j, Redis, RabbitMQ, Infrahub server)
docker compose -f development/docker-compose.yml up -d

# Wait for Infrahub to be ready (port 8000)
# Or use: uv run invoke dev.deps
```

## Schema Provisioning

Provision the datacenter fabric data model into Infrahub:

```python
from snapl_intent.infrahub.store import InfrahubIntentStore

store = InfrahubIntentStore()

# Provision schema (idempotent — safe to repeat)
result = await store.provision_schema("dcfabric")
print(f"Loaded {result.schemas_loaded} schemas, changed: {result.changed}")
```

Or via the CLI:
```bash
uv run invoke intent.provision --use-case dcfabric
```

## Seed Data

Load the datacenter fabric dataset into Infrahub:

```python
result = await store.seed("dcfabric")
print(f"Created {result.devices_created}, updated {result.devices_updated}")
```

Seed data files live in `packages/intent/snapl_intent/seed/dcfabric/`.

## Query Desired State

```python
# Get all devices in the datacenter fabric
devices = await store.get_desired_state(use_case="dcfabric")

# Get a specific device by name
spines = await store.get_desired_state(use_case="dcfabric", role="spine")

# Get by UUID
device = await store.get_desired_state(device_id=some_uuid)
```

Each `DesiredState` object contains the device, its interfaces, and BGP sessions.

## Inspect Schema

```python
schema = await store.get_schema("dcfabric")
print(f"Entities: {schema.entities}")
print(f"Version: {schema.version}")
```

## Seed on an Infrahub Branch

```python
# Seed to a feature branch (aligns with git branch)
result = await store.seed("dcfabric", branch="001-naf-intent-sot")
```

## File Locations

| What | Where |
|------|-------|
| IntentStore ABC | `packages/intent/snapl_intent/abc.py` |
| Pydantic models | `packages/intent/snapl_intent/models.py` |
| Infrahub implementation | `packages/intent/snapl_intent/infrahub/store.py` |
| Schema YAML files | `packages/intent/snapl_intent/schemas/` (3-batch: base/ → extensions/ → project) |
| Seed data YAML files | `packages/intent/snapl_intent/seed/dcfabric/` |
| Unit tests | `tests/unit/intent/` |
| Integration tests | `tests/integration/intent/` |
| Docker Compose | `development/docker-compose.yml` |

## Running Tests

```bash
# Unit tests (no Infrahub needed — uses mock client)
uv run pytest tests/unit/intent/ -m unit -v

# Integration tests (requires running Infrahub)
uv run pytest tests/integration/intent/ -m integration -v
```
