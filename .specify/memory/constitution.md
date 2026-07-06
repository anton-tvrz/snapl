# snapl Constitution

## Vision

snapl is a modular network automation prototype that implements the Network Automation Forum (NAF) Framework's six building blocks as independent, composable Python packages. The platform demonstrates production-grade automation patterns across multiple network use cases (datacenter fabric, client edge, SD-WAN, WAN) using Nokia SR Linux as the prototyping vendor, with architecture designed for multi-vendor extension.

This project brings together the NAF Framework for architecture, GitHub Spec Kit for development methodology, and proven patterns from project-network-synapse-quattro for implementation.

## Core Principles

### I. NAF Alignment

Every component maps to exactly one NAF building block. The six blocks are:

1. **Intent** — Stores and manages desired network state through structured APIs
2. **Executor** — Applies network changes via write interfaces (gNMI, NETCONF, SSH)
3. **Collector** — Retrieves live network data using read protocols
4. **Observability** — Persists historical data, detects discrepancies, generates events
5. **Orchestrator** — Coordinates automation workflows across components
6. **Presentation** — Provides user interfaces for system interaction

The NAF feedback loop (Intent -> Executor -> Collector -> Observability -> Orchestrator -> Executor) is the primary architectural pattern. Components communicate through well-defined Python interfaces (abstract base classes + Pydantic models). No circular dependencies between packages.

Dependency direction:
- `presentation` -> `orchestrator` -> {`intent`, `executor`, `collector`, `observability`}
- `observability` -> `collector`
- `executor` -> `intent`

### II. Spec-Driven Development (NON-NEGOTIABLE)

No module is implemented without a completed specification. The SDD workflow is:

1. **Constitution** — This document; governs all development
2. **Specify** — Define what to build (what/why, not how)
3. **Plan** — Document architecture and technology choices
4. **Tasks** — Generate actionable, ordered task breakdown
5. **Implement** — Execute tasks following plan and fulfilling spec

Specifications are living documents updated as understanding evolves. Contracts (API interfaces between NAF blocks) are defined before implementation. Cross-artifact analysis must pass before implementation begins.

### III. Test-Driven Development (NON-NEGOTIABLE)

TDD is mandatory. When generating code:

1. Always produce the test file first
2. Follow Red-Green-Refactor: failing test -> minimum implementation -> refactor
3. Never create a source file without its corresponding test file
4. Use pytest fixtures from `tests/conftest.py` for shared test data
5. Coverage target: >=80% on new code

Test markers: `unit`, `integration`, `live`, `e2e`, `slow`, `pre_deployment`, `post_deployment`.

### IV. Modularity and Composability

Each NAF block is an independent Python package in a uv workspace monorepo:

```
packages/
├── intent/        (snapl-intent)
├── executor/      (snapl-executor)
├── collector/     (snapl-collector)
├── observability/ (snapl-observability)
├── orchestrator/  (snapl-orchestrator)
└── presentation/  (snapl-presentation)
```

Packages communicate through abstract base classes and Pydantic models. Each package must be independently testable with unit tests requiring no external infrastructure.

### V. Contract-First Interfaces

Before implementing any NAF block, define its contract:

- `IntentStore` ABC: `get_desired_state()`, `get_schema()`, `seed()`
- `Executor` ABC: `apply(config)`, `rollback(config)`, `dry_run(config)`
- `Collector` ABC: `collect(device, paths)`, `get_running_config(device)`
- `Observer` ABC: `detect_drift(desired, actual)`, `emit_event(event)`, `log_audit(entry)`
- Orchestrator: Temporal workflows composing the above via their ABCs

For async events (drift detected, config changed), Temporal signals/activities serve as the event mechanism.

### VI. Single-Vendor Prototype, Multi-Vendor Architecture

All prototyping uses Nokia SR Linux via Containerlab. Vendor-specific code is behind abstraction layers (driver pattern). gNMI is the primary device interface (model-driven, structured). The architecture must support adding new vendors without modifying core logic.

### VII. Simplicity

Start simple, add complexity only when needed. Three similar lines of code are better than a premature abstraction. No speculative features, no unnecessary configurability. Each module should do one thing well.

### VIII. Intent-First Correctness

