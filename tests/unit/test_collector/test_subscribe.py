"""Unit tests for the gNMI subscription bridge (spec 016 T005, research R2).

pygnmi's stream subscriber reads the gRPC stream in its own thread. That thread
puts each message on a queue, and when the stream fails it records the error on
``subscriber.error`` instead of raising it to the reader. The fake below has
the same surface: ``peek()``, ``get_update(timeout)``, ``error`` and
``close()``. No test dials a device.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from typing import Any
from unittest.mock import patch

import grpc
import pytest

pytestmark = pytest.mark.unit

_CONN: dict[str, Any] = {
    "host": "172.20.21.13",
    "port": 57400,
    "username": "admin",
    "password": "test",  # pragma: allowlist secret
    "insecure": True,
    "timeout": 5,
}


class FakeRpcError(grpc.RpcError):
    def __init__(self, code: grpc.StatusCode, details: str = "") -> None:
        super().__init__(details)
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details


class FakeSubscriber:
    """pygnmi's StreamSubscriber: messages, then optionally an error, else it blocks."""

    def __init__(self, messages: list[dict] = (), *, error: BaseException | None = None, sync_timeout: bool = False):
        self._queue: queue.Queue[dict] = queue.Queue()
        for message in messages:
            self._queue.put(message)
        self._final_error = error
        self._sync_timeout = sync_timeout
        self.error: BaseException | None = None
        self.closed = threading.Event()

    def peek(self) -> bool:
        if self._sync_timeout:
            return True
        if self._queue.empty() and self._final_error is not None:
            self.error = self._final_error
        return not self._queue.empty()

    def get_update(self, timeout: float) -> dict:
        if self._sync_timeout:
            raise TimeoutError(f"No update from target after {timeout}s")
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"No update from target after {timeout}s") from None

    def close(self) -> None:
        # Closing the channel ends pygnmi's reader thread with CANCELLED.
        self.error = FakeRpcError(grpc.StatusCode.CANCELLED, "Channel closed!")
        self.closed.set()


class FakeClient:
    def __init__(self, subscriber: FakeSubscriber, *, enter_error: BaseException | None = None, **kwargs: Any):
        self.kwargs = kwargs
        self.subscriber = subscriber
        self.requests: list[dict] = []
        self.exited = False
        self._enter_error = enter_error

    def __enter__(self) -> FakeClient:
        if self._enter_error is not None:
            raise self._enter_error
        return self

    def __exit__(self, *exc: object) -> None:
        self.exited = True

    def subscribe2(self, subscribe: dict) -> FakeSubscriber:
        self.requests.append(subscribe)
        return self.subscriber


def _patched(subscriber: FakeSubscriber, *, enter_error: BaseException | None = None):
    clients: list[FakeClient] = []

    def _factory(**kwargs: Any) -> FakeClient:
        client = FakeClient(subscriber, enter_error=enter_error, **kwargs)
        clients.append(client)
        return client

    return patch("snapl_collector.gnmi.subscribe.gNMIclient", side_effect=_factory), clients


def _bridge_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith("snapl-gnmi-subscribe")]


def _stream(paths=("/interface[name=lo0]/oper-state",), interval_s=10, stall_s=None):
    from snapl_collector.gnmi.subscribe import subscribe_sample

    kwargs: dict[str, Any] = {}
    if stall_s is not None:
        kwargs["stall_s"] = stall_s
    return subscribe_sample(**_CONN, paths=list(paths), interval_s=interval_s, **kwargs)


async def _drain(stream, into: list) -> None:
    async for message in stream:
        into.append(message)


def _msg(n: int) -> dict:
    return {"update": {"update": [{"path": "p", "val": {"n": n}}], "timestamp": n}}


