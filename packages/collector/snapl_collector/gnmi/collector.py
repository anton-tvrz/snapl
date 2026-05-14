"""GnmiCollector — Nokia SR Linux implementation of the Collector ABC."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import grpc

from snapl_collector.abc import Collector
from snapl_collector.gnmi.client import gnmi_get
from snapl_collector.models import BatchCollectResult, CollectResult

if TYPE_CHECKING:
    from snapl_intent.models import Device


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
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._insecure = insecure
        self._timeout = timeout

    # ── Internal helpers ─────────────────────────────────────────────────

    def _conn_kwargs(self) -> dict[str, Any]:
        return {
            "host": self._host,
            "port": self._port,
            "username": self._username,
            "password": self._password,
            "insecure": self._insecure,
            "timeout": self._timeout,
        }

    def _parse_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Extract path→value mapping from a pygnmi GET response.

        Raises KeyError/TypeError if the response structure is unexpected.
        """
        data: dict[str, Any] = {}
        for notification in response["notification"]:
            for update in notification.get("update", []):
                path = update["path"]
                data[path] = update["val"]
        return data

    def _classify_error(self, exc: Exception, timeout: int) -> str:
        if isinstance(exc, grpc.RpcError):
            code = exc.code()
            detail = exc.details() or ""
            if code == grpc.StatusCode.UNAUTHENTICATED:
                return f"auth error: {detail}"
            if code == grpc.StatusCode.DEADLINE_EXCEEDED:
                return f"timeout after {timeout}s"
            return f"connectivity error: {detail}"
        if isinstance(exc, OSError):
            return f"connectivity error: {exc}"
        if isinstance(exc, KeyError | TypeError | ValueError):
            return f"parse error: {exc}"
        return f"connectivity error: {exc}"

    # ── Core operations ───────────────────────────────────────────────────

    async def collect(self, device: Device, paths: list[str]) -> CollectResult:
        if not paths:
            raise ValueError("paths must not be empty")

        start = time.monotonic()
        try:
            response = await gnmi_get(**self._conn_kwargs(), paths=paths)
            data = self._parse_response(response)
            duration_ms = int((time.monotonic() - start) * 1000)
            return CollectResult(
                device_id=device.id,
                device_name=device.name,
                success=True,
                data=data,
                paths=paths,
                duration_ms=duration_ms,
            )
        except (KeyError, TypeError) as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            return CollectResult(
                device_id=device.id,
                device_name=device.name,
                success=False,
                paths=paths,
                error=f"parse error: {exc}",
                duration_ms=duration_ms,
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            return CollectResult(
                device_id=device.id,
                device_name=device.name,
                success=False,
                paths=paths,
                error=self._classify_error(exc, self._timeout),
                duration_ms=duration_ms,
            )

    async def get_running_config(self, device: Device) -> CollectResult:
        return await self.collect(device, paths=["/"])

    async def collect_batch(
        self,
        devices: list[Device],
        paths: list[str],
    ) -> BatchCollectResult:
        if not devices:
            raise ValueError("devices must not be empty")
        if not paths:
            raise ValueError("paths must not be empty")
        ids = [d.id for d in devices]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate device IDs in devices list")

        async def _collect_one(device: Device) -> CollectResult:
            collector = GnmiCollector(
                host=device.management_address,
                port=self._port,
                username=self._username,
                password=self._password,
                insecure=self._insecure,
                timeout=self._timeout,
            )
            try:
                return await collector.collect(device, paths)
            except Exception as exc:
                return CollectResult(
                    device_id=device.id,
                    device_name=device.name,
                    success=False,
                    paths=paths,
                    error=self._classify_error(exc, self._timeout),
                )

        results_list = await asyncio.gather(*[_collect_one(d) for d in devices])
        results = {r.device_id: r for r in results_list}
        succeeded = sum(1 for r in results_list if r.success)
        failed = len(results_list) - succeeded
        return BatchCollectResult(
            results=results,
            total=len(devices),
            succeeded=succeeded,
            failed=failed,
        )
