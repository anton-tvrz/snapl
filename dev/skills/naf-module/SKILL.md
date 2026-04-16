# NAF Module — Building NAF-Aligned Modules Skill

## Metadata
- name: naf-module
- triggers: NAF, building block, new module, package, ABC, contract, interface
- project: snapl

## Overview

Every module in snapl maps to one of the six NAF building blocks. This skill describes how to design, spec, and implement a new NAF module following SDD and TDD.

## The Six NAF Building Blocks

| Block | Package | ABC | Responsibility |
|-------|---------|-----|----------------|
| Intent | `snapl_intent` | `IntentStore` | Desired state, SoT interaction |
| Executor | `snapl_executor` | `Executor` | Config deployment via gNMI |
| Collector | `snapl_collector` | `Collector` | Live data retrieval |
| Observability | `snapl_observability` | `Observer` | Drift detection, audit |
| Orchestrator | `snapl_orchestrator` | (Temporal) | Workflow coordination |
| Presentation | `snapl_presentation` | (CLI/API) | User interface |

## Dependency Direction (No Circular Deps)

```
presentation -> orchestrator -> {intent, executor, collector, observability}
observability -> collector
executor -> intent
```

## SDD Workflow for New Modules

1. **Specify** (`/speckit-specify`): Define what the module does, acceptance criteria, contracts
2. **Plan** (`/speckit-plan`): Choose technology, define architecture
3. **Tasks** (`/speckit-tasks`): Break into ordered, testable tasks
4. **Implement** (`/speckit-implement`): Execute with TDD

## Contract Pattern

Every NAF module exposes an abstract base class (ABC) as its public contract:

```python
from abc import ABC, abstractmethod
from pydantic import BaseModel

class DeviceConfig(BaseModel):
    """Pydantic model shared across NAF blocks."""
    hostname: str
    platform: str
    config: dict

class Executor(ABC):
    """NAF Executor contract — apply config changes to devices."""

    @abstractmethod
    async def apply(self, config: DeviceConfig) -> None:
        """Apply configuration to a device."""

    @abstractmethod
    async def rollback(self, config: DeviceConfig) -> None:
        """Rollback configuration on a device."""

    @abstractmethod
    async def dry_run(self, config: DeviceConfig) -> dict:
        """Preview what would change without applying."""
```

## Package Structure

```
packages/<block>/
├── pyproject.toml
└── snapl_<block>/
    ├── __init__.py
    ├── base.py          # ABC definition (contract)
    ├── models.py         # Pydantic models
    └── <impl>.py         # Concrete implementation
```

## TDD for NAF Modules

1. Write test for the ABC contract first
2. Write test for the concrete implementation
3. Implement the ABC
4. Implement the concrete class
5. Verify all tests pass

## Common Mistakes

- Creating a module that spans multiple NAF blocks
- Circular dependency between packages
- Not defining the ABC before starting implementation
- Tight coupling to a specific vendor (use driver pattern)
- Skipping the SDD spec phase
