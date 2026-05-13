# Contract: Collector

**Feature**: 003-collector-gnmi
**Date**: 2026-05-13
**Type**: Abstract Base Class (Python)

## Overview

The `Collector` ABC is the public interface of the NAF Collector building block. The Observability module, Orchestrator activities, and operators interact with the Collector exclusively through this contract. The concrete `GnmiCollector` class implements this ABC for Nokia SR Linux via gNMI.

## Design Note: Results vs Exceptions

Consistent with the `Executor` ABC, `Collector` returns result objects for all device-side outcomes. Device unreachability, authentication failure, timeouts, and parse errors are returned as `CollectResult(success=False, error=...)` rather than raised exceptions. This is required for:

- **Batch collect** (US3): one device failure must not abort collection from other devices.
- **Observability integration**: the caller needs a structured result for each device to perform drift analysis — an exception would discard the partial results.
- **Orchestrator compatibility**: Temporal activities work better with return values than exceptions for expected failure modes.

Python exceptions are still raised for: invalid constructor arguments, programming errors (empty path list, duplicate devices in batch).

## Interface Definition

```python
from __future__ import annotations

from abc import ABC, abstractmethod

from snapl_intent.models import Device

from snapl_collector.models import BatchCollectResult, CollectResult


class Collector(ABC):
    """NAF Collector building block — live data retrieval interface."""

    # ── Core Operations ──────────────────────────────────────────────────

    @abstractmethod
    async def collect(self, device: Device, paths: list[str]) -> CollectResult:
        """Retrieve data at the specified YANG paths from a device via gNMI GET.

        Issues a gNMI GET for the given paths and returns the collected data
        as a dict keyed by YANG path. Returns a result object — does not raise
        for device-side errors (connectivity, auth, timeout, parse).

        Args:
            device: The target device descriptor (from snapl_intent).
            paths: One or more YANG path strings to retrieve. Must be non-empty.

        Returns:
            CollectResult with success=True and data dict if GET succeeded,
            or success=False with error detail if the device was unreachable,
            rejected auth, timed out, or returned an unparseable response.

        Raises:
            ValueError: paths is empty (programming error — validation precedes connection)
        """

    @abstractmethod
    async def get_running_config(self, device: Device) -> CollectResult:
        """Retrieve the complete running configuration of a device via gNMI GET.

        Issues a gNMI GET at the root path ("/") and returns the full device
        configuration tree as structured data. Equivalent to
        collect(device, paths=["/"]) with a fixed root path.

        Args:
            device: The target device descriptor (from snapl_intent).

        Returns:
            CollectResult with success=True and the full config dict if GET
            succeeded, or success=False with error detail on failure.
        """

    @abstractmethod
    async def collect_batch(
        self,
        devices: list[Device],
        paths: list[str],
    ) -> BatchCollectResult:
        """Collect data from multiple devices concurrently.

        Retrieves the specified paths from each device in parallel. A failure
        on one device does not prevent collection from other devices. Results
        are collected into a BatchCollectResult map keyed by device UUID.

        Args:
            devices: List of target Device objects. Must be non-empty and
                contain no duplicate device IDs.
            paths: YANG paths to retrieve from each device. Must be non-empty.

        Returns:
            BatchCollectResult with per-device CollectResult entries and
            aggregate summary counts.

        Raises:
            ValueError: devices is empty, paths is empty, or devices contains
                duplicate device IDs (programming error)
        """
```

## Concrete Implementation: GnmiCollector

```python
class GnmiCollector(Collector):
    """Nokia SR Linux Collector implementation using gNMI (pygnmi).

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
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class CollectResult:
    device_id: UUID
    device_name: str
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    paths: list[str] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


@dataclass(frozen=True)
class BatchCollectResult:
    results: dict[UUID, CollectResult] = field(default_factory=dict)
    total: int = 0
    succeeded: int = 0
    failed: int = 0
```

## Exceptions

```python
class CollectorError(Exception):
    """Base class for programming errors in the Collector module."""

class CollectorConfigError(CollectorError):
    """Invalid Collector configuration (missing credentials, bad timeout, etc.)."""
```

Note: `CollectorError` and subclasses are raised only for programming errors (invalid constructor arguments, bad call arguments). Device-side errors (connectivity, auth, timeout, parse) are returned as `CollectResult(success=False, error=...)`.

## Consumer Notes

- **Observability**: Call `collect(device, paths)` or `collect_batch(devices, paths)` to retrieve current device state. Compare `result.data` directly against the Executor's rendered payloads or the Intent module's `DesiredState` for drift detection.
- **Orchestrator**: Call `get_running_config(device)` or `collect_batch(devices, paths)` within Temporal activities. Check `result.success` to decide whether to signal the workflow. A `BatchCollectResult` with `failed > 0` may trigger a compensation or alert workflow.
- **Presentation**: Use `get_running_config()` to show operators the current device state before or after an apply. Use `collect()` with specific paths for targeted status checks.

## Relationship to Executor Contract

The `Collector` ABC mirrors the `Executor` ABC in several ways:
- Same result-over-exception pattern (device-side failures return results, not exceptions).
- Same batch pattern: `collect_batch()` mirrors `apply_batch()`, returning a `Batch*Result` with per-device map and aggregate counts.
- Same connection parameters: `GnmiCollector` constructor is identical to `GnmiExecutor` constructor.
- Same `asyncio.to_thread()` bridge for pygnmi's synchronous calls.

The Collector is read-only; it never issues gNMI SET operations.
