"""Observer ABC — NAF Observability building block interface.

All consumers (Orchestrator, Presentation) depend on this ABC rather than the
concrete StructuralObserver — the ABC keeps the comparison strategy swappable
and is the single integration point for the rest of the NAF loop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from snapl_collector.models import CollectResult
    from snapl_intent.models import DesiredState
    from snapl_observability.models import (
        AuditEntry,
        BatchDriftReport,
        DriftReport,
        ObservabilityEvent,
    )


class Observer(ABC):
    """NAF Observability building block — drift, events, and audit interface."""

    # ---- Drift detection ----------------------------------------------------

    @abstractmethod
    async def detect_drift(
        self,
        desired: DesiredState,
        actual: CollectResult,
    ) -> DriftReport:
        """Compare desired against live state and produce a DriftReport.

        A failed CollectResult (success=False) is reflected as
        DriftReport(status=ERROR) — the upstream collector's failure is never
        re-raised.

        Raises:
            ValueError: desired.device.id != actual.device_id (programming error).
        """

    @abstractmethod
    async def detect_drift_batch(
        self,
        pairs: list[tuple[DesiredState, CollectResult]],
    ) -> BatchDriftReport:
        """Run drift detection across multiple devices.

        Each pair is processed independently; one device's outcome does not
        affect another's. Mismatched device IDs in any pair raise immediately.

        Raises:
            ValueError: pairs is empty, or any pair has mismatched device IDs.
        """

    # ---- Event emission -----------------------------------------------------

    @abstractmethod
    async def emit_event(self, report: DriftReport) -> ObservabilityEvent:
        """Emit a structured event derived from a DriftReport.

        The event type is mapped 1:1 from report.status. The event is dispatched
        to every registered EventBus handler and returned to the caller.
        """

    # ---- Audit logging ------------------------------------------------------

    @abstractmethod
    async def log_audit(self, entry: AuditEntry) -> None:
        """Append an immutable AuditEntry to the audit log."""
