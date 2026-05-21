# Implementation Plan: NAF Orchestrator — Temporal Workflows

**Branch**: `005-orchestrator-temporal` | **Date**: 2026-05-21 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/005-orchestrator-temporal/spec.md`

## Summary

Implement the NAF Orchestrator building block as a Temporal-based workflow layer that composes Intent, Executor, Collector, and Observability into durable, retryable, auditable end-to-end automation. The module exposes three workflows (`DeployIntent`, `ScanDrift`, `ReconcileDevices`) plus an `AuditLog` durable store. Each workflow is implemented with `temporalio` (the official Python SDK) and runs as deterministic workflow code calling typed activities that wrap the four downstream blocks. Per-device serialization is achieved via deterministic workflow IDs (`deploy-intent-{device_id}`) with the `USE_EXISTING` ID-conflict policy. The audit log is backed by Temporal's native event history plus a SQLite projection that supports the cross-workflow queries (`by device ID`, `by time range`) required by FR-007.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: temporalio>=1.7, pydantic>=2.5, aiosqlite>=0.19, snapl-intent (workspace dep), snapl-executor (workspace dep), snapl-collector (workspace dep), snapl-observability (workspace dep)
**Storage**: SQLite (file-backed, async via aiosqlite) for the audit log projection; Temporal's own event history (provided by the Temporal cluster, not by snapl) for workflow durability
**Testing**: pytest, pytest-asyncio, pytest-cov; markers: unit, integration. `temporalio.testing.WorkflowEnvironment` for in-process Temporal cluster in tests
**Target Platform**: macOS/Linux (local development via OrbStack/Docker; Temporal dev cluster via Docker Compose under `development/`)
**Project Type**: Library (Python package in uv workspace monorepo) plus a Temporal worker entry point
**Performance Goals**: <60s single-device `deploy_intent` (SC-001), <3min fabric-wide `scan_drift` of 12 devices (SC-002), worker-restart resume with no work loss (SC-003)
**Constraints**: Unit tests require no live Temporal cluster (use `WorkflowEnvironment`); integration tests require Containerlab SR Linux node + a running Temporal cluster; deterministic workflow code (no clock/IO/randomness outside activities)
**Scale/Scope**: 12-device dcfabric (primary); architecture extensible to additional use cases and devices without workflow-code changes

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. NAF Alignment | PASS | Orchestrator maps to exactly one NAF block. Package: `packages/orchestrator/`. Dependency direction respected: orchestrator → {intent, executor, collector, observability} |
| II. Spec-Driven Development | PASS | Spec completed and committed before planning. Constitution → Specify → Plan → Tasks → Implement followed |
| III. Test-Driven Development | PASS | Plan mandates tests-first. Unit tests use `WorkflowEnvironment` + mocked activities; integration tests against live Temporal + Containerlab |
| IV. Modularity and Composability | PASS | Independent package `snapl-orchestrator` in uv workspace. Consumes downstream ABCs unchanged — no breaking changes to Intent / Executor / Collector / Observability |
| V. Contract-First Interfaces | PASS | Workflow signatures, activity signatures, and `AuditLog` interface defined in `contracts/orchestrator.md` before implementation |
| VI. Single-Vendor, Multi-Vendor Arch | PASS | Orchestrator is fully vendor-neutral — all vendor specificity is downstream of the activities it calls. Adding a new vendor is a downstream change to Executor/Collector, not the Orchestrator |
| VII. Simplicity | PASS | One Temporal SDK, one durability mechanism (Temporal history + SQLite projection), no speculative features. No scheduling, no auto-remediation, no auth — all explicitly deferred per spec Assumptions |

**Gate result**: All 7 principles satisfied. No violations to justify.

## Project Structure

### Documentation (this feature)

```text
specs/005-orchestrator-temporal/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0: technology research
├── data-model.md        # Phase 1: entity/result type definitions
├── quickstart.md        # Phase 1: developer quickstart
├── contracts/
│   └── orchestrator.md  # Phase 1: Workflow + Activity + AuditLog contracts
└── tasks.md             # Phase 2: task breakdown (created by /speckit-tasks)
```

### Source Code (repository root)

```text
packages/orchestrator/
├── pyproject.toml                              # Package config (workspace deps)
└── snapl_orchestrator/
    ├── __init__.py                             # Package exports
    ├── models.py                               # WorkflowResult, DriftScanResult, ReconcileResult, AuditEvent
    ├── exceptions.py                           # OrchestratorError, OrchestratorConfigError
    ├── activities/
    │   ├── __init__.py
    │   ├── intent.py                           # fetch_desired_state activity
    │   ├── executor.py                         # apply_config activity
    │   ├── collector.py                        # collect_running_state activity
    │   ├── observability.py                    # detect_drift activity
    │   └── audit.py                            # record_audit_event activity
    ├── workflows/
    │   ├── __init__.py
    │   ├── deploy_intent.py                    # DeployIntent workflow
    │   ├── scan_drift.py                       # ScanDrift workflow
    │   └── reconcile_devices.py                # ReconcileDevices workflow
    ├── audit/
    │   ├── __init__.py
    │   ├── abc.py                              # AuditLog ABC (durable, append-only)
    │   ├── sqlite.py                           # SqliteAuditLog — file-backed projection
    │   └── schema.sql                          # SQLite DDL for the audit table
    └── worker/
        ├── __init__.py
        ├── client.py                           # Temporal client factory
        └── run.py                              # Worker entry point (invoke target)

