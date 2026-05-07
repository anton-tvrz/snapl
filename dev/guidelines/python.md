# Python Coding Standards

## Testing

All new code follows TDD. Write a failing test, implement the minimum to pass, refactor.

- Tests live in `tests/` mirroring the source structure
- Use pytest fixtures from `conftest.py` for shared test data
- Unit tests: `tests/unit/` — no external dependencies, < 30s total
- Integration tests: `tests/integration/` — containerlab + Infrahub required
- E2E tests: `tests/e2e/` — full pipeline validation
- Golden file tests: `tests/golden/` — byte-for-byte Jinja2 template output comparison
- Coverage target: >=80% on new code
- Run tests: `uv run invoke test-unit` (quick) or `uv run pytest tests/ -v` (full)

## Tooling

| Tool | Purpose | Config Location |
|------|---------|-----------------|
| **ruff** | Linting + formatting (replaces black, isort, pylint, flake8) | `pyproject.toml [tool.ruff]` |
| **mypy** | Static type checking | `pyproject.toml [tool.mypy]` |
| **bandit** | Security scanning | `pyproject.toml [tool.bandit]` |
| **pytest** | Testing framework | `pyproject.toml [tool.pytest]` |

## Style Rules

- **Line length:** 120 characters
- **Quote style:** Double quotes (`"like this"`)
- **Indent:** 4 spaces (no tabs)
- **Line endings:** LF (Unix)
- **Python target:** 3.12+

## Import Ordering (ruff isort)

```python
# 1. Standard library
import os
import sys
from pathlib import Path

# 2. Third-party packages
import httpx
import yaml
from pydantic import BaseModel

# 3. First-party (our packages)
from snapl_intent.schemas import load_schemas
from snapl_executor.gnmi import deploy_config
from snapl_collector.state import get_running_config
from snapl_observability.drift import detect_drift
from snapl_orchestrator.workflows import DeployWorkflow
from snapl_presentation.cli import main
```

## Type Hints

- Required on all public functions and methods
- Use modern syntax: `str | None` instead of `Optional[str]`
- Use `from __future__ import annotations` for forward references
- Complex types: use `TypeAlias` or `TypeVar` as needed

```python
from __future__ import annotations

def get_device(name: str, timeout: int = 30) -> dict[str, str] | None:
    """Fetch device data from Infrahub."""
    ...
```

## Docstrings

- Required on all modules, classes, and public functions
- Use Google-style docstrings
- First line: single-sentence summary (imperative mood)

```python
def deploy_config(device_name: str, config: dict) -> bool:
    """Deploy configuration to a device via gNMI.

    Args:
        device_name: Name of the target device.
        config: SR Linux JSON configuration payload.

    Returns:
        True if deployment succeeded, False otherwise.

    Raises:
        ConnectionError: If the device is unreachable.
    """
```

## Error Handling

- Use specific exception types, not bare `except:`
- Log errors before re-raising
- Use `RuntimeError` for application-level errors
- Use custom exceptions for domain-specific errors

## Testing

- Test files: `tests/unit/test_<module>.py` or `tests/integration/test_<module>.py`
- Use `@pytest.mark.unit` and `@pytest.mark.integration` markers
- Fixtures in `tests/conftest.py` for shared test data
- Async tests: use `pytest-asyncio` (auto mode enabled)

## Commands

```bash
uv run invoke format    # Auto-format with ruff
uv run invoke lint      # Check linting with ruff
uv run invoke scan      # Security scan with bandit
```
