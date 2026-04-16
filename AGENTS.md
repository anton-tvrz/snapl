# snapl — Agent Knowledge Map

> This file is the entry point for AI coding agents working on this project.
> It provides a comprehensive map of the codebase, conventions, and development workflow.

## What Is This Project?

**snapl** is a modular network automation prototype that implements the Network Automation Forum (NAF) Framework's six building blocks as independent Python packages. It automates the full lifecycle of network configuration changes across multiple use cases using:

- **Infrahub** (OpsMill) — Graph-based Source of Truth (SoT) for network inventory and intended state
- **Temporal** — Durable workflow orchestration engine for auditable automation workflows
- **Containerlab** — Nokia SR Linux virtual network labs running locally via Docker/OrbStack
- **GitHub Spec Kit** — Spec-Driven Development methodology governing all implementation

**NAF Loop:** Intent (desired state) -> Executor (deploy) -> Collector (verify) -> Observability (detect drift) -> Orchestrator (coordinate) -> back to Executor

**Use Cases:** Datacenter fabric, client edge, SD-WAN, WAN — all prototyped with Nokia SR Linux.

## Repository Structure

```
snapl/
├── .specify/                   # SDD artifacts (constitution, specs, contracts)
│   ├── memory/constitution.md  # Project constitution (governing document)
│   ├── specs/                  # Feature specifications (per module/use case)
│   ├── scripts/                # Automation scripts
│   └── templates/              # Spec/plan/task templates
│
├── packages/                   # NAF building block packages (uv workspace)
│   ├── intent/                 # snapl-intent: SoT, desired state, schemas
│   │   └── snapl_intent/
│   ├── executor/               # snapl-executor: config deployment (gNMI, Jinja2)
│   │   └── snapl_executor/
│   ├── collector/              # snapl-collector: live data retrieval
│   │   └── snapl_collector/
│   ├── observability/          # snapl-observability: drift, metrics, audit
│   │   └── snapl_observability/
│   ├── orchestrator/           # snapl-orchestrator: Temporal workflows
│   │   └── snapl_orchestrator/
│   └── presentation/           # snapl-presentation: CLI / API
│       └── snapl_presentation/
│
├── tests/                      # Centralized test suite
│   ├── conftest.py             # Shared fixtures
│   ├── unit/                   # Unit tests per package
│   ├── integration/            # Integration tests
│   └── e2e/                    # End-to-end tests
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
├── .claude/skills/             # Spec Kit skills for Claude Code
├── pyproject.toml              # Root workspace config (uv + all tool configs)
├── .pre-commit-config.yaml     # Pre-commit hooks (ruff, detect-secrets, gitleaks)
└── .github/                    # CI/CD workflows, PR/issue templates
```

## Workspace Architecture

This is a **uv workspace monorepo** with six packages mapped to NAF building blocks:

| Package              | Import Path            | NAF Block      | Description                                       |
| -------------------- | ---------------------- | -------------- | ------------------------------------------------- |
| `snapl-intent`       | `snapl_intent`         | Intent         | SoT interaction, desired state models, schemas    |
| `snapl-executor`     | `snapl_executor`       | Executor       | Config deployment via gNMI, Jinja2 templates      |
| `snapl-collector`    | `snapl_collector`      | Collector      | Live network data retrieval                       |
| `snapl-observability`| `snapl_observability`  | Observability  | Drift detection, metrics, audit logging           |
| `snapl-orchestrator` | `snapl_orchestrator`   | Orchestrator   | Temporal workflows + activities                   |
| `snapl-presentation` | `snapl_presentation`   | Presentation   | CLI / API user interface                          |

**Dependency direction** (no circular deps):
```
presentation -> orchestrator -> {intent, executor, collector, observability}
observability -> collector
executor -> intent
```

## Key Commands

```bash
# Setup
uv sync --all-groups                    # Install all dependencies

# Development
uv run invoke lint                      # Ruff lint check
uv run invoke format                    # Ruff format
uv run invoke scan                      # Security scan (bandit)

# Testing
uv run invoke test-unit                 # Run unit tests
uv run pytest tests/unit/ -m unit -v    # Direct pytest

# Spec-Driven Development
/speckit-constitution                   # View/update constitution
/speckit-specify                        # Create specification
/speckit-plan                           # Create implementation plan
/speckit-tasks                          # Generate task breakdown
/speckit-implement                      # Execute implementation
/speckit-analyze                        # Cross-artifact consistency check
```

## Development Methodology

### Spec-Driven Development (SDD)
Every module follows: Constitution -> Specify -> Plan -> Tasks -> Implement.
Constitution is at `.specify/memory/constitution.md`.

### Test-Driven Development (TDD)
Tests are written FIRST, then implementation. Red-Green-Refactor. 80% coverage minimum.

### NAF Contract-First
Each NAF block exposes an abstract base class + Pydantic models as its public interface:
- `IntentStore` ABC: `get_desired_state()`, `get_schema()`, `seed()`
- `Executor` ABC: `apply()`, `rollback()`, `dry_run()`
- `Collector` ABC: `collect()`, `get_running_config()`
- `Observer` ABC: `detect_drift()`, `emit_event()`, `log_audit()`
- Orchestrator: Temporal workflows composing the above

## Conventions

- **Commits:** Conventional commits (`feat:`, `fix:`, `docs:`, etc.)
- **Branching:** Feature branches from `main`, PR to `main`
- **Changelog:** Towncrier fragments in `changelog/` (e.g., `42.added.md`)
- **Line length:** 120 characters
- **Python:** 3.12+, type hints on public functions
- **Formatting:** Ruff (double quotes, space indent, LF line endings)

## Lineage

snapl inherits patterns and code from [project-network-synapse-quattro](https://github.com/anton-tvrz/project-network-synapse-quattro), a network automation platform for Nokia SR Linux datacenter fabrics. Key elements ported:
- Infrahub SoT integration patterns
- Temporal workflow architecture
- Containerlab topologies
- gNMI deployment scripts
- TDD methodology and test fixtures
- CI/CD pipeline structure
- Context Nuggets documentation pattern
