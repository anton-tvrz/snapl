# Adding a NAF Module

This guide walks through how to specify and build a new module within one of the NAF building block packages using the SDD (Spec-Driven Development) workflow.

## Prerequisites

- Read `dev/knowledge/naf-framework.md` to understand the building blocks
- Read `dev/knowledge/sdd-workflow.md` to understand the SDD process
- Identify which NAF block your module belongs to (Intent, Executor, Collector, Observability, Orchestrator, Presentation)

## Step 1: Specify

Create a specification for the new module using the SDD `/speckit-specify` command.

The specification must define:
- **Purpose** — what problem the module solves within its NAF block
- **Interface** — the ABC methods or Pydantic models it exposes
- **Dependencies** — which other packages/modules it depends on
- **Constraints** — performance, security, or compatibility requirements

Output: `.specify/specs/<block>/<module-name>.md`

## Step 2: Plan

Create an implementation plan using `/speckit-plan`.

The plan must include:
- File locations for the ABC, models, and concrete implementation
- Test file locations
- Dependency changes (if the module introduces new third-party deps)
- Integration points with other NAF blocks

Output: `.specify/specs/<block>/<module-name>-plan.md`

## Step 3: Create the ABC

Define the abstract base class in the target package:

```python
# packages/<block>/snapl_<block>/<module_name>.py
from __future__ import annotations

from abc import ABC, abstractmethod
from pydantic import BaseModel


class MyModuleInput(BaseModel):
    """Input model for the module operation."""
    device_name: str
    # ... other fields


class MyModuleResult(BaseModel):
    """Result model returned by the module."""
    success: bool
    # ... other fields


class MyModuleBase(ABC):
    """Abstract base class defining the module contract.

    Implementations must provide all abstract methods.
    """

    @abstractmethod
    async def execute(self, input_data: MyModuleInput) -> MyModuleResult:
        """Execute the module operation.

        Args:
            input_data: The input parameters for the operation.

        Returns:
            The result of the operation.
        """
        ...
```

## Step 4: Write Tests First (TDD)

Create the test file before writing any implementation:

```python
# tests/unit/test_<module_name>.py
"""Tests for <module_name> in snapl_<block>."""
import pytest

from snapl_<block>.<module_name> import MyModuleBase, MyModuleInput, MyModuleResult


class TestMyModule:
    """Test the module contract and implementation."""

    def test_input_model_validates(self):
        """Input model accepts valid data."""
        input_data = MyModuleInput(device_name="spine01")
        assert input_data.device_name == "spine01"

    def test_input_model_rejects_invalid(self):
        """Input model rejects invalid data."""
        with pytest.raises(ValueError):
            MyModuleInput(device_name="")  # if validation exists

    @pytest.mark.asyncio
    async def test_execute_returns_result(self, my_module_instance):
        """Execute returns a valid result."""
        input_data = MyModuleInput(device_name="spine01")
        result = await my_module_instance.execute(input_data)
        assert isinstance(result, MyModuleResult)
        assert result.success is True
```

Run the tests to confirm they fail (RED):

```bash
uv run pytest tests/unit/test_<module_name>.py -x
```

## Step 5: Generate Tasks

Use `/speckit-tasks` to break down the implementation into concrete sub-tasks.

## Step 6: Implement

Write the concrete implementation using `/speckit-implement` or manually:

```python
# packages/<block>/snapl_<block>/<module_name>_impl.py
from __future__ import annotations

from snapl_<block>.<module_name> import MyModuleBase, MyModuleInput, MyModuleResult


class MyModuleImpl(MyModuleBase):
    """Concrete implementation of MyModule."""

    async def execute(self, input_data: MyModuleInput) -> MyModuleResult:
        """Execute the module operation."""
        # Implementation here
        return MyModuleResult(success=True)
```

Run the tests again to confirm they pass (GREEN):

```bash
uv run pytest tests/unit/test_<module_name>.py -x
```

## Step 7: Refactor

- Extract shared logic
- Add comprehensive type hints
- Improve docstrings
- Run tests after each refactor step

## Step 8: Quality Checks

```bash
uv run invoke format    # Format code
uv run invoke lint      # Lint code
uv run invoke scan      # Security scan
```

## Step 9: Document

- Export the module in the package's `__init__.py`
- Add changelog fragment if user-facing
- Update `dev/knowledge/` if the module changes architecture understanding

## Checklist

- [ ] Specification created in `.specify/specs/`
- [ ] ABC defined with Pydantic models
- [ ] Tests written BEFORE implementation
- [ ] All tests pass (GREEN)
- [ ] Code formatted and linted
- [ ] Docstrings on all public interfaces
- [ ] Changelog fragment added (if user-facing)
- [ ] PR opened with conventional commit message
