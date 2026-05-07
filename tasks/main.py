"""Root tasks — format, lint, scan, check-all."""

from __future__ import annotations

from invoke import task

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


@task(pre=[lint, scan])
def check_all(ctx):
    """Run all quality checks (lint + security scan)."""
    print("\nAll quality checks complete.")
