"""GnmiExecutor — concrete Executor implementation for Nokia SR Linux (T016, T021, T024, T028)."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from pygnmi.client import gNMIclient

from snapl_executor.abc import Executor
from snapl_executor.exceptions import ExecutorConfigError
from snapl_executor.gnmi.renderer import RENDER_ERROR_KEY, ConfigRenderer
from snapl_executor.models import ApplyResult, BatchResult, DryRunResult

if TYPE_CHECKING:
    from uuid import UUID

    from snapl_intent.models import DesiredState


class GnmiExecutor(Executor):
    """Nokia SR Linux Executor using gNMI (pygnmi) + Jinja2 templates."""

    def __init__(
        self,
        *,
        host: str,
        port: int = 57400,
        username: str = "admin",
        password: str,
        insecure: bool = True,
        timeout: int = 30,
    ) -> None:
        if not host:
            raise ExecutorConfigError("host is required")
        if not password:
            raise ExecutorConfigError("password is required")
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._insecure = insecure
        self._timeout = timeout

    # ------------------------------------------------------------------ US1

    async def apply(self, desired: DesiredState) -> ApplyResult:
        if desired is None:
            raise ValueError("desired is required")
        renderer = ConfigRenderer(use_case=desired.device.use_case)
        payload = renderer.render(desired)
        return await self._gnmi_set(desired, payload, is_rollback=False)

    # ------------------------------------------------------------------ US2

    async def dry_run(self, desired: DesiredState) -> DryRunResult:
        if desired is None:
            raise ValueError("desired is required")
        renderer = ConfigRenderer(use_case=desired.device.use_case)
        result = renderer.render_safe(desired)
        if RENDER_ERROR_KEY in result:
            return DryRunResult(
                device_id=desired.device.id,
                device_name=desired.device.name,
                success=False,
                render_error=result[RENDER_ERROR_KEY],
            )
        return DryRunResult(
            device_id=desired.device.id,
            device_name=desired.device.name,
            success=True,
            payload=result,
        )

    # ------------------------------------------------------------------ US3

    async def rollback(self, desired: DesiredState) -> ApplyResult:
        if desired is None:
            raise ValueError("desired is required")
        renderer = ConfigRenderer(use_case=desired.device.use_case)
        payload = renderer.render(desired)
        return await self._gnmi_set(desired, payload, is_rollback=True)

    # ------------------------------------------------------------------ US4

    async def apply_batch(self, states: list[DesiredState]) -> BatchResult:
        if not states:
            raise ValueError("states list is empty")
        ids: list[UUID] = [ds.device.id for ds in states]
        if len(ids) != len(set(ids)):
            raise ValueError("states contains duplicate device IDs")

        tasks = [self._apply_one(ds) for ds in states]
        results_list: list[ApplyResult] = await asyncio.gather(*tasks)

        results: dict[UUID, ApplyResult] = {r.device_id: r for r in results_list}
        succeeded = sum(1 for r in results_list if r.success)
        failed = len(results_list) - succeeded
        return BatchResult(
            results=results,
            total=len(results_list),
            succeeded=succeeded,
            failed=failed,
        )

    # ------------------------------------------------------------------ internals

    async def _apply_one(self, desired: DesiredState) -> ApplyResult:
        try:
            renderer = ConfigRenderer(use_case=desired.device.use_case)
            payload = renderer.render(desired)
            return await self._gnmi_set(desired, payload, is_rollback=False)
        except Exception as exc:
            return ApplyResult(
                device_id=desired.device.id,
                device_name=desired.device.name,
                success=False,
                payload={},
                error=str(exc),
            )

    async def _gnmi_set(self, desired: DesiredState, payload: dict[str, Any], *, is_rollback: bool) -> ApplyResult:
        start = time.monotonic()
        try:
            response = await asyncio.to_thread(self._blocking_set, payload)
            duration_ms = int((time.monotonic() - start) * 1000)
            return ApplyResult(
                device_id=desired.device.id,
                device_name=desired.device.name,
                success=True,
                payload=payload,
                device_response=str(response),
                is_rollback=is_rollback,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            return ApplyResult(
                device_id=desired.device.id,
                device_name=desired.device.name,
                success=False,
                payload=payload,
                error=str(exc),
                is_rollback=is_rollback,
                duration_ms=duration_ms,
            )

    def _blocking_set(self, payload: dict[str, Any]) -> Any:
        with gNMIclient(
            target=(self._host, self._port),
            username=self._username,
            password=self._password,
            insecure=self._insecure,
            timeout=self._timeout,
        ) as gc:
            return gc.set(update=[("/", payload)])
