# Implementation Plan: NAF Observability — Drift Detection & Audit

**Branch**: `004-observability-drift` | **Date**: 2026-05-14 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/004-observability-drift/spec.md`

## Summary

Implement the NAF Observability building block as a pure-Python comparison and event layer. The module exposes an `Observer` ABC with `detect_drift()`, `emit_event()`, and `log_audit()` (plus a batch variant), and ships one concrete `StructuralObserver` that performs structural diff between a `DesiredState` (from snapl-intent) and a `CollectResult` (from snapl-collector). Events are dispatched to in-process synchronous handlers; audit entries are persisted to an in-memory `AuditLog` queryable by device. The block is stateless with respect to the network — it does not call the Collector or talk to any device — and contains no third-party runtime dependencies beyond Pydantic. Async event-bus and durable audit storage are deliberately deferred to the Orchestrator block.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: pydantic>=2.5, snapl-intent (workspace dep), snapl-collector (workspace dep)
**Storage**: In-memory `AuditLog` for this iteration; durable persistence is out of scope and deferred to the Orchestrator
**Testing**: pytest, pytest-asyncio, pytest-cov; markers: unit
**Target Platform**: macOS/Linux (local development); pure Python — no platform constraints
**Project Type**: Library (Python package in uv workspace monorepo)
**Performance Goals**: <100 ms drift report per device given pre-fetched inputs (SC-001); 10-device batch produces a result for every device (SC-005)
**Constraints**: No external service connections at import or runtime; unit tests run with no infrastructure (SC-003)
**Scale/Scope**: 12-device fabric (dcfabric use case); architecture extensible to additional intent entity types

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. NAF Alignment | PASS | Observability maps to exactly one NAF block. Package: `packages/observability/` |
| II. Spec-Driven Development | PASS | Spec completed before planning. Constitution → Specify → Plan → Tasks → Implement followed |
| III. Test-Driven Development | PASS | Plan mandates tests-first. All units mockable; no infrastructure dependency |
| IV. Modularity and Composability | PASS | Independent package `snapl-observability` in uv workspace. Depends on snapl-intent and snapl-collector (correct direction per constitution) |
| V. Contract-First Interfaces | PASS | Observer ABC + Pydantic models + exceptions defined in `contracts/observer.md` before implementation |
| VI. Single-Vendor, Multi-Vendor Arch | PASS | Comparison logic is vendor-agnostic. Concrete `StructuralObserver` operates on Pydantic models — no vendor-specific path knowledge |
| VII. Simplicity | PASS | One concrete Observer. No event broker, no database. Synchronous handlers. In-memory audit log. No metrics export to Prometheus/OTel — deferred until a real consumer exists |

**Gate result**: All 7 principles satisfied. No violations to justify.

## Project Structure

### Documentation (this feature)

```text
specs/004-observability-drift/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0: design decisions
├── data-model.md        # Phase 1: entity/result type definitions
├── quickstart.md        # Phase 1: developer quickstart
├── contracts/
│   └── observer.md      # Phase 1: Observer ABC contract
└── tasks.md             # Phase 2: task breakdown (created by /speckit-tasks)
```

### Source Code (repository root)

```text
packages/observability/
├── pyproject.toml                              # Already present
└── snapl_observability/
    ├── __init__.py                             # Package exports
    ├── abc.py                                  # Observer ABC
    ├── models.py                               # DriftItem, DriftReport, ObservabilityEvent, AuditEntry, BatchDriftReport
    ├── exceptions.py                           # ObserverError
    ├── audit.py                                # AuditLog (in-memory append-only store)
    ├── events.py                               # EventBus (synchronous in-process dispatcher)
    └── structural/
        ├── __init__.py
        ├── diff.py                             # diff_desired_vs_actual() — pure function
        └── observer.py                         # StructuralObserver — concrete Observer implementation

tests/
└── unit/test_observability/
    ├── __init__.py
    ├── test_abc.py                             # ABC enforcement (cannot instantiate without impl)
    ├── test_models.py                          # Model invariants (immutability, status enum, validation)
    ├── test_audit.py                           # AuditLog append, query-by-device, ordering, immutability
    ├── test_events.py                          # EventBus register/emit, multi-handler, isolated handler failures
    ├── test_diff.py                            # Pure-function structural diff across all entity types
    └── test_observer.py                        # StructuralObserver — drift, clean, error, batch
```

**Structure Decision**: The `structural/` subpackage isolates the concrete diff logic behind the ABC — same isolation pattern as `gnmi/` in the Collector and Executor. `audit.py` and `events.py` are top-level helpers because they are concrete services (not vendor-pluggable). No integration test directory: the Observability block has no external services to integrate with — every concern is unit-testable. Adding a future "semantic" comparator (e.g., CIDR-aware) means a new sibling subpackage with no changes to `abc.py` or `models.py`.

## Post-Design Constitution Re-Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. NAF Alignment | PASS | Single package, single NAF block |
| II. SDD | PASS | Full spec → plan → research → contracts → data-model pipeline completed |
| III. TDD | PASS | Test files mapped 1:1 with source files; all logic mockable in-process |
| IV. Modularity | PASS | No circular deps. observability → collector and observability → intent (both correct per constitution) |
| V. Contract-First | PASS | Observer ABC + result models + exceptions defined before implementation |
| VI. Single-Vendor/Multi-Vendor | PASS | ABC is vendor-neutral; structural diff operates on intent models — vendor-agnostic by design |
| VII. Simplicity | PASS | No metrics exporter, no async event bus, no durable audit. Defer until a real consumer needs them |

## Complexity Tracking

No violations to justify. All constitution gates pass.
