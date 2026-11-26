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
    from snapl_executor.models import ApplyResult, BatchResult, DryRunResult, RemoveResult
    from snapl_intent.models import DesiredState, Device


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
        """Re-apply a desired state, marked as a rollback. It does not restore.

        Identical to apply() but sets ApplyResult.is_rollback=True, including
        on a render failure. Nothing is read from the device first and no
        earlier device config is kept, so this cannot put a device back the
        way it was: it renders ``desired`` and merges it, as apply() does
        (#36). Rolling back means converging again on a desired state the
        caller holds, usually the Source of Truth's current one. Config the
        failed change added and ``desired`` does not mention stays on the
        device (#65).

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

    # ---- Removal (spec 013) -------------------------------------------------

    @abstractmethod
    async def dry_run_remove(self, device: Device, paths: list[str]) -> RemoveResult:
        """Check deletes against the executor's guard without connecting.

        The dry run that precedes every removal (constitution VIII.4, VIII.5).
        A refused path makes success=False with ``refused`` naming it.

        Raises:
            ValueError: paths is empty or holds a duplicate (programming error).
        """

    @abstractmethod
    async def remove(self, device: Device, paths: list[str]) -> RemoveResult:
        """Delete ``paths`` from the device in one gNMI Set of deletes only.

        Checks the same guard as ``dry_run_remove`` first; if any path is
        refused, nothing is sent. The device applies all of the deletes or
        none (FR-008). A rejection is success=False with the device's reason
        in ``error``, not an exception.

        Raises:
            ValueError: paths is empty or holds a duplicate (programming error).
        """
