"""Shared utilities for invoke tasks."""

from __future__ import annotations

from pathlib import Path

# Project root directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent

PACKAGES_DIR = PROJECT_ROOT / "packages"
TESTS_DIR = PROJECT_ROOT / "tests"

# NAF building block package directories
INTENT_DIR = PACKAGES_DIR / "intent"
EXECUTOR_DIR = PACKAGES_DIR / "executor"
COLLECTOR_DIR = PACKAGES_DIR / "collector"
OBSERVABILITY_DIR = PACKAGES_DIR / "observability"
ORCHESTRATOR_DIR = PACKAGES_DIR / "orchestrator"
PRESENTATION_DIR = PACKAGES_DIR / "presentation"


def execute_command(ctx, command: str, pty: bool = True, warn: bool = False, **kwargs):
    """Execute a command with consistent settings."""
    return ctx.run(command, pty=pty, warn=warn, **kwargs)
