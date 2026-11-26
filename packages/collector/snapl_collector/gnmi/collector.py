"""GnmiCollector — Nokia SR Linux implementation of the Collector ABC."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import grpc

from snapl_collector.abc import Collector
from snapl_collector.exceptions import CollectorConfigError, TelemetryStreamError
from snapl_collector.gnmi.client import gnmi_get
from snapl_collector.gnmi.subscribe import subscribe_sample
from snapl_collector.models import (
    BatchCollectResult,
    BgpSessionSample,
    CollectResult,
    InterfaceSample,
    PlatformSample,
    TelemetrySample,
    TelemetryTarget,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from snapl_intent.models import Device

_logger = logging.getLogger(__name__)


# How many devices one batch dials at once (#55). Each collect holds a gRPC
# connection, so an uncapped batch over a large fabric would open one per device.
DEFAULT_MAX_CONCURRENCY = 16


class GnmiCollector(Collector):
    """Nokia SR Linux Collector implementation using gNMI (pygnmi).

    One instance serves any number of devices: the dial target is resolved
    per call from the Device — ``lab_node_name`` first, then
    ``management_address`` — with the constructor ``host`` as a last-resort
    fallback for single-target use (tests, ad-hoc scripts).

    Args:
        host: Fallback hostname or IP when the device carries no addressing.
        port: gNMI port (SR Linux default: 57400).
        username: gNMI username.
        password: gNMI password.
        insecure: Skip TLS verification (default True for lab environments).
        timeout: gNMI operation timeout in seconds (default 30).
        max_concurrency: How many devices ``collect_batch`` dials at once (default 16).

    Raises:
        CollectorConfigError: If ``password`` is empty or missing.
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
            raise CollectorConfigError("password is required")
        if max_concurrency < 1:
            raise CollectorConfigError(f"max_concurrency must be at least 1, got {max_concurrency}")
        self._max_concurrency = max_concurrency
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._insecure = insecure
        self._timeout = timeout

    # ── Internal helpers ─────────────────────────────────────────────────

    def _dial_host(self, device: Device) -> str | None:
        return device.lab_node_name or device.management_address or self._host

    def _conn_kwargs(self, host: str) -> dict[str, Any]:
        return {
            "host": host,
            "port": self._port,
            "username": self._username,
            "password": self._password,
            "insecure": self._insecure,
            "timeout": self._timeout,
        }

    def _parse_response(self, response: dict[str, Any], paths: list[str]) -> dict[str, Any]:
        """Extract path→value mapping from a pygnmi GET response.

        ``gnmi_get`` stamps every empty-path update with its requested path, so
        each update here carries a usable path. A root ``/`` GET is the one case
        that still reports an empty/root path; key it by the single requested
        path.

        gNMI lets a target split one path across several notifications or
        updates, so updates that land on the same key are merged by
        :meth:`_merge_fragment` instead of overwriting each other.

        Raises KeyError/TypeError if the response structure is unexpected, and
        ValueError if two fragments for one key can't be reconciled.
        """
        data: dict[str, Any] = {}
        for notification in response["notification"]:
            for update in notification.get("update", []):
                path = update["path"]
                if path and path != "/":
                    key = self._normalize_path(path)
                elif len(paths) == 1:
                    key = self._normalize_path(paths[0])
                else:
                    key = "/"
                if key in data:
                    data[key] = self._merge_fragment(data[key], update["val"], key)
                else:
                    data[key] = update["val"]
        return data

    @classmethod
    def _merge_fragment(cls, existing: Any, incoming: Any, where: str) -> Any:
        """Merge a repeated update for one path into what was already collected.

        The rule, applied recursively:

        - dict + dict: deep-merged key by key.
        - list + list: concatenated in arrival order; an entry equal to one
          already collected is not added again, so a re-sent fragment is
          harmless. Entries are never merged with each other (the YANG list
          keys aren't known here), so one list entry split across fragments
          stays two entries.
        - equal values (the same leaf reported twice): kept once.
        - anything else, such as two different scalars for one leaf or a dict
          against a list, can't be reconciled and raises ValueError naming
          the conflicting leaf, so ``collect`` reports a parse error instead
          of silently keeping one fragment.
        """
        if isinstance(existing, dict) and isinstance(incoming, dict):
            merged = dict(existing)
            for name, value in incoming.items():
                if name in merged:
                    merged[name] = cls._merge_fragment(merged[name], value, f"{where.rstrip('/')}/{name}")
                else:
                    merged[name] = value
            return merged
        if isinstance(existing, list) and isinstance(incoming, list):
            return existing + [entry for entry in incoming if entry not in existing]
        if existing == incoming:
            return existing
        raise ValueError(f"conflicting updates for {where}: {existing!r:.80} vs {incoming!r:.80}")

    @staticmethod
    def _normalize_path(path: str | None) -> str:
        """Normalize a device-reported path to the form callers request.

        SR Linux keys the root GET response with an empty path and prefixes
        targeted paths with YANG module names without a leading slash
        (``srl_nokia-interfaces:interface``); strip the prefixes (only outside
        ``[key=value]`` brackets) and anchor at ``/``.
        """
        if not path or path == "/":
            return "/"
        return "/" + "/".join(_path_elements(path))

    def _classify_error(self, exc: Exception, timeout: int, target: str | None = None) -> str:
        """Turn an exception into a reason an operator can act on.

        A dial that never connects raises an exception whose ``str()`` is empty,
        so the reason falls back to the exception's class name, and a
        connectivity error names the ``host:port`` that was dialed (#194).
        """
        where = f", dialing {target}" if target else ""
        if isinstance(exc, grpc.RpcError):
            code = exc.code()
            detail = exc.details() or ""
            if code == grpc.StatusCode.UNAUTHENTICATED:
                return f"auth error: {detail or type(exc).__name__}"
            if code == grpc.StatusCode.DEADLINE_EXCEEDED:
                return f"timeout after {timeout}s{where}"
            return f"connectivity error: {detail or type(exc).__name__}{where}"
        reason = str(exc) or type(exc).__name__
        if isinstance(exc, KeyError | TypeError | ValueError):
            return f"parse error: {reason}"
        return f"connectivity error: {reason}{where}"

    # ── Core operations ───────────────────────────────────────────────────

    async def collect(self, device: Device, paths: list[str]) -> CollectResult:
        if not paths:
            raise ValueError("paths must not be empty")

        host = self._dial_host(device)
        if not host:
            return CollectResult(
                device_id=device.id,
                device_name=device.name,
                success=False,
                paths=paths,
                error=(
                    f"no gNMI dial target for device {device.name!r}: "
                    "lab_node_name and management_address are empty and no fallback host is configured"
                ),
            )

        start = time.monotonic()
        try:
            response = await gnmi_get(**self._conn_kwargs(host), paths=paths)
            data = self._parse_response(response, paths)
            duration_ms = int((time.monotonic() - start) * 1000)
            return CollectResult(
                device_id=device.id,
                device_name=device.name,
                success=True,
                data=data,
                paths=paths,
                duration_ms=duration_ms,
            )
        except (KeyError, TypeError, ValueError) as exc:
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
                error=self._classify_error(exc, self._timeout, f"{host}:{self._port}"),
                duration_ms=duration_ms,
            )

    async def get_running_config(self, device: Device) -> CollectResult:
        return await self.collect(device, paths=["/"])

    async def stream_telemetry(self, target: TelemetryTarget, *, interval_s: int) -> AsyncIterator[TelemetrySample]:
        device = target.device
        host = self._dial_host(device)
        if not host:
            raise TelemetryStreamError(
                f"no gNMI dial target for device {device.name!r}: "
                "lab_node_name and management_address are empty and no fallback host is configured"
            )
        parser = _TelemetryParser(target)
        stream = subscribe_sample(**self._conn_kwargs(host), paths=_telemetry_paths(target), interval_s=interval_s)
        try:
            async for message in stream:
                yield parser.parse(message)
        finally:
            await stream.aclose()

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

        # Created per batch: a semaphore belongs to the event loop it is first used on.
        slots = asyncio.Semaphore(self._max_concurrency)

        async def _collect_one(device: Device) -> CollectResult:
            try:
                async with slots:
                    return await self.collect(device, paths)
            except Exception as exc:
                host = self._dial_host(device)
                return CollectResult(
                    device_id=device.id,
                    device_name=device.name,
                    success=False,
                    paths=paths,
                    error=self._classify_error(exc, self._timeout, f"{host}:{self._port}" if host else None),
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


# ── Telemetry (spec 016, research R1) ────────────────────────────────────

_MODULE_PREFIX = re.compile(r"^[\w.-]+:")
_LIST_KEY = re.compile(r"\[([^=\]]+)=([^\]]*)\]")
_BGP_NEIGHBOR = "/network-instance[name=default]/protocols/bgp/neighbor[peer-address={peer}]"
_RECEIVED_ROUTES = "afi-safi[afi-safi-name=ipv4-unicast]/received-routes"
_CPU_PATH = "/platform/control[slot=A]/cpu[index=all]/total/instant"
_MEMORY_PATH = "/platform/control[slot=A]/memory/utilization"

# InterfaceSample field <- leaf under the interface's statistics.
_INTERFACE_COUNTERS = {
    "in_octets": "statistics/in-octets",
    "out_octets": "statistics/out-octets",
    "in_errors": "statistics/in-error-packets",
    "out_errors": "statistics/out-error-packets",
    "in_discards": "statistics/in-discarded-packets",
    "out_discards": "statistics/out-discarded-packets",
}


def _path_elements(path: str) -> list[str]:
    """Split a gNMI path at the slashes outside ``[key=value]`` and drop each element's module prefix."""
    elements: list[str] = []
    depth = 0
    start = 0
    for i, char in enumerate(path):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == "/" and depth == 0:
            elements.append(path[start:i])
            start = i + 1
    elements.append(path[start:])
    return [_MODULE_PREFIX.sub("", el, count=1) for el in elements if el]


def _telemetry_paths(target: TelemetryTarget) -> list[str]:
    """One path per leaf intent asks for, so nothing else is sent (research R1)."""
    paths: list[str] = []
    for name in target.interfaces:
        paths += [f"/interface[name={name}]/oper-state", f"/interface[name={name}]/statistics"]
    for peer in target.peers:
        neighbor = _BGP_NEIGHBOR.format(peer=peer)
        paths += [f"{neighbor}/session-state", f"{neighbor}/{_RECEIVED_ROUTES}"]
    return [*paths, _CPU_PATH, _MEMORY_PATH]


def _flatten(value: Any, prefix: str, out: dict[str, Any]) -> None:
    """Leaves of ``value`` keyed by their path below the list entry, module prefixes dropped."""
    if isinstance(value, dict):
        for key, child in value.items():
            name = _MODULE_PREFIX.sub("", str(key), count=1)
            _flatten(child, f"{prefix}/{name}" if prefix else name, out)
    elif prefix:
        out[prefix] = value


def _sample_time(timestamp: Any) -> datetime:
    if isinstance(timestamp, int) and not isinstance(timestamp, bool) and timestamp > 0:
        seconds, nanos = divmod(timestamp, 1_000_000_000)
        return datetime.fromtimestamp(seconds, tz=UTC) + timedelta(microseconds=nanos // 1000)
    return datetime.now(tz=UTC)


class _TelemetryParser:
    """Turns one stream's messages into samples. Keyed on the list entry, so module prefixes don't matter."""

    def __init__(self, target: TelemetryTarget) -> None:
        self._device = target.device.name
        self._interfaces = set(target.interfaces)
        self._peers = set(target.peers)
        self._warned: set[tuple[str, str]] = set()

    def parse(self, message: dict[str, Any]) -> TelemetrySample:
        notification = message.get("update") or {}
        interfaces: dict[str, dict[str, Any]] = {}
        sessions: dict[str, dict[str, Any]] = {}
        platform: dict[str, Any] = {}
        has_platform = False
        for update in notification.get("update") or []:
            elements = _path_elements(str(update.get("path") or ""))
            for index, element in enumerate(elements):
                name, _, _ = element.partition("[")
                key = _LIST_KEY.search(element)
                rest = "/".join(elements[index + 1 :])
                if name == "interface" and key and key.group(1) == "name":
                    if key.group(2) in self._interfaces:
                        _flatten(update.get("val"), rest, interfaces.setdefault(key.group(2), {}))
                    break
                if name == "neighbor" and key and key.group(1) == "peer-address":
                    if key.group(2) in self._peers:
                        _flatten(update.get("val"), rest, sessions.setdefault(key.group(2), {}))
                    break
                if name in ("cpu", "memory"):
                    has_platform = True
                    _flatten(update.get("val"), f"{name}/{rest}" if rest else name, platform)
                    break
        return TelemetrySample(
            device_name=self._device,
            timestamp=_sample_time(notification.get("timestamp")),
            interfaces=tuple(self._interface(n, leaves) for n, leaves in sorted(interfaces.items())),
            sessions=tuple(self._session(p, leaves) for p, leaves in sorted(sessions.items())),
            platform=self._platform(platform) if has_platform else None,
            sync=bool(message.get("sync_response")),
        )

    def _interface(self, name: str, leaves: dict[str, Any]) -> InterfaceSample:
        counters = {field: self._int(leaves.get(leaf), name, leaf) for field, leaf in _INTERFACE_COUNTERS.items()}
        return InterfaceSample(name=name, oper_up=self._oper_up(leaves.get("oper-state"), name), **counters)

    def _session(self, peer: str, leaves: dict[str, Any]) -> BgpSessionSample:
        state = leaves.get("session-state")
        if state is not None and not isinstance(state, str):
            self._warn(peer, "session-state", state)
            state = None
        return BgpSessionSample(
            peer=peer,
            state=state.lower() if state is not None else None,
            prefixes_received=self._int(leaves.get(_RECEIVED_ROUTES), peer, "received-routes"),
        )

    def _platform(self, leaves: dict[str, Any]) -> PlatformSample:
        return PlatformSample(
            cpu_percent=self._int(leaves.get("cpu/total/instant"), "cpu", "instant"),
            memory_percent=self._int(leaves.get("memory/utilization"), "memory", "utilization"),
        )

    def _oper_up(self, value: Any, item: str) -> bool | None:
        if value is None:
            return None
        word = str(value).lower()
        if word in ("up", "down"):
            return word == "up"
        self._warn(item, "oper-state", value)
        return None

    def _int(self, value: Any, item: str, leaf: str) -> int | None:
        if value is None:
            return None
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value)
        self._warn(item, leaf, value)
        return None

    def _warn(self, item: str, leaf: str, value: Any) -> None:
        """Log a value that does not parse, once per stream for each item and leaf."""
        if (item, leaf) in self._warned:
            return
        self._warned.add((item, leaf))
        _logger.warning(
            "telemetry from %s: %s %s = %.40r does not parse; read as no value", self._device, item, leaf, value
        )
