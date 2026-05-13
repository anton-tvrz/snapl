"""Executor ABC — NAF Executor building block contract (T008).

All consumers (Orchestrator, Presentation) interact with the Executor
exclusively through this interface.

Design note: apply/rollback/dry_run return result objects for device-side
outcomes. Exceptions are reserved for programming errors only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from snapl_executor.models import ApplyResult, BatchResult, DryRunResult
    from snapl_intent.models import DesiredState


class Executor(ABC):
    """NAF Executor building block — config deployment interface."""

    @abstractmethod
    async def apply(self, desired: DesiredState) -> ApplyResult:
        """Render and deploy the desired state to the target device via gNMI SET.

        Returns ApplyResult — does not raise for device-side errors.

        Raises:
            ValueError: desired is None (programming error).
        """

    @abstractmethod
    async def rollback(self, desired: DesiredState) -> ApplyResult:
        """Re-apply a prior known-good desired state.

        Identical to apply() but sets ApplyResult.is_rollback=True.

        Raises:
            ValueError: desired is None (programming error).
        """

    @abstractmethod
    async def dry_run(self, desired: DesiredState) -> DryRunResult:
        """Render the desired state without applying it.

        No gNMI connection is made. Render errors are returned in the result.

        Raises:
            ValueError: desired is None (programming error).
        """

    @abstractmethod
    async def apply_batch(self, states: list[DesiredState]) -> BatchResult:
        """Apply desired state to multiple devices, returning per-device results.

        A failure on one device does not prevent application to others.

        Raises:
            ValueError: states is empty or contains duplicate device IDs.
        """
