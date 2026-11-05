"""Executor ABC — NAF Executor building block contract (T008).

All consumers (Orchestrator, Presentation) interact with the Executor
exclusively through this interface.

Error contract: every entrypoint returns its result model for render and
device-side outcomes. A renderer that can't be built (unknown use case) or a
render that fails (template error, rendered text that is not JSON) comes back
as ``success=False`` with the error message set, from apply, rollback,
dry_run and apply_batch alike (#56). Exceptions are reserved for programming
errors only.
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

        Returns ApplyResult — does not raise for render or device-side errors.
        A render failure sets success=False and error, and nothing is sent.

        Raises:
            ValueError: desired is None (programming error).
        """

    @abstractmethod
    async def rollback(self, desired: DesiredState) -> ApplyResult:
        """Re-apply a prior known-good desired state.

        Identical to apply() but sets ApplyResult.is_rollback=True, including
        on a render failure.

        Raises:
            ValueError: desired is None (programming error).
        """

    @abstractmethod
    async def dry_run(self, desired: DesiredState) -> DryRunResult:
        """Render the desired state without applying it.

        No gNMI connection is made. A render failure, unknown use case
        included, returns success=False with render_error set.

        Raises:
            ValueError: desired is None (programming error).
        """

    @abstractmethod
    async def apply_batch(self, states: list[DesiredState]) -> BatchResult:
        """Apply desired state to multiple devices, returning per-device results.

        A failure on one device, render or device-side, is that device's
        ApplyResult (success=False, error set) and does not prevent
        application to the others.

        Raises:
            ValueError: states is empty or contains duplicate device IDs.
        """
