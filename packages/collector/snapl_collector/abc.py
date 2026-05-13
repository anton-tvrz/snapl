"""Collector ABC — NAF Collector building block interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from snapl_collector.models import BatchCollectResult, CollectResult
    from snapl_intent.models import Device


class Collector(ABC):
    """NAF Collector building block — live data retrieval interface."""

    @abstractmethod
    async def collect(self, device: Device, paths: list[str]) -> CollectResult:
        """Retrieve data at the specified YANG paths from a device via gNMI GET.

        Args:
            device: The target device descriptor (from snapl_intent).
            paths: One or more YANG path strings to retrieve. Must be non-empty.

        Returns:
            CollectResult with success=True and data dict if GET succeeded,
            or success=False with error detail on device-side failure.

        Raises:
            ValueError: paths is empty.
        """

    @abstractmethod
    async def get_running_config(self, device: Device) -> CollectResult:
        """Retrieve the complete running configuration via gNMI GET at root path.

        Equivalent to collect(device, paths=["/"]).

        Args:
            device: The target device descriptor (from snapl_intent).

        Returns:
            CollectResult with full config dict or error detail.
        """

    @abstractmethod
    async def collect_batch(
        self,
        devices: list[Device],
        paths: list[str],
    ) -> BatchCollectResult:
        """Collect data from multiple devices concurrently.

        Args:
            devices: List of target Device objects. Non-empty, no duplicate IDs.
            paths: YANG paths to retrieve from each device. Non-empty.

        Returns:
            BatchCollectResult with per-device CollectResult entries.

        Raises:
            ValueError: devices is empty, paths is empty, or duplicate device IDs.
        """