"Source of Truth" conflates two concepts that must never be merged: **intent** (what the network is supposed to look like) and **operational state** (what it actually looks like right now). Drift is the gap between them, and it is daily reality, not an edge case. (See ADR-0003.)

1. **Authority split** — The SoT is authoritative for intended state; the live network is authoritative only for operational reality. Device state is never silently promoted to truth: no automatic reverse-sync of running config into the SoT. Promoting observed state into intent is an explicit, reviewed operation. A manual device change that "works" is still drift. Intent attributes and operational addressing are distinct even when they look alike (e.g. `management_ip` is intent data for rendering; the gNMI dial target is operational addressing resolved at call time).
2. **Drift response is part of drift detection** — Every drift-detection path has a defined response: `report` (default), `remediate` (operator-triggered or explicitly automated), or `suppress` (e.g. maintenance windows). Detecting drift without a defined response is an incomplete feature. Auto-remediation is never the silent default.
3. **Intent extends beyond configuration** — Intent may declare operational expectations (BGP sessions established, interfaces oper-up, thresholds), not only config. Verification compares reality against intent; liveness alone never passes verification ("operationally up" ≠ "correct").
4. **Executor idempotency** — `Executor.apply()` is idempotent: applying the same intent twice yields the same device state with no additional change. Workflows may safely retry applies. Destructive or uncertain changes go through `dry_run()` first.

## Use Cases

### Datacenter Fabric (Primary)
Spine-leaf eBGP underlay with Nokia SR Linux. Ported from project-network-synapse-quattro as the baseline. Demonstrates the full NAF feedback loop.

### Client Edge
Single or dual SR Linux routers as customer-facing edge. BGP peering to upstream, static/OSPF downstream. Demonstrates intent-driven edge provisioning.

### SD-WAN (Conceptual)
SR Linux nodes simulating SD-WAN hub/spoke topology. Overlay tunnel configuration via intent. Demonstrates policy-based routing intent.

### WAN
SR Linux backbone routers with IS-IS/MPLS-like underlay. Traffic engineering intent. Demonstrates multi-hop path orchestration.

## Technology Stack

### Required
- Python 3.12+
- uv workspace monorepo
- Ruff (lint + format), mypy (type checking)
- pytest with strict markers
- Infrahub (OpsMill) as Source of Truth
- Temporal for durable workflow orchestration
- Containerlab + Nokia SR Linux for lab environments
- pygnmi for gNMI operations
- Pydantic v2 for all data models
- Jinja2 for config template rendering

### Development Tooling
- Pre-commit hooks: ruff, detect-secrets, gitleaks
- Conventional commits: `feat:`, `fix:`, `docs:`, etc.
- Towncrier for changelog management
- Bandit for security scanning
- GitHub Actions CI/CD

### Excluded
- No Ansible (pure Python automation)
- No cloud-only dependencies (must run locally on macOS/OrbStack)
- No vendor-locked SoT formats

## Quality Standards

- All public functions require type hints
- Line length: 120 characters
- Pre-commit hooks must pass before every commit
- CI pipeline: lint -> security scan -> unit tests -> type check
- Each PR must include changelog fragment and test coverage
- Branch from `main`, PR to `main`

## Documentation

Two complementary documentation systems:

1. **SDD Specs** (`.specify/`) — What to build, consumed before coding
2. **Context Nuggets** (`dev/`) — How things work, consumed during coding
   - `dev/adr/` — Architecture Decision Records
   - `dev/commands/` — Reusable AI agent command workflows
   - `dev/guidelines/` — Coding standards and conventions
   - `dev/guides/` — Step-by-step procedures
   - `dev/knowledge/` — Architecture explanations by domain
   - `dev/skills/` — Domain-specific AI agent skills

`AGENTS.md` at the project root is the entry point for AI coding agents. `CLAUDE.md` provides quick reference for Claude Code.

## Governance

This Constitution supersedes all other development practices. Amendments require:
1. An Architecture Decision Record (ADR) documenting the change
2. Update to this Constitution
3. Review and approval

All PRs and code reviews must verify compliance with this Constitution.

**Version**: 1.1.0 | **Ratified**: 2026-04-14 | **Last Amended**: 2026-07-06 (ADR-0003: Core Principle VIII — Intent-First Correctness)
