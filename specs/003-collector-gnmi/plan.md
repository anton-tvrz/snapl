# Implementation Plan: NAF Collector — gNMI Live Data Retrieval

**Branch**: `003-collector-gnmi` | **Date**: 2026-05-13 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/003-collector-gnmi/spec.md`

## Summary

Implement the NAF Collector building block as a gNMI live data retrieval layer for Nokia SR Linux. The module provides a `Collector` ABC with async methods for targeted path collection, full running config retrieval, and batch collection. The concrete `GnmiCollector` class wraps pygnmi (synchronous) via `asyncio.to_thread` — the same bridge pattern as the Executor — and returns structured result objects (not raised exceptions) for all device-side outcomes. Collected data is returned as raw Python dicts directly comparable to the Executor's rendered payloads, enabling drift detection in the Observability block.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: pygnmi>=0.8, grpcio>=1.60, pydantic>=2.5, snapl-intent (workspace dep)
**Storage**: None — Collector is stateless and read-only; no persistence
**Testing**: pytest, pytest-asyncio, pytest-cov; markers: unit, integration
**Target Platform**: macOS/Linux (local development via OrbStack/Docker; Containerlab for lab nodes)
**Project Type**: Library (Python package in uv workspace monorepo)
**Performance Goals**: <30s single-device get_running_config (SC-001), <5s targeted collect (SC-002), <2min batch of 12 devices (SC-004)
**Constraints**: Unit tests require no live infrastructure; integration tests require Containerlab SR Linux node
**Scale/Scope**: 12-device fabric (dcfabric use case); architecture extensible to additional use cases/vendors

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. NAF Alignment | PASS | Collector maps to exactly one NAF block. Package: `packages/collector/` |
| II. Spec-Driven Development | PASS | Spec completed before planning. Constitution → Specify → Plan → Tasks → Implement followed |
| III. Test-Driven Development | PASS | Plan mandates tests-first. Unit tests with mocked gNMIclient; integration tests with live SR Linux node |
| IV. Modularity and Composability | PASS | Independent package `snapl-collector` in uv workspace. Communicates via ABC + dataclass models. Depends only on snapl-intent (correct direction) |
| V. Contract-First Interfaces | PASS | Collector ABC + result models + exceptions defined in contracts/collector.md before implementation |
| VI. Single-Vendor, Multi-Vendor Arch | PASS | Nokia SR Linux for prototype. Vendor-specific code is entirely in `gnmi/` subpackage; new vendors add new subpackages without touching the ABC |
| VII. Simplicity | PASS | No speculative abstractions. One concrete implementation (GnmiCollector). No renderer — Collector does not transform data, only retrieves and parses |

**Gate result**: All 7 principles satisfied. No violations to justify.

## Project Structure

### Documentation (this feature)

```text
specs/003-collector-gnmi/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0: technology research
├── data-model.md        # Phase 1: entity/result type definitions
├── quickstart.md        # Phase 1: developer quickstart
├── contracts/
│   └── collector.md     # Phase 1: Collector ABC contract
└── tasks.md             # Phase 2: task breakdown (created by /speckit-tasks)
```

### Source Code (repository root)

```text
packages/collector/
├── pyproject.toml                           # Package config (new)
└── snapl_collector/
    ├── __init__.py                          # Package exports
    ├── abc.py                               # Collector ABC (abstract base class)
    ├── models.py                            # CollectResult, BatchCollectResult
    ├── exceptions.py                        # CollectorError, CollectorConfigError
    └── gnmi/
        ├── __init__.py
        ├── client.py                        # gNMIclient wrapper (timeout, asyncio.to_thread bridge)
        └── collector.py                     # GnmiCollector — concrete Collector implementation

tests/
├── unit/test_collector/
│   ├── __init__.py
│   ├── test_abc.py                          # ABC contract (cannot instantiate without impl)
│   ├── test_models.py                       # Result type validation (success/failure invariants)
│   ├── test_client.py                       # gnmi_get — mock gNMIclient, error propagation
│   └── test_collector.py                   # GnmiCollector with mocked gNMIclient
└── integration/test_collector/
    ├── __init__.py
    ├── conftest.py                          # SR Linux fixture (skip if unreachable)
    └── test_gnmi_collect.py                 # Live collect/get_running_config against SR Linux node
```

**Structure Decision**: The `gnmi/` subpackage isolates Nokia SR Linux specifics behind the ABC — identical pattern to the Executor. No templates directory: the Collector has no rendering step. Adding a NETCONF collector means adding a `netconf/` sibling with no changes to `abc.py` or `models.py`.

## Post-Design Constitution Re-Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. NAF Alignment | PASS | Single package, single NAF block |
| II. SDD | PASS | Full spec → plan → contracts pipeline completed |
| III. TDD | PASS | Test files mapped 1:1 with source files; unit tests mock gNMIclient |
| IV. Modularity | PASS | No circular deps. collector → intent (correct). gnmi/ subpackage isolates vendor code |
| V. Contract-First | PASS | Collector ABC + result models + exceptions defined before implementation |
| VI. Single-Vendor/Multi-Vendor | PASS | ABC is vendor-neutral; GnmiCollector is the SR Linux concrete. New vendors = new subpackage |
| VII. Simplicity | PASS | Flat structure, no renderer layer, DC fabric only for v1. Simpler than the Executor (read-only, no templates) |

## Complexity Tracking

No violations to justify. All constitution gates pass.