class TestSubscribeSample:
    async def test_messages_arrive_in_order(self):
        subscriber = FakeSubscriber([_msg(1), _msg(2), _msg(3)])
        patcher, _ = _patched(subscriber)
        with patcher:
            stream = _stream()
            got = [await anext(stream) for _ in range(3)]
            await stream.aclose()
        assert [m["update"]["timestamp"] for m in got] == [1, 2, 3]

    async def test_dials_with_the_connection_settings(self):
        patcher, clients = _patched(FakeSubscriber([_msg(1)]))
        with patcher:
            stream = _stream()
            await anext(stream)
            await stream.aclose()
        assert clients[0].kwargs == {
            "target": ("172.20.21.13", 57400),
            "username": "admin",
            "password": "test",  # pragma: allowlist secret
            "insecure": True,
            "timeout": 5,
        }

    async def test_request_samples_each_path_in_nanoseconds(self):
        paths = ["/interface[name=lo0]/oper-state", "/platform/control[slot=A]/memory/utilization"]
        patcher, clients = _patched(FakeSubscriber([_msg(1)]))
        with patcher:
            stream = _stream(paths=paths, interval_s=10)
            await anext(stream)
            await stream.aclose()
        assert clients[0].requests == [
            {
                "subscription": [
                    {"path": paths[0], "mode": "sample", "sample_interval": 10_000_000_000},
                    {"path": paths[1], "mode": "sample", "sample_interval": 10_000_000_000},
                ],
                "mode": "stream",
                "encoding": "json_ietf",
            }
        ]

    async def test_closing_ends_cleanly_and_the_thread_is_gone(self):
        subscriber = FakeSubscriber([_msg(1)])
        patcher, clients = _patched(subscriber)
        with patcher:
            stream = _stream()
            await anext(stream)
            assert _bridge_threads()
            await stream.aclose()  # blocks on the device; must not raise
        assert _bridge_threads() == []
        assert subscriber.closed.is_set()
        assert clients[0].exited

    async def test_cancelled_after_a_requested_stop_is_not_raised(self):
        # The fake's close() records CANCELLED, as closing pygnmi's channel does.
        subscriber = FakeSubscriber([_msg(1)])
        patcher, _ = _patched(subscriber)
        with patcher:
            stream = _stream()
            await anext(stream)
            await stream.aclose()
        assert isinstance(subscriber.error, FakeRpcError)

    async def test_an_error_before_a_stop_is_raised_after_the_messages(self):
        from snapl_collector import TelemetryStreamError

        error = FakeRpcError(grpc.StatusCode.UNAVAILABLE, "Socket closed")
        patcher, _ = _patched(FakeSubscriber([_msg(1), _msg(2)], error=error))
        got: list[dict] = []
        with patcher, pytest.raises(TelemetryStreamError, match="UNAVAILABLE: Socket closed") as info:
            await _drain(_stream(), got)
        assert len(got) == 2
        assert "172.20.21.13:57400" in str(info.value)
        assert info.value.__cause__ is error
        assert _bridge_threads() == []

    async def test_a_dial_failure_is_raised(self):
        from snapl_collector import TelemetryStreamError

        patcher, _ = _patched(FakeSubscriber(), enter_error=ConnectionRefusedError())
        with patcher, pytest.raises(TelemetryStreamError, match="ConnectionRefusedError"):
            await anext(_stream())
        assert _bridge_threads() == []

    async def test_a_first_message_that_never_completes_is_raised(self):
        from snapl_collector import TelemetryStreamError

        patcher, _ = _patched(FakeSubscriber(sync_timeout=True))
        with patcher, pytest.raises(TelemetryStreamError, match="No update from target"):
            await anext(_stream())
        assert _bridge_threads() == []

    async def test_a_silent_stream_is_raised_after_the_stall_time(self):
        """A device that stops answering without closing (a paused container)."""
        from snapl_collector import TelemetryStreamError

        patcher, _ = _patched(FakeSubscriber([_msg(1)]))
        with patcher:
            stream = _stream(stall_s=0.3)
            await anext(stream)
            with pytest.raises(TelemetryStreamError, match=r"no message for 0\.3 s"):
                await asyncio.wait_for(anext(stream), timeout=5)
        assert _bridge_threads() == []

    async def test_the_stall_time_defaults_to_the_dial_timeout_plus_three_intervals(self):
        from snapl_collector.gnmi.subscribe import default_stall_s

        assert default_stall_s(timeout=30, interval_s=10) == 60
