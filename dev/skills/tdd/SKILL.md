# TDD — Test-Driven Development Skill

## Metadata
- name: tdd
- triggers: test, testing, TDD, pytest, coverage, red-green-refactor
- project: snapl

## Core Principle

All new code follows TDD: write a failing test, implement the minimum to pass, refactor. Tests are not optional, not deferred, and not written after implementation.

## The TDD Cycle

| Phase | Action | Example |
|-------|--------|---------|
| **RED** | Write a failing test defining expected behaviour | `test_intent_store_returns_desired_state()` — fails because implementation doesn't exist |
| **GREEN** | Write minimum code to make the test pass | Implement just enough logic. No optimisation, no edge cases |
| **REFACTOR** | Improve code while keeping tests green | Extract reusable functions, add type hints, simplify |

## Test File Naming Convention

| Source File | Test File |
|-------------|-----------|
| `packages/intent/snapl_intent/<name>.py` | `tests/unit/test_intent/test_<name>.py` |
| `packages/executor/snapl_executor/<name>.py` | `tests/unit/test_executor/test_<name>.py` |
| `packages/collector/snapl_collector/<name>.py` | `tests/unit/test_collector/test_<name>.py` |
| `packages/observability/snapl_observability/<name>.py` | `tests/unit/test_observability/test_<name>.py` |
| `packages/orchestrator/snapl_orchestrator/workflows/<name>.py` | `tests/unit/test_orchestrator/test_<name>_workflow.py` |
| `packages/orchestrator/snapl_orchestrator/activities/<name>.py` | `tests/unit/test_orchestrator/test_<name>_activities.py` |
| `packages/presentation/snapl_presentation/<name>.py` | `tests/unit/test_presentation/test_<name>.py` |

## Pytest Fixture Patterns

Use shared fixtures from `tests/conftest.py`:
- `sample_device_config` — Sample device configuration dict
- More fixtures added as modules are built

## Coverage Requirements

- Minimum **80%** line coverage on new code
- CI gate: `coverage report --fail-under=80`
- Coverage report generated on every test run

## Golden File Testing (Jinja2 Templates)

For config generation templates in `packages/executor/`:
1. Store expected output in `tests/golden/<template_name>.json`
2. Test renders template with known inputs
3. Compare output byte-for-byte against golden file
4. Any change causes test failure — developer must explicitly update the golden file

## Temporal Workflow Testing

Use `temporalio.testing.WorkflowEnvironment`:
- Unit test workflows without a running Temporal server
- Mock activities to test workflow logic in isolation
- Use time-skipping for timer-based workflows
- Test saga compensation by making activities raise errors

## TDD Workflow Commands

```bash
# Step 1: Run new test (expect RED)
uv run pytest tests/unit/test_intent/test_new_feature.py -x

# Step 2: Implement minimum code

# Step 3: Run test again (expect GREEN)
uv run pytest tests/unit/test_intent/test_new_feature.py -x

# Step 4: Run full suite (expect all GREEN)
uv run invoke test-unit
```
