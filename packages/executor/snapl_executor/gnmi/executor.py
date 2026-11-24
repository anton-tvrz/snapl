"""GnmiExecutor — concrete Executor implementation for Nokia SR Linux (T016, T021, T024, T028)."""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING, Any

from pygnmi.client import gNMIclient

from snapl_executor.abc import Executor
from snapl_executor.exceptions import ExecutorConfigError
from snapl_executor.gnmi.renderer import ConfigRenderer
from snapl_executor.models import ApplyResult, BatchResult, DryRunResult

if TYPE_CHECKING:
    from collections.abc import Collection
    from uuid import UUID

    from snapl_intent.models import DesiredState, Device


def _render(desired: DesiredState) -> tuple[dict[str, Any] | None, str | None]:
    """Build the renderer for the device's use case and render ``desired``.

    Returns ``(payload, None)``, or ``(None, error)`` when the renderer can't
    be built (no templates for the use case) or rendering fails (a template
    error, or rendered text that is not JSON). Every entrypoint renders
    through here, so all of them report render failures the same way (#56).
    """
    use_case = desired.device.use_case
    try:
        return ConfigRenderer(use_case=use_case).render(desired), None
    except Exception as exc:
        return None, f"render failed for use case {use_case!r}: {type(exc).__name__}: {exc}"


# The only paths a removal may delete (spec 013 FR-005, research R2): one
# whole entity of a removable kind, or one address under an interface. Each
# pattern captures the interface the path belongs to, if any, for the
# protected check. A key may hold '/' (ethernet-1/7, 192.0.2.1/31) but never a
# bracket, so no key can swallow a deeper path.
_KEY = r"[^\[\]]+"
_DELETE_SHAPES: tuple[re.Pattern[str], ...] = (
    re.compile(rf"/interface\[name=({_KEY})\]"),
    re.compile(rf"/interface\[name=({_KEY})\]/subinterface\[index=[0-9]+\]/ipv4/address\[ip-prefix={_KEY}\]"),
    # A neighbour belongs to no interface: its empty group never matches a protected name.
    re.compile(rf"/network-instance\[name=default\]/protocols/bgp/neighbor\[peer-address={_KEY}\]()"),
    re.compile(rf"/network-instance\[name=default\]/interface\[name=({_KEY})\.[0-9]+\]"),
)

REFUSED_NO_PROTECTED_SET = "no protected set configured"
REFUSED_PROTECTED = "protected"
REFUSED_SHAPE = "not one entity this executor deletes"


def check_delete_paths(paths: list[str], protected: Collection[str] | None) -> dict[str, str]:
    """The paths a removal must not send, each with the reason; empty when all may go.

    A path passes when it fully matches one of the entity shapes above and the
    interface it belongs to is not protected. Without a protected set every
    path is refused, so an executor built without one deletes nothing.
    """
    refused: dict[str, str] = {}
    for path in paths:
        if protected is None:
            refused[path] = REFUSED_NO_PROTECTED_SET
            continue
        match = next((m for shape in _DELETE_SHAPES if (m := shape.fullmatch(path))), None)
        if match is None:
            refused[path] = REFUSED_SHAPE
        elif match.group(1) in protected:
            refused[path] = REFUSED_PROTECTED
    return refused


# How many devices one batch dials at once (#55). Each apply holds a gRPC
# connection and a thread, so an uncapped batch over a large fabric would open
# one of each per device.
DEFAULT_MAX_CONCURRENCY = 16


