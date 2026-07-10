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
    """Issue a gNMI GET for the given paths and return a merged response dict.

    Each path is fetched individually (within one connection) and every
    empty-path update is stamped with its requested path before the responses
    are merged. This keeps each returned subtree unambiguously tied to the path
    it was requested for: gNMI guarantees neither the order nor the count of
    notifications in a multi-path GetResponse, so de-multiplexing a single
    multi-path response by position would silently mis-key the data (#53).

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
            merged: list[dict[str, Any]] = []
            for path in paths:
                response = gc.get(path=[path], datatype="all", encoding="json_ietf")
                for notification in response.get("notification", []):
                    stamped = [
                        {**update, "path": update.get("path") or path} for update in notification.get("update", [])
                    ]
                    merged.append({**notification, "update": stamped})
            return {"notification": merged}

    return await asyncio.to_thread(_blocking)
