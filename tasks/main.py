"""Root tasks — format, lint, scan, check-all."""

from __future__ import annotations

from invoke import task
from invoke.exceptions import Exit

from .lab_runs import record_run
from .shared import execute_command


@task
def format_code(ctx):
    """Format all Python code with ruff."""
    print("Formatting code with ruff...")
    execute_command(ctx, "ruff check --fix .")
    execute_command(ctx, "ruff format .")
    print("Done.")


@task
def lint(ctx):
    """Lint all Python code with ruff."""
    print("Linting with ruff...")
    execute_command(ctx, "ruff check .")
    execute_command(ctx, "ruff format --check .")
    print("All checks passed.")


@task
def scan(ctx):
    """Run security scans (bandit + detect-secrets)."""
    print("Running bandit security scan...")
    execute_command(ctx, "bandit -r packages/ -c pyproject.toml", warn=True)
    print("\nRunning detect-secrets scan...")
    execute_command(ctx, "detect-secrets scan --baseline .secrets.baseline", warn=True)
    print("Security scans complete.")


@task
def test_unit(ctx):
    """Run all unit tests."""
    print("Running unit tests...")
    execute_command(
        ctx,
        "pytest tests/unit/ -v -m unit "
        "--cov=packages/intent/snapl_intent "
        "--cov=packages/executor/snapl_executor "
        "--cov=packages/collector/snapl_collector "
        "--cov=packages/observability/snapl_observability "
        "--cov=packages/orchestrator/snapl_orchestrator "
        "--cov=packages/presentation/snapl_presentation "
        "--cov-report=term-missing",
    )


@task
def test_e2e(ctx):
    """Run the end-to-end demo arc against a live stack (issue #100).

    Opt-in and destructive: it provisions the schema, seeds the Source of
    Truth, and pushes configuration to the lab devices. Bring the stack up
    first with `invoke demo.up`, and make sure INFRAHUB_ADDRESS points at
    snapl's own instance (see #107).
    """
    print("Running the end-to-end demo arc (seeds the SoT and writes to devices)...")
    _recorded(ctx, "e2e", "SNAPL_E2E=1 pytest tests/e2e -m e2e -v")


@task
def test_integration(ctx):
    """Run the integration tests against a live stack.

    They dial real devices and services, so the stack and the lab must be up
    first (`invoke demo.up`). Like test-e2e, each run is recorded under
    `.snapl/test-runs/` for the dashboard (spec 010 FR-026).
    """
    print("Running the integration tests against the live stack...")
    _recorded(ctx, "integration", "pytest tests/integration -m integration -v")


def _recorded(ctx, suite: str, command: str) -> None:
    """Run a live suite through the lab-run recorder, failing the task as pytest did."""
    code = record_run(ctx, suite, command)
    if code:
        raise Exit(code=code)


@task(pre=[lint, scan])
def check_all(ctx):
    """Run all quality checks (lint + security scan)."""
    print("\nAll quality checks complete.")
