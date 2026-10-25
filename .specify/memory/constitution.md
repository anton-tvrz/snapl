# snapl Constitution

## Vision

snapl is a network automation prototype that runs a closed **intent → deploy → verify → drift → reconcile** loop against a real Nokia SR Linux fabric. It is built as the Network Automation Forum (NAF) Framework's six building blocks, each an independent Python package.

Every claim snapl makes is demonstrated in its lab. Anything not yet demonstrated is listed as roadmap or as a known limitation, never implied.

The project combines the NAF Framework for architecture, GitHub Spec Kit for development methodology, and patterns proven in project-network-synapse-quattro for implementation.

## Core Principles

### I. NAF Alignment

Every component has one owning NAF building block:

1. **Intent** — stores and manages desired network state through structured APIs
2. **Executor** — applies network changes via write interfaces (gNMI)
3. **Collector** — retrieves live network data using read protocols
4. **Observability** — detects discrepancies, generates events, keeps history
5. **Orchestrator** — coordinates automation workflows across components
6. **Presentation** — provides user interfaces for system interaction

Where a component deliberately sits outside the block NAF would assign it, an ADR records the deviation and why. Current deviations (ADR-0004): durable audit persistence and the SR Linux collect-to-diff adapter both live in the Orchestrator, and Presentation reads the Source of Truth directly to resolve device names.

The NAF feedback loop (Intent → Executor → Collector → Observability → Orchestrator → Executor) is the primary architectural pattern. Components communicate through abstract base classes and Pydantic models. No circular dependencies between packages.

Dependency direction:
- `presentation` → `orchestrator` → {`intent`, `executor`, `collector`, `observability`}
- `observability` → {`collector`, `intent`}
- `executor` → `intent`
- `collector` → `intent`

Any package may import another block's models and exceptions. Those are shared data contracts, and results flow through the loop in them. Importing another block's behaviour (stores, executors, collectors, observers) follows the arrows above.

### II. Spec-Driven Development

A specification is required before implementing:

- a new NAF block,
- a new capability, or
- any change that writes to a device or to the Source of Truth in a way the system did not before.

The workflow is Specify → Plan → Tasks → Implement, governed by this constitution.

Fixes, hardening and refactors do not need a spec. They need an issue that states the reproduction or the finding.

Specifications are living documents while their feature is in progress, and historical records once it ships. Where an old spec conflicts with this constitution, the constitution wins.

### III. Test-Driven Development

TDD is mandatory:

1. Write the test first.
2. Follow Red-Green-Refactor: failing test → minimum implementation → refactor.
3. Never create a source file without its corresponding test file.
4. Use pytest fixtures from `tests/conftest.py` for shared test data.
5. Coverage stays at or above 80% on new code (enforced in CI).

Coverage is a regression net, not a measure of correctness. Mocks encode assumptions, and most of snapl's bugs lived where an assumption about an external system was wrong. So:

- **Boundary changes carry live evidence.** A PR that touches a system boundary (Infrahub response shapes, gNMI payloads, templates, the drift diff, the collect adapter) states in its body what was run against the live lab and what was observed.
- **Bugs found live leave a fixture behind.** Every bug found on a live run adds a captured real payload as a test fixture, so the unit suite learns the boundary's true shape.

Test markers: `unit`, `integration`, `live`, `e2e`, `slow`. Unit tests require no external infrastructure.

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

Each package must be independently testable with unit tests.

### V. Contract-First Interfaces

Each block exposes one abstract base class plus Pydantic models as its public interface. The contract is defined before the implementation. The Orchestrator composes blocks through these contracts, and only its worker bootstrap names the concrete implementations.

Each package's `abc.py` is the source of truth for its contract. This document does not repeat method signatures.

For asynchronous events (drift detected, config changed), Temporal signals and activities serve as the event mechanism.

### VI. Single Vendor, Named Seams

snapl is single-vendor (Nokia SR Linux via Containerlab) and built to take a second. gNMI is the device interface.

Vendor-specific code lives only in named seams:

- concrete block implementations (`GnmiExecutor`, `GnmiCollector`)
- Jinja templates
- the drift field map
- the collect-to-diff adapter

Workflows and models never name a vendor.

