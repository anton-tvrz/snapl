"""gNMI client wrapper for asyncio compatibility (T014).

pygnmi is synchronous. All blocking calls are dispatched via
asyncio.to_thread so the Executor ABC surface stays async.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pygnmi.client import gNMIclient


async def gnmi_set(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    insecure: bool,
    timeout: int,
    payload: dict,
) -> dict[str, Any]:
    """Issue a gNMI SET (update) at path '/' with the merged payload.

    Runs the blocking pygnmi call on a thread-pool thread via asyncio.to_thread.
    Returns the raw gNMI SET response dict.
    Raises whatever pygnmi/gRPC raises — callers map to error strings.
    """

    def _blocking_set() -> dict[str, Any]:
        with gNMIclient(
            target=(host, port),
            username=username,
            password=password,
            insecure=insecure,
            timeout=timeout,
        ) as gc:
            return gc.set(update=[("/", payload)])

    return await asyncio.to_thread(_blocking_set)
