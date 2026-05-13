# Contract: Executor

**Feature**: 002-executor-gnmi
**Date**: 2026-05-07
**Type**: Abstract Base Class (Python)

## Overview

The `Executor` ABC is the public interface of the NAF Executor building block. Orchestrator activities and other callers interact with the Executor exclusively through this contract. The concrete `GnmiExecutor` class implements this ABC for Nokia SR Linux via gNMI.

## Design Note: Results vs Exceptions

Unlike `IntentStore` (which raises domain exceptions for device/schema errors), `Executor` returns result objects for all device-side outcomes. This is intentional:

- Device unreachability is a **runtime condition**, not a caller mistake — it belongs in the result, not in control flow.
- Batch apply (US4) requires per-device outcomes — exception semantics would force a try/except per device on the caller side.
- Temporal activities work better with return values than exceptions for expected failure modes.

Python exceptions are still raised for: invalid constructor arguments, j2 template syntax errors (fatal, not runtime-recoverable), programming errors.

## Interface Definition

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from snapl_intent.models import DesiredState

from snapl_executor.models import ApplyResult, BatchResult, DryRunResult


class Executor(ABC):
    """NAF Executor building block — config deployment interface."""

    # ── Core Operations ──────────────────────────────────────────────────

    @abstractmethod
    async def apply(self, desired: DesiredState) -> ApplyResult:
        """Render and deploy the desired state to the target device via gNMI SET.

        Renders the DesiredState into a device-native payload, then issues a
        gNMI SET to the target device. Returns a result object indicating
        success or failure — does not raise for device-side errors.

        Args:
            desired: The complete desired state for one device (interfaces,
                BGP sessions, system config).

        Returns:
            ApplyResult with success=True if gNMI SET succeeded, or
            success=False with error detail if the device rejected the
            payload, was unreachable, or timed out.

        Raises:
            ValueError: desired is None or missing required fields (programming error)
        """

    @abstractmethod
    async def rollback(self, desired: DesiredState) -> ApplyResult:
        """Re-apply a prior known-good desired state to the target device.

        Semantically identical to apply() but sets ApplyResult.is_rollback=True
        so callers and audit logs can distinguish rollback from normal apply.

        Args:
            desired: The known-good desired state to restore.

        Returns:
            ApplyResult with is_rollback=True.

        Raises:
            ValueError: desired is None or missing required fields (programming error)
        """

    @abstractmethod
    async def dry_run(self, desired: DesiredState) -> DryRunResult:
        """Render the desired state and return the payload without applying it.

        Template rendering errors are caught and returned as DryRunResult
        with success=False. No gNMI connection is made.

        Args:
            desired: The desired state to render.

        Returns:
            DryRunResult with the rendered payload if rendering succeeded,
            or render_error if it failed. success=True means "render OK,
            no changes made". success=False means "render failed".

        Raises:
            ValueError: desired is None (programming error)
        """

    @abstractmethod
    async def apply_batch(
        self,
        states: list[DesiredState],
    ) -> BatchResult:
        """Apply desired state to multiple devices, returning per-device results.

        Applies each DesiredState independently. A failure on one device does
        not prevent application to other devices. Results are collected into a
        BatchResult map keyed by device UUID.

        Args:
            states: List of DesiredState objects, one per target device.

        Returns:
            BatchResult with per-device ApplyResult entries and aggregate
            summary counts.

        Raises:
            ValueError: states is empty or contains duplicate device IDs
        """
```

## Concrete Implementation: GnmiExecutor

```python
class GnmiExecutor(Executor):
    """Nokia SR Linux Executor implementation using gNMI (pygnmi).

    Args:
        host: Device hostname or IP address.
        port: gNMI port (SR Linux default: 57400).
        username: gNMI username.
        password: gNMI password.
        insecure: Skip TLS verification (default True for lab environments).
        timeout: gNMI operation timeout in seconds (default 30).
    """

    def __init__(
        self,
        *,
        host: str,
        port: int = 57400,
        username: str = "admin",
        password: str,
        insecure: bool = True,
        timeout: int = 30,
    ) -> None: ...
```

## Models

```python
from dataclasses import dataclass, field
from uuid import UUID


@dataclass(frozen=True)
class ApplyResult:
    device_id: UUID
    device_name: str
    success: bool
    payload: dict
    device_response: str | None = None
    error: str | None = None
    is_rollback: bool = False
    duration_ms: int = 0


@dataclass(frozen=True)
class DryRunResult:
    device_id: UUID
    device_name: str
    success: bool
    payload: dict | None = None
    render_error: str | None = None


@dataclass(frozen=True)
class BatchResult:
    results: dict[UUID, ApplyResult] = field(default_factory=dict)
    total: int = 0
    succeeded: int = 0
    failed: int = 0
```

## Exceptions

```python
class ExecutorError(Exception):
    """Base class for programming errors in the Executor module."""

class ExecutorRenderError(ExecutorError):
    """A Jinja2 template has a syntax error — fatal, not runtime-recoverable."""

class ExecutorConfigError(ExecutorError):
    """Invalid Executor configuration (missing credentials, bad timeout, etc.)."""
```

Note: `ExecutorRenderError` is raised at startup (template load time) or when a template has a hard syntax error. Missing template variables return `DryRunResult(success=False, render_error=...)` rather than raising.

## Consumer Notes

- **Orchestrator**: Call `apply()` per device within a Temporal activity. Check `result.success` to decide whether to signal the workflow for retry or compensation. Use `rollback()` for saga compensation steps.
- **Presentation**: Call `dry_run()` before `apply()` to show operators the rendered payload for review.
- **Integration with Intent**: Retrieve `DesiredState` objects from `IntentStore.get_desired_state()`, pass directly to `apply()`.

## Constitution Note

The constitution defines the Executor ABC as `apply(config)`, `rollback(config)`, `dry_run(config)`. This implementation uses `apply(desired: DesiredState)` with the intent model type for clarity. The constitution should be amended to reflect `DesiredState` as the parameter type (ADR update pending).
