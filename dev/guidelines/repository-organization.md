# Repository Organization

## Overview

snapl is a **uv workspace monorepo** where each top-level concern maps to a NAF (Network Automation Forum) building block, implemented as an independent Python package.

## Directory Structure

```
snapl/
├── packages/                   # NAF building block packages
│   ├── intent/                 # snapl-intent: SoT, desired state, schemas
│   │   ├── pyproject.toml
│   │   └── snapl_intent/
│   ├── executor/               # snapl-executor: config deployment (gNMI, Jinja2)
│   │   ├── pyproject.toml
│   │   └── snapl_executor/
│   ├── collector/              # snapl-collector: live data retrieval
│   │   ├── pyproject.toml
│   │   └── snapl_collector/
│   ├── observability/          # snapl-observability: drift, metrics, audit
│   │   ├── pyproject.toml
│   │   └── snapl_observability/
│   ├── orchestrator/           # snapl-orchestrator: Temporal workflows
│   │   ├── pyproject.toml
│   │   └── snapl_orchestrator/
│   └── presentation/           # snapl-presentation: CLI / API
│       ├── pyproject.toml
│       └── snapl_presentation/
│
├── tests/                      # Centralized test suite
│   ├── conftest.py             # Shared fixtures
│   ├── unit/                   # Unit tests (no external deps)
│   ├── integration/            # Integration tests (containerlab + Infrahub)
│   └── e2e/                    # End-to-end pipeline tests
│
├── containerlab/               # Lab topologies per use case
├── development/                # Docker Compose, monitoring stack
├── changelog/                  # Towncrier changelog fragments
├── tasks/                      # Invoke task runner modules
│
├── dev/                        # Developer documentation (Context Nuggets)
│   ├── adr/                    # Architecture Decision Records
│   ├── commands/               # Reusable AI agent commands
│   ├── guidelines/             # Coding standards and conventions
│   ├── guides/                 # Step-by-step procedures
│   ├── knowledge/              # Architecture explanations
│   ├── prompts/                # Prompt templates
│   └── skills/                 # AI agent skills
│
├── .specify/                   # SDD artifacts (constitution, specs, contracts)
├── pyproject.toml              # Root workspace config
└── AGENTS.md                   # AI agent entry point
```

## NAF Block Mapping

Each package in `packages/` corresponds to one of the six NAF building blocks:

| NAF Block        | Package Directory      | Import Path            | Responsibility                           |
| ---------------- | ---------------------- | ---------------------- | ---------------------------------------- |
| **Intent**       | `packages/intent/`     | `snapl_intent`         | SoT interaction, desired state, schemas  |
| **Executor**     | `packages/executor/`   | `snapl_executor`       | Config deployment via gNMI, Jinja2       |
| **Collector**    | `packages/collector/`  | `snapl_collector`      | Live network data retrieval              |
| **Observability**| `packages/observability/` | `snapl_observability` | Drift detection, metrics, audit logging |
| **Orchestrator** | `packages/orchestrator/` | `snapl_orchestrator`  | Temporal workflows + activities          |
| **Presentation** | `packages/presentation/` | `snapl_presentation`  | CLI / API user interface                 |

## Dependency Direction

Dependencies flow in a strict direction to prevent circular imports:

```
presentation -> orchestrator -> {intent, executor, collector, observability}
observability -> collector
executor -> intent
```

**Rules:**
- A package may only depend on packages to its right or below in the dependency graph
- `intent` and `collector` are leaf packages with no first-party dependencies
- `orchestrator` composes all other blocks but none depend on it (except `presentation`)
- Circular dependencies between packages are forbidden

## Contract-First Design

Each NAF block exposes its public interface through:

1. **Abstract Base Class (ABC)** — defines the contract (methods, signatures, docstrings)
2. **Pydantic models** — defines the data shapes passed between blocks
3. **Concrete implementations** — fulfill the ABC contract for specific backends

This means:
- Tests can be written against the ABC before any implementation exists
- Implementations can be swapped (e.g., different SoT backends) without changing consumers
- The Orchestrator depends on ABCs, not concrete implementations

## Test Organization

Tests are centralized in `tests/` (not inside each package):

```
tests/
├── conftest.py                     # Shared fixtures
├── unit/
│   ├── test_intent_store.py        # Tests for snapl_intent
│   ├── test_executor_gnmi.py       # Tests for snapl_executor
│   ├── test_collector_state.py     # Tests for snapl_collector
│   ├── test_observer_drift.py      # Tests for snapl_observability
│   └── test_deploy_workflow.py     # Tests for snapl_orchestrator
├── integration/
│   └── test_full_deploy.py         # Cross-package integration
└── e2e/
    └── test_pipeline.py            # Full pipeline validation
```

## Adding a New Package

If a new NAF concern emerges that doesn't fit existing blocks:

1. Create `packages/<name>/` with its own `pyproject.toml`
2. Add it to the uv workspace in the root `pyproject.toml`
3. Define the ABC and Pydantic models first
4. Write tests before implementation
5. Document the decision in a new ADR under `dev/adr/`
