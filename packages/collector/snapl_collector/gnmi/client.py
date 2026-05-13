"""gNMI GET client wrapper for the NAF Collector."""

from __future__ import annotations

import asyncio
from typing import Any

from pygnmi.client import gNMIclient


async def gnmi_get(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    insecure: bool,
    timeout: int,
    paths: list[str],
) -> dict[str, Any]:
    """Issue a gNMI GET for the given paths and return the raw response dict.

    Wraps the synchronous pygnmi call with asyncio.to_thread so callers can
    await it. All exceptions propagate to the caller — GnmiCollector handles
    error classification.
    """

    def _blocking() -> dict[str, Any]:
        target = (host, port)
        with gNMIclient(
            target=target,
            username=username,
            password=password,
            insecure=insecure,
            timeout=timeout,
        ) as gc:
            return gc.get(path=paths, datatype="all", encoding="json_ietf")

    return await asyncio.to_thread(_blocking)
