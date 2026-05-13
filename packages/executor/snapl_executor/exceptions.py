"""Executor domain exceptions (T006).

ExecutorError and subclasses are raised for programming errors only.
Device-side failures (unreachable, rejected config, timeout) are returned
as ApplyResult/DryRunResult objects — not exceptions.
"""

from __future__ import annotations


class ExecutorError(Exception):
    """Base class for programming errors in the Executor module."""


class ExecutorRenderError(ExecutorError):
    """A Jinja2 template has a hard syntax error — fatal at load time."""


class ExecutorConfigError(ExecutorError):
    """Invalid Executor configuration (missing credentials, bad timeout, etc.)."""
