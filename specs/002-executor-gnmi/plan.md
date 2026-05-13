# Implementation Plan: NAF Executor — gNMI Config Deployment

**Branch**: `002-executor-gnmi` | **Date**: 2026-05-07 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/002-executor-gnmi/spec.md`

## Summary

Implement the NAF Executor building block as a gNMI config deployment layer for Nokia SR Linux. The module provides an `Executor` ABC with async methods for apply, rollback, dry-run, and batch apply. The concrete `GnmiExecutor` class wraps pygnmi (synchronous) via `asyncio.to_thread`, renders `DesiredState` objects from `snapl_intent` into SR Linux YANG-modelled JSON via Jinja2 templates, and returns structured result objects (not raised exceptions) for all device-side outcomes. The datacenter fabric (spine-leaf eBGP) is the primary use case.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: pygnmi>=0.8, grpcio>=1.60, jinja2>=3.1, pydantic>=2.5, snapl-intent (workspace dep)
**Storage**: None — Executor is stateless; reads from Intent, writes to devices
**Testing**: pytest, pytest-asyncio, pytest-cov; markers: unit, integration
**Target Platform**: macOS/Linux (local development via OrbStack/Docker; Containerlab for lab nodes)
**Project Type**: Library (Python package in uv workspace monorepo)
**Performance Goals**: <30s single device apply (SC-001), <1s dry-run render (SC-002), <2min batch of 12 devices (SC-004)
**Constraints**: Unit tests require no live infrastructure; integration tests require Containerlab SR Linux node
**Scale/Scope**: 12-device fabric (dcfabric use case); architecture extensible to additional use cases/vendors

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. NAF Alignment | PASS | Executor maps to exactly one NAF block. Package: `packages/executor/` |
| II. Spec-Driven Development | PASS | Spec completed before planning. Constitution → Specify → Plan → Tasks → Implement followed |
| III. Test-Driven Development | PASS | Plan mandates tests-first. Unit tests with mocked gNMIclient; integration tests with live SR Linux node |
| IV. Modularity and Composability | PASS | Independent package `snapl-executor` in uv workspace. Communicates via ABC + dataclass models. Depends only on snapl-intent (correct direction) |
| V. Contract-First Interfaces | PASS | Executor ABC + result models + exceptions defined in contracts/executor.md before implementation |
| VI. Single-Vendor, Multi-Vendor Arch | PASS | Nokia SR Linux for prototype. Vendor-specific code is entirely in `gnmi/` subpackage; new vendors add new subpackages without touching the ABC |
| VII. Simplicity | PASS | No speculative abstractions. One concrete implementation (GnmiExecutor). DC fabric first, others later |

**Gate result**: All 7 principles satisfied. No violations to justify.

**Constitution amendment note**: The constitution defines `Executor.apply(config)`. This plan uses `apply(desired: DesiredState)` to make the intent dependency explicit. An ADR should document this parameter name clarification.

## Project Structure

### Documentation (this feature)

```text
specs/002-executor-gnmi/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0: technology research
├── data-model.md        # Phase 1: entity/result type definitions
├── quickstart.md        # Phase 1: developer quickstart
├── contracts/
│   └── executor.md      # Phase 1: Executor ABC contract
└── tasks.md             # Phase 2: task breakdown (created by /speckit-tasks)
```

### Source Code (repository root)

```text
packages/executor/
├── pyproject.toml                           # Package config (already exists)
└── snapl_executor/
    ├── __init__.py                          # Package exports
    ├── abc.py                               # Executor ABC (abstract base class)
    ├── models.py                            # ApplyResult, DryRunResult, BatchResult
    ├── exceptions.py                        # ExecutorError, ExecutorRenderError, ExecutorConfigError
    ├── gnmi/
    │   ├── __init__.py
    │   ├── client.py                        # gNMIclient wrapper (timeout, asyncio.to_thread bridge)
    │   ├── executor.py                      # GnmiExecutor — concrete Executor implementation
    │   └── renderer.py                      # ConfigRenderer — Jinja2 template load + render
    └── templates/
        └── dcfabric/
            ├── interfaces.j2                # Interface list → SR Linux YANG JSON
            ├── bgp.j2                       # BGP sessions → SR Linux YANG JSON
            └── system.j2                    # Device system config (loopback, hostname)

tests/
├── unit/test_executor/
│   ├── __init__.py
│   ├── test_abc.py                          # ABC contract (cannot instantiate without impl)
│   ├── test_models.py                       # Result type validation (success/failure invariants)
│   ├── test_renderer.py                     # ConfigRenderer — template rendering, missing vars, error paths
│   └── test_executor.py                     # GnmiExecutor with mocked gNMIclient
└── integration/test_executor/
    ├── __init__.py
    ├── conftest.py                          # SR Linux fixture (skip if unreachable)
    └── test_gnmi_apply.py                   # Live apply/dry_run/rollback against SR Linux node
```

**Structure Decision**: The `gnmi/` subpackage isolates Nokia SR Linux specifics behind the ABC. Adding a NETCONF executor in the future means adding a `netconf/` sibling with no changes to `abc.py` or `models.py`. Templates live inside the package for co-versioning with the code. Per-use-case template directories allow entity templates to be shared across use cases that have identical config shapes.

## Post-Design Constitution Re-Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. NAF Alignment | PASS | Single package, single NAF block |
| II. SDD | PASS | Full spec → plan → contracts pipeline completed |
| III. TDD | PASS | Test files mapped 1:1 with source files; unit tests mock gNMIclient |
| IV. Modularity | PASS | No circular deps. executor → intent (correct). gnmi/ subpackage isolates vendor code |
| V. Contract-First | PASS | Executor ABC + result models + exceptions defined before implementation |
| VI. Single-Vendor/Multi-Vendor | PASS | ABC is vendor-neutral; GnmiExecutor is the SR Linux concrete. New vendors = new subpackage |
| VII. Simplicity | PASS | Flat structure, no speculative abstractions, DC fabric only for v1 |

## Complexity Tracking

No violations to justify. All constitution gates pass.