tests/
├── unit/test_orchestrator/
│   ├── __init__.py
│   ├── test_models.py                          # Pydantic model invariants
│   ├── test_audit_sqlite.py                    # SqliteAuditLog append/query/append-only
│   ├── test_activity_intent.py                 # fetch_desired_state activity — mocked IntentStore
│   ├── test_activity_executor.py               # apply_config activity — mocked Executor
│   ├── test_activity_collector.py              # collect_running_state activity — mocked Collector
│   ├── test_activity_observability.py          # detect_drift activity — mocked Observer
│   ├── test_activity_audit.py                  # record_audit_event activity — in-memory log
│   ├── test_workflow_deploy_intent.py          # DeployIntent — WorkflowEnvironment + mocked activities
│   ├── test_workflow_scan_drift.py             # ScanDrift — WorkflowEnvironment + mocked activities
│   └── test_workflow_reconcile_devices.py      # ReconcileDevices — WorkflowEnvironment + mocked activities
└── integration/test_orchestrator/
    ├── __init__.py
    ├── conftest.py                             # Temporal dev cluster + SR Linux fixtures (skip if unreachable)
    └── test_deploy_intent_live.py              # End-to-end deploy against live SR Linux + live Temporal
```

**Structure Decision**: The `activities/` and `workflows/` split is the standard Temporal Python layout — workflow code is deterministic and never imports non-deterministic libraries; activities own all IO and call the downstream blocks. The `audit/` subpackage holds the durable AuditLog ABC + concrete SQLite implementation. Adding a Postgres-backed AuditLog later is a sibling module under `audit/` with no workflow-code changes. The `worker/` subpackage isolates the Temporal client/worker bootstrapping from the workflow definitions, so the same workflows can be invoked from tests, from a CLI (future Presentation block), or from a long-running worker.

## Post-Design Constitution Re-Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. NAF Alignment | PASS | Single package, single NAF block. Dependency direction matches `presentation → orchestrator → {intent, executor, collector, observability}` |
| II. SDD | PASS | Full spec → plan → research → data-model → contracts pipeline completed before tasks |
| III. TDD | PASS | Test files mapped 1:1 with source files; workflow tests use `WorkflowEnvironment` so no live Temporal required |
| IV. Modularity | PASS | No circular deps. Consumes downstream ABCs only — no concrete coupling to GnmiExecutor / GnmiCollector / StructuralObserver / InfrahubIntentStore |
| V. Contract-First | PASS | Workflow signatures, activity signatures, models, and AuditLog ABC defined in contracts before implementation |
| VI. Single-Vendor/Multi-Vendor | PASS | Orchestrator is vendor-neutral. Vendor specificity lives entirely in downstream packages |
| VII. Simplicity | PASS | One workflow engine (Temporal), one durable store for projection (SQLite), one ABC per concern. No scheduling, no auto-remediation, no auth — explicitly deferred |

## Complexity Tracking

No violations to justify. All constitution gates pass.
