"""Invoke task runner — unified CLI for development commands.

Usage:
    uv run invoke --list          # Show all available tasks
    uv run invoke format          # Format all code
    uv run invoke lint            # Lint all code
    uv run invoke test-unit       # Run all unit tests
"""

from invoke import Collection

from . import main

ns = Collection()

# Root-level tasks (format, lint, scan, test-unit, check-all)
ns.add_task(main.format_code, name="format")
ns.add_task(main.lint, name="lint")
ns.add_task(main.scan, name="scan")
ns.add_task(main.test_unit, name="test-unit")
ns.add_task(main.check_all, name="check-all")
