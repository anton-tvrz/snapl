"""Sample-mode gNMI subscription, bridged from pygnmi's thread to asyncio (spec 016, research R2).

pygnmi's stream subscriber reads the gRPC stream in a thread of its own and
queues each message. When the stream fails, that thread records the error on
``subscriber.error`` and stops, but a reader blocked on ``next()`` is never
woken. So the bridge never blocks on the subscriber: it polls ``peek()`` and
``error`` from one thread per stream, posts each message to the event loop with
``loop.call_soon_threadsafe``, and raises ``TelemetryStreamError`` when the
stream fails or goes silent for longer than the stall time.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from typing import TYPE_CHECKING, Any

import grpc
from pygnmi.client import gNMIclient

from snapl_collector.exceptions import TelemetryStreamError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# How often the bridge thread looks for a message, an error or a stop.
_POLL_S = 0.1

# The longest gap allowed between the messages that make up the first, complete
# dump. pygnmi coalesces them until ``sync_response``; on the lab they arrive as one.
_SYNC_GAP_S = 10.0

_NS_PER_S = 1_000_000_000


def default_stall_s(*, timeout: int, interval_s: int) -> float:
    """How long a stream may stay silent before it counts as broken.

    The device sends at least one message every interval. The dial timeout
    covers the first message.
    """
    return float(timeout + 3 * interval_s)


def _subscribe_request(paths: list[str], interval_s: int) -> dict[str, Any]:
    return {
        "subscription": [{"path": path, "mode": "sample", "sample_interval": interval_s * _NS_PER_S} for path in paths],
        "mode": "stream",
        "encoding": "json_ietf",
    }


def _describe(exc: BaseException) -> str:
    if isinstance(exc, grpc.RpcError) and callable(getattr(exc, "code", None)):
        code = exc.code()
        name = getattr(code, "name", str(code))
        details = exc.details() if callable(getattr(exc, "details", None)) else ""
        return f"{name}: {details}" if details else name
    return str(exc) or type(exc).__name__


async def subscribe_sample(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    insecure: bool,
    timeout: int,
    paths: list[str],
    interval_s: int,
    stall_s: float | None = None,
) -> AsyncGenerator[dict[str, Any]]:
    """Subscribe to ``paths`` in sample mode and yield each message pygnmi parses.

    The first message is the complete dump, with ``sync_response``. Closing the
    iterator stops the subscription and waits for its thread to end; it never
    raises.

    Raises:
        TelemetryStreamError: the dial failed, the stream ended with an error,
            the first dump did not complete, or no message arrived for
            ``stall_s`` seconds (default: :func:`default_stall_s`).
    """
    loop = asyncio.get_running_loop()
    inbox: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
    stop = threading.Event()
    request = _subscribe_request(paths, interval_s)
    where = f"{host}:{port}"
    silent_s = stall_s if stall_s is not None else default_stall_s(timeout=timeout, interval_s=interval_s)

    def _post(kind: str, value: Any) -> None:
        with contextlib.suppress(RuntimeError):  # the loop has closed: nobody is reading
            loop.call_soon_threadsafe(inbox.put_nowait, (kind, value))

    def _run() -> None:
        try:
            with gNMIclient(
                target=(host, port), username=username, password=password, insecure=insecure, timeout=timeout
            ) as client:
                subscriber = client.subscribe2(subscribe=request)
                try:
                    while not stop.is_set():
                        if subscriber.peek():
                            _post("message", subscriber.get_update(timeout=_SYNC_GAP_S))
                        elif subscriber.error is not None:
                            raise subscriber.error
                        else:
                            stop.wait(_POLL_S)
                finally:
                    subscriber.close()
        except Exception as exc:
            if not stop.is_set():
                _post("error", exc)

    thread = threading.Thread(target=_run, name=f"snapl-gnmi-subscribe-{where}", daemon=True)
    thread.start()
    try:
        while True:
            try:
                kind, value = await asyncio.wait_for(inbox.get(), timeout=silent_s)
            except TimeoutError:
                raise TelemetryStreamError(f"telemetry from {where}: no message for {silent_s:g} s") from None
            if kind == "error":
                raise TelemetryStreamError(f"telemetry from {where}: {_describe(value)}") from value
            yield value
    finally:
        stop.set()
        await asyncio.to_thread(thread.join)
