# Running Tests

## Quick Reference

```bash
# Run all unit tests
uv run invoke test-unit

# Run all unit tests with verbose output
uv run pytest tests/unit/ -m unit -v

# Run a specific test file
uv run pytest tests/unit/test_intent_store.py -x

# Run a specific test by name
uv run pytest tests/unit/test_intent_store.py -k "test_get_desired_state" -v

# Run integration tests (requires containerlab + Infrahub)
uv run pytest tests/integration/ -m integration -v

# Run e2e tests (requires full stack)
uv run pytest tests/e2e/ -m e2e -v

# Run with coverage
uv run pytest tests/unit/ --cov=packages --cov-report=term-missing
```

## Test Structure

Tests are centralized in the `tests/` directory, organized by scope:

```
tests/
├── conftest.py                     # Shared fixtures for all tests
├── unit/                           # Fast, no external deps (< 30s total)
│   ├── test_intent_store.py        # Tests for snapl_intent
│   ├── test_executor_gnmi.py       # Tests for snapl_executor
│   ├── test_collector_state.py     # Tests for snapl_collector
│   ├── test_observer_drift.py      # Tests for snapl_observability
│   └── test_deploy_workflow.py     # Tests for snapl_orchestrator
├── integration/                    # Requires containerlab + Infrahub
│   └── test_full_deploy.py
└── e2e/                            # Full pipeline validation
    └── test_pipeline.py
```

## Test File Naming Convention

| Source Location | Test File |
|-----------------|-----------|
| `packages/intent/snapl_intent/<module>.py` | `tests/unit/test_<module>.py` |
| `packages/executor/snapl_executor/<module>.py` | `tests/unit/test_<module>.py` |
| `packages/collector/snapl_collector/<module>.py` | `tests/unit/test_<module>.py` |
| `packages/observability/snapl_observability/<module>.py` | `tests/unit/test_<module>.py` |
| `packages/orchestrator/snapl_orchestrator/<module>.py` | `tests/unit/test_<module>.py` |
| `packages/presentation/snapl_presentation/<module>.py` | `tests/unit/test_<module>.py` |

## Pytest Markers

Use markers to categorize tests:

```python
import pytest

@pytest.mark.unit
def test_something_fast():
    """Unit test — no external dependencies."""
    ...

@pytest.mark.integration
def test_something_with_infrahub():
    """Integration test — requires Infrahub running."""
    ...

@pytest.mark.e2e
def test_full_pipeline():
    """End-to-end test — requires full stack."""
    ...
```

## Shared Fixtures

Common fixtures are defined in `tests/conftest.py`:

```python
# Example fixtures available to all tests:
# - spine_leaf_topology: 3-node topology data (spine01, leaf01, leaf02)
# - mock_infrahub_client: Mocked Infrahub async client
# - sample_device_config: SR Linux JSON config payload
```

## TDD Workflow

Always follow Red-Green-Refactor:

```bash
# Step 1: Write failing test
uv run pytest tests/unit/test_new_module.py -x    # Expect RED

# Step 2: Implement minimum code to pass

# Step 3: Run test again
uv run pytest tests/unit/test_new_module.py -x    # Expect GREEN

# Step 4: Run full suite to check for regressions
uv run invoke test-unit                            # All GREEN

# Step 5: Refactor, re-run after each change
```

## Coverage

- Target: >= 80% line coverage on new code
- CI enforces this threshold
- Generate a coverage report:

```bash
uv run pytest tests/unit/ --cov=packages --cov-report=html
# Open htmlcov/index.html in browser
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `ModuleNotFoundError` for a package | Run `uv sync --all-groups` to install workspace packages |
| Integration tests fail to connect | Ensure containerlab is running: `docker ps \| grep clab` |
| Slow test discovery | Use `-x` flag to stop on first failure |
| Async test hangs | Check `pytest-asyncio` mode in `pyproject.toml` (should be `auto`) |
