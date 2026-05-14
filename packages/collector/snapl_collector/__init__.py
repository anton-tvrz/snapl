"""NAF Collector building block — live network data retrieval."""

from snapl_collector.abc import Collector
from snapl_collector.exceptions import CollectorConfigError, CollectorError
from snapl_collector.models import BatchCollectResult, CollectResult

__all__ = [
    "BatchCollectResult",
    "CollectResult",
    "Collector",
    "CollectorConfigError",
    "CollectorError",
]
