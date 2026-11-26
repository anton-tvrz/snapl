"""NAF Collector building block — live network data retrieval."""

from snapl_collector.abc import Collector
from snapl_collector.exceptions import CollectorConfigError, CollectorError, TelemetryStreamError
from snapl_collector.models import (
    BatchCollectResult,
    BgpSessionSample,
    CollectResult,
    InterfaceSample,
    PlatformSample,
    TelemetrySample,
    TelemetryTarget,
)

__all__ = [
    "BatchCollectResult",
    "BgpSessionSample",
    "CollectResult",
    "Collector",
    "CollectorConfigError",
    "CollectorError",
    "InterfaceSample",
    "PlatformSample",
    "TelemetrySample",
    "TelemetryStreamError",
    "TelemetryTarget",
]
