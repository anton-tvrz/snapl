"""Invoke task runner — unified CLI for development commands.

Usage:
    uv run invoke --list          # Show all available tasks
    uv run invoke format          # Format all code
    uv run invoke lint            # Lint all code
    uv run invoke test-unit       # Run all unit tests
"""

from invoke import Collection

from .shared import load_env_file

# Before the task modules resolve anything from os.environ. docker compose
# reads development/.env on its own; this is what makes the same file reach
# demo.seed, demo.check, orchestrator.start and test-e2e.
load_env_file()

from . import demo, dev, main, orchestrator  # noqa: E402 — must follow load_env_file

ns = Collection()

# Root-level tasks (format, lint, scan, test-unit, check-all)
ns.add_task(main.format_code, name="format")
ns.add_task(main.lint, name="lint")
ns.add_task(main.scan, name="scan")
ns.add_task(main.test_unit, name="test-unit")
ns.add_task(main.test_e2e, name="test-e2e")
ns.add_task(main.check_all, name="check-all")

# Dev environment namespace (deps, stop, down)
ns.add_collection(Collection.from_module(dev, name="dev"))

# Orchestrator namespace
ns.add_collection(Collection.from_module(orchestrator, name="orchestrator"))

# Demo namespace (up, seed, check, reset) — see docs/demo-scenarios.md
ns.add_collection(Collection.from_module(demo, name="demo"))