snapl claims to be multi-vendor only once a second vendor runs the full loop in the lab. Until then, no abstraction is built for a vendor that does not exist (Principle VII). How multi-vendor support will be structured is an open question, recorded in ADR-0004.

### VII. Simplicity

Start simple, add complexity only when needed. Three similar lines of code are better than a premature abstraction. No speculative features, no unnecessary configurability. Each module should do one thing well.

### VIII. Intent-First Correctness

"Source of Truth" conflates two concepts that must never be merged: **intent** (what the network is supposed to look like) and **operational state** (what it actually looks like right now). Drift is the gap between them, and it is daily reality, not an edge case. (See ADR-0003.)

1. **Authority split** — The SoT is authoritative for intended state; the live network is authoritative only for operational reality. Device state is never silently promoted to truth: no automatic reverse-sync of running config into the SoT. Promoting observed state into intent is an explicit, reviewed operation. A manual device change that "works" is still drift. Intent attributes and operational addressing are distinct even when they look alike (e.g. `management_ip` is intent data for rendering; the gNMI dial target is operational addressing resolved at call time).
2. **Drift response is part of drift detection** — Every drift-detection path has a defined response: `report` (default), `remediate` (operator-triggered or explicitly automated), or `suppress` (e.g. maintenance windows). Detecting drift without a defined response is an incomplete feature. Auto-remediation is never the silent default.
3. **Intent extends beyond configuration** — Intent may declare operational expectations (BGP sessions established, interfaces oper-up, thresholds), not only config. Verification compares reality against intent; liveness alone never passes verification ("operationally up" ≠ "correct").
4. **Executor idempotency** — `Executor.apply()` is idempotent: applying the same intent twice yields the same device state with no additional change. Workflows may safely retry applies. Destructive or uncertain changes go through `dry_run()` first.
5. **Undesired config** — Configuration present on a device and absent from intent is drift, and it is reported. Removing it is a destructive change: never automatic, always preceded by `dry_run()`, and never applied to the protected set (management and system interfaces). (See spec 007.)

## Use Cases

### Datacenter Fabric (current)
Spine-leaf eBGP underlay on Nokia SR Linux. Demonstrates the full NAF feedback loop end to end.

### Site Edge (next)
A manufacturing site edge on SR Linux: office and OT segments, and a WAN uplink over eBGP to a carrier router. Intent includes a segmentation rule (the OT segment cannot reach the WAN except a named subnet), so drift on a security control is detected and reconciled like any other drift. Tracked in #117.

### Roadmap (not started)
SD-WAN (hub/spoke overlay) and WAN backbone (IS-IS/MPLS-style underlay, traffic engineering).

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
- CI quality gates (lint and format, mypy, bandit, secrets detection, YAML lint, lock check, unit tests with coverage) run as parallel jobs behind a single quality gate, and all must pass
- Each PR links its issue (`Closes #N`) and includes a changelog fragment, or the `skip-changelog` label for docs-, CI- or test-only changes
- Boundary-touching PRs include live evidence (Principle III)
- Branch from `main`, PR to `main`

## Documentation

Two complementary documentation systems:

1. **SDD specs** (`specs/`) — what to build, consumed before coding
2. **Context Nuggets** (`dev/`) — how things work, consumed during coding
   - `dev/adr/` — Architecture Decision Records
   - `dev/commands/` — Reusable AI agent command workflows
   - `dev/guidelines/` — Coding standards and conventions
   - `dev/guides/` — Step-by-step procedures
   - `dev/knowledge/` — Architecture explanations by domain
   - `dev/skills/` — Domain-specific AI agent skills

`AGENTS.md` at the project root is the entry point for AI coding agents. `CLAUDE.md` provides quick reference for Claude Code.

## Governance

This constitution is the standing brief for the AI agents that work on snapl and for its maintainer. Specs, plans and code follow it. Where they conflict with it, it wins.

Amendments require:
1. An ADR recording the change and why
2. A version bump here (major for removed or redefined principles, minor for added ones, patch for wording)
3. A PR the maintainer merges

**Version**: 2.0.0 | **Ratified**: 2026-04-14 | **Last Amended**: 2026-10-25 (ADR-0004: claim only what is true)
