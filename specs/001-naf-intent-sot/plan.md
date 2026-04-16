# Implementation Plan: NAF Intent — Source of Truth Integration

**Branch**: `001-naf-intent-sot` | **Date**: 2026-04-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/001-naf-intent-sot/spec.md`

## Summary

Implement the NAF Intent building block as the Source of Truth integration layer using Infrahub. The module provides an `IntentStore` ABC with async methods for desired state retrieval, two-phase seeding (schema provisioning + data ingestion from git-based declarative YAML files), schema inspection, and coordinated device deletion. The datacenter fabric (spine-leaf eBGP) is the primary use case. Infrahub's native branching aligns with the git branch model.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: infrahub-sdk[ctl]>=1.0.0, pydantic>=2.5, httpx>=0.25, pyyaml>=6.0
**Storage**: Infrahub (graph-native SoT, accessed via async Python SDK / GraphQL)
**Testing**: pytest, pytest-asyncio, pytest-cov; markers: unit, integration, live
**Target Platform**: macOS/Linux (local development via OrbStack/Docker)
**Project Type**: Library (Python package in uv workspace monorepo)
**Performance Goals**: <5s device retrieval (SC-001), <2min full fabric seed of 50 devices (SC-002)
**Constraints**: Must run locally on macOS/OrbStack, no cloud dependencies, Infrahub runs as Docker container
**Scale/Scope**: 50 devices max for prototype, 4 use cases (datacenter fabric primary)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. NAF Alignment | PASS | Intent module maps to exactly one NAF block. Package: `packages/intent/` |
| II. Spec-Driven Development | PASS | Spec completed and clarified before planning. Constitution → Specify → Plan → Tasks → Implement followed |
| III. Test-Driven Development | PASS | Plan mandates tests-first. Unit tests with mock Infrahub client; integration tests with live Infrahub |
| IV. Modularity and Composability | PASS | Independent package `snapl-intent` in uv workspace. Communicates via ABC + Pydantic models |
| V. Contract-First Interfaces | PASS | IntentStore ABC defined in contracts/intent-store.md before implementation |
| VI. Single-Vendor, Multi-Vendor Arch | PASS | Nokia SR Linux for prototype. IntentStore ABC abstracts SoT — could be backed by non-Infrahub store |
| VII. Simplicity | PASS | Prototype scope. Three similar lines over premature abstraction. DC fabric first, others later |

**Gate result**: All 7 principles satisfied. No violations to justify.

## Project Structure

### Documentation (this feature)

```text
specs/001-naf-intent-sot/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0: technology research
├── data-model.md        # Phase 1: entity definitions
├── quickstart.md        # Phase 1: developer quickstart
├── contracts/
│   └── intent-store.md  # Phase 1: IntentStore ABC contract
└── tasks.md             # Phase 2: task breakdown (created by /speckit-tasks)
```

### Source Code (repository root)

```text
packages/intent/
├── pyproject.toml                      # Package config (already exists)
└── snapl_intent/
    ├── __init__.py                     # Package exports (already exists)
    ├── abc.py                          # IntentStore ABC
    ├── models.py                       # Pydantic models (Device, Interface, BGPSession, etc.)
    ├── exceptions.py                   # Domain exceptions (IntentError hierarchy)
    ├── infrahub/
    │   ├── __init__.py
    │   ├── client.py                   # Infrahub client wrapper (connection, auth, timeout)
    │   ├── store.py                    # InfrahubIntentStore — concrete IntentStore implementation
    │   ├── schema.py                   # Schema provisioning logic (3-batch loading)
    │   └── seed.py                     # Data ingestion logic (dependency-ordered upsert)
    ├── schemas/                        # Infrahub schema YAML definitions
    │   ├── base/                       # Batch 1: schema-library base (dcim, ipam, location, org)
    │   ├── extensions/                 # Batch 2: schema-library extensions (routing_bgp, vrf)
    │   ├── network_device.yml          # Batch 3: DcimDevice project extensions
    │   ├── network_interface.yml       # Batch 3: InterfacePhysical project extensions
    │   └── business_intent.yml         # Batch 3: Business intent stubs (8 entities)
    └── seed/                           # Declarative seed data (git-based)
        └── dcfabric/
            └── topology.yml            # Spine-leaf fabric: devices, interfaces, BGP

development/
├── docker-compose.yml                  # Infrahub + Neo4j + Redis + RabbitMQ
└── .env.example                        # INFRAHUB_ADDRESS, INFRAHUB_API_TOKEN

tests/
├── conftest.py                         # Shared fixtures (mock_infrahub_client, spine_leaf_topology)
├── unit/
│   └── test_intent/
│       ├── test_models.py              # Pydantic model validation
│       ├── test_abc.py                 # ABC contract enforcement
│       ├── test_store.py               # InfrahubIntentStore with mock client
│       ├── test_schema.py              # Schema provisioning logic (batch ordering)
│       └── test_seed.py                # Data ingestion logic (dependency ordering)
└── integration/
    └── test_intent/
        ├── test_infrahub_schema.py     # Schema load against live Infrahub
        ├── test_infrahub_seed.py       # Seed data against live Infrahub
        └── test_infrahub_query.py      # Query desired state from live Infrahub
```

**Structure Decision**: Single package within the existing uv workspace monorepo. The `infrahub/` subpackage isolates the concrete SoT implementation behind the ABC. Schema YAML and seed data YAML live inside the package so they are co-versioned with the code. Schemas use Infrahub's schema-library as base types (ported from predecessor). Docker Compose infrastructure lives in `development/` (ported from predecessor).

## Post-Design Constitution Re-Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. NAF Alignment | PASS | Single package, single NAF block |
| II. SDD | PASS | Full spec → plan → contracts pipeline completed |
| III. TDD | PASS | Test files mapped 1:1 with source files; unit tests use mocked Infrahub |
| IV. Modularity | PASS | No circular deps. Intent is a leaf node (depends on no other snapl packages) |
| V. Contract-First | PASS | IntentStore ABC + Pydantic models + exceptions defined before implementation |
| VI. Single-Vendor/Multi-Vendor | PASS | ABC allows non-Infrahub backends. SR Linux is prototype target only |
| VII. Simplicity | PASS | Flat structure, no speculative abstractions, DC fabric only for v1 |

## Complexity Tracking

No violations to justify. All constitution gates pass.
