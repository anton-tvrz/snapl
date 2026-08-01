"""Shared utilities for invoke tasks."""

from __future__ import annotations

import os
from pathlib import Path

# Project root directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The env file `docs/demo-scenarios.md` tells operators to create. Docker
# compose picks it up on its own because it sits beside the compose file; the
# invoke process has to be told, which is what `load_env_file` is for.
ENV_FILE = PROJECT_ROOT / "development" / ".env"

PACKAGES_DIR = PROJECT_ROOT / "packages"
TESTS_DIR = PROJECT_ROOT / "tests"

# NAF building block package directories
INTENT_DIR = PACKAGES_DIR / "intent"
EXECUTOR_DIR = PACKAGES_DIR / "executor"
COLLECTOR_DIR = PACKAGES_DIR / "collector"
OBSERVABILITY_DIR = PACKAGES_DIR / "observability"
ORCHESTRATOR_DIR = PACKAGES_DIR / "orchestrator"
PRESENTATION_DIR = PACKAGES_DIR / "presentation"


def load_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    """Load ``path`` into ``os.environ``, leaving already-set vars alone.

    Every task that talks to the stack — ``demo.seed``, ``demo.check``,
    ``orchestrator.start``, ``test-e2e`` — resolves its connection settings
    from ``os.environ``. Without this the documented setup path
    (``cp development/.env.example development/.env`` then
    ``uv run invoke demo.up``) fails on an unset ``INFRAHUB_API_TOKEN``, and
    the worker starts with no token and no device password.

    An existing environment variable always wins, so a one-off
    ``INFRAHUB_ADDRESS=... uv run invoke demo.check`` overrides the file
    rather than being silently ignored. Returns only what was applied.
    """
    if not path.is_file():
        return {}

    # dotenv rather than a hand-rolled split("="): the committed example
    # carries `NokiaSrl1!  # pragma: allowlist secret`, and an inline comment
    # parsed into the value becomes a failed gNMI authentication.
    from dotenv import dotenv_values  # noqa: PLC0415 — keeps `invoke --list` import-light

    applied = {}
    for key, value in dotenv_values(path).items():
        if value is not None and key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied


def execute_command(ctx, command: str, pty: bool = True, warn: bool = False, **kwargs):
    """Execute a command with consistent settings."""
    return ctx.run(command, pty=pty, warn=warn, **kwargs)