class GnmiExecutor(Executor):
    """Nokia SR Linux Executor using gNMI (pygnmi) + Jinja2 templates.

    One instance serves any number of devices: the dial target is resolved
    per call from the Device — ``lab_node_name`` first, then
    ``management_address`` — with the constructor ``host`` as a last-resort
    fallback for single-target use (tests, ad-hoc scripts).

    Error contract (see ``Executor``): ``apply``, ``rollback``, ``dry_run``
    and ``apply_batch`` return their result with ``success=False`` when the
    renderer can't be built or rendering fails, and ``apply``/``rollback``
    do the same when the device can't be reached or rejects the payload.
    They raise only for programming errors (``desired`` is None, an empty
    or duplicate batch); the constructor raises ``ExecutorConfigError``.
    """

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int = 57400,
        username: str = "admin",
        password: str,
        insecure: bool = True,
        timeout: int = 30,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    ) -> None:
        if not password:
            raise ExecutorConfigError("password is required")
        if max_concurrency < 1:
            raise ExecutorConfigError(f"max_concurrency must be at least 1, got {max_concurrency}")
        self._max_concurrency = max_concurrency
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._insecure = insecure
        self._timeout = timeout
        # SR Linux rejects concurrent exclusive config sessions, so gNMI
        # sets are serialized per dial host.
        self._set_locks: dict[str, asyncio.Lock] = {}

    def _dial_host(self, device: Device) -> str:
        host = device.lab_node_name or device.management_address or self._host
        if not host:
            raise ExecutorConfigError(
                f"no gNMI dial target for device {device.name!r}: "
                "lab_node_name and management_address are empty and no fallback host is configured"
            )
        return host

    # ------------------------------------------------------------------ US1

    async def apply(self, desired: DesiredState) -> ApplyResult:
        if desired is None:
            raise ValueError("desired is required")
        return await self._deploy(desired, is_rollback=False)

    # ------------------------------------------------------------------ US2

    async def dry_run(self, desired: DesiredState) -> DryRunResult:
        if desired is None:
            raise ValueError("desired is required")
        payload, render_error = _render(desired)
        if payload is None:
            return DryRunResult(
                device_id=desired.device.id,
                device_name=desired.device.name,
                success=False,
                render_error=render_error,
            )
        return DryRunResult(
            device_id=desired.device.id,
            device_name=desired.device.name,
            success=True,
            payload=payload,
        )

    # ------------------------------------------------------------------ US3

    async def rollback(self, desired: DesiredState) -> ApplyResult:
        if desired is None:
            raise ValueError("desired is required")
        return await self._deploy(desired, is_rollback=True)

    # ------------------------------------------------------------------ US4

    async def apply_batch(self, states: list[DesiredState]) -> BatchResult:
        if not states:
            raise ValueError("states list is empty")
        ids: list[UUID] = [ds.device.id for ds in states]
        if len(ids) != len(set(ids)):
            raise ValueError("states contains duplicate device IDs")

        # Created per batch: a semaphore belongs to the event loop it is first used on.
        slots = asyncio.Semaphore(self._max_concurrency)

        async def _deploy_one(ds: DesiredState) -> ApplyResult:
            async with slots:
                return await self._deploy(ds, is_rollback=False)

        results_list: list[ApplyResult] = await asyncio.gather(*[_deploy_one(ds) for ds in states])

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

    async def _deploy(self, desired: DesiredState, *, is_rollback: bool) -> ApplyResult:
        """Render and SET ``desired``; a render failure is a failed result and nothing is dialled."""
        payload, render_error = _render(desired)
        if payload is None:
            return ApplyResult(
                device_id=desired.device.id,
                device_name=desired.device.name,
                success=False,
                payload={},
                error=render_error,
                is_rollback=is_rollback,
            )
        return await self._gnmi_set(desired, payload, is_rollback=is_rollback)

    async def _gnmi_set(self, desired: DesiredState, payload: dict[str, Any], *, is_rollback: bool) -> ApplyResult:
        start = time.monotonic()
        host: str | None = None
        try:
            host = self._dial_host(desired.device)
            async with self._set_locks.setdefault(host, asyncio.Lock()):
                response = await asyncio.to_thread(self._blocking_set, host, payload)
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
                error=self._error_reason(exc, host),
                is_rollback=is_rollback,
                duration_ms=duration_ms,
            )

    def _error_reason(self, exc: Exception, host: str | None) -> str:
        """The exception's message, or its class and the dial target when it has none.

        A dial that never connects raises an exception whose ``str()`` is empty,
        which left the apply error blank (#194).
        """
        reason = str(exc)
        if reason:
            return reason
        where = f", dialing {host}:{self._port}" if host else ""
        return f"{type(exc).__name__}{where}"

    def _blocking_set(self, host: str, payload: dict[str, Any]) -> Any:
        with gNMIclient(
            target=(host, self._port),
            username=self._username,
            password=self._password,
            insecure=self._insecure,
            timeout=self._timeout,
        ) as gc:
            return gc.set(update=[("/", payload)])
