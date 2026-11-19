"""Result models for the NAF Collector building block."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID  # noqa: TC003 — runtime import: pydantic/Temporal resolve across workflow boundary

if TYPE_CHECKING:
    from snapl_intent.models import Device


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True)
class CollectResult:
    """Outcome of a single collect() or get_running_config() call.

    Invariants:
    - success=True  → error is None
    - success=False → error is set; data is empty dict
    """

    device_id: UUID
    device_name: str
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    paths: list[str] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0
    timestamp: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if self.success and self.error is not None:
            raise ValueError("error must be None when success=True")
        if not self.success and not self.error:
            raise ValueError("error must be set when success=False")


@dataclass(frozen=True)
class BatchCollectResult:
    """Aggregated outcome of a collect_batch() call.

    Invariant: succeeded + failed == total
    """

    results: dict[UUID, CollectResult] = field(default_factory=dict)
    total: int = 0
    succeeded: int = 0
    failed: int = 0

    def __post_init__(self) -> None:
        if self.succeeded + self.failed != self.total:
            raise ValueError(f"succeeded ({self.succeeded}) + failed ({self.failed}) must equal total ({self.total})")


# ── Telemetry (spec 016) ─────────────────────────────────────────────────


@dataclass(frozen=True)
class TelemetryTarget:
    """What intent names on one device: the interfaces and BGP peers to watch.

    The items are held sorted and once, so two targets are equal when the same
    device and the same items are named, in any order. That is how the runner
    tells that intent changed (spec 016 FR-004).
    """

    device: Device
    interfaces: tuple[str, ...]
    peers: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "interfaces", tuple(sorted(set(self.interfaces))))
        object.__setattr__(self, "peers", tuple(sorted(set(self.peers))))


@dataclass(frozen=True)
class InterfaceSample:
    """One interface in one telemetry message. ``None``: the message did not carry that leaf."""

    name: str
    oper_up: bool | None = None
    in_octets: int | None = None
    out_octets: int | None = None
    in_errors: int | None = None
    out_errors: int | None = None
    in_discards: int | None = None
    out_discards: int | None = None


@dataclass(frozen=True)
class BgpSessionSample:
    """One BGP session end in one telemetry message.

    ``state`` is the device's word (``established``, ``active``, ...), lower-cased.
    """

    peer: str
    state: str | None = None
    prefixes_received: int | None = None


@dataclass(frozen=True)
class PlatformSample:
    """The device's CPU and memory use, in percent."""

    cpu_percent: int | None = None
    memory_percent: int | None = None


@dataclass(frozen=True)
class TelemetrySample:
    """One gNMI telemetry message, vendor-neutral.

    ``timestamp`` is the device's, from the notification. ``sync`` marks the
    first message of a stream, which carries every item.
    """

    device_name: str
    timestamp: datetime
    interfaces: tuple[InterfaceSample, ...] = ()
    sessions: tuple[BgpSessionSample, ...] = ()
    platform: PlatformSample | None = None
    sync: bool = False
