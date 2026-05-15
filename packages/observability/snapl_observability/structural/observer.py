"""StructuralObserver — concrete Observer implementation.

Composes the pure structural diff function with an EventBus and AuditLog.
Stateless w.r.t. the network — every input is pre-fetched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from snapl_observability.abc import Observer
from snapl_observability.audit import AuditLog
from snapl_observability.events import EventBus
from snapl_observability.models import (
    STATUS_TO_EVENT_TYPE,
    AuditEntry,
    AuditOperation,
    AuditOutcome,
    BatchDriftReport,
    DriftReport,
    DriftStatus,
    ObservabilityEvent,
)
from snapl_observability.structural.diff import diff_desired_vs_actual

if TYPE_CHECKING:
    from snapl_collector.models import CollectResult
    from snapl_intent.models import DesiredState


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class StructuralObserver(Observer):
    """Structural value-equality Observer for snapl-intent entity types."""

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        audit_log: AuditLog | None = None,
        component_name: str = "StructuralObserver",
    ) -> None:
        self.event_bus = event_bus if event_bus is not None else EventBus()
        self.audit_log = audit_log if audit_log is not None else AuditLog()
        self.component_name = component_name

    # ---- Drift detection ----------------------------------------------------

    async def detect_drift(
        self,
        desired: DesiredState,
        actual: CollectResult,
    ) -> DriftReport:
        if desired.device.id != actual.device_id:
            raise ValueError(
                f"detect_drift: desired.device.id ({desired.device.id}) does not match "
                f"actual.device_id ({actual.device_id})"
            )

        if not actual.success:
            report = DriftReport(
                device_id=desired.device.id,
                device_name=desired.device.name,
                status=DriftStatus.ERROR,
                items=[],
                error=actual.error,
                timestamp=_utc_now(),
            )
        else:
            items = diff_desired_vs_actual(desired, actual.data)
            status = DriftStatus.CLEAN if not items else DriftStatus.DRIFTED
            report = DriftReport(
                device_id=desired.device.id,
                device_name=desired.device.name,
                status=status,
                items=items,
                timestamp=_utc_now(),
            )

        self.audit_log.append(
            AuditEntry(
                operation=AuditOperation.DETECT_DRIFT,
                device_id=desired.device.id,
                component=self.component_name,
                outcome=AuditOutcome.SUCCESS,
                detail={"status": report.status.value, "item_count": len(report.items)},
                timestamp=_utc_now(),
            )
        )
        return report

    async def detect_drift_batch(
        self,
        pairs: list[tuple[DesiredState, CollectResult]],
    ) -> BatchDriftReport:
        if not pairs:
            raise ValueError("detect_drift_batch: pairs must be non-empty")

        # Validate all device IDs match before doing any work.
        for desired, actual in pairs:
            if desired.device.id != actual.device_id:
                raise ValueError(
                    f"detect_drift_batch: desired.device.id ({desired.device.id}) does not "
                    f"match actual.device_id ({actual.device_id})"
                )

        reports: dict = {}
        clean = drifted = errored = 0
        for desired, actual in pairs:
            report = await self.detect_drift(desired, actual)
            reports[desired.device.id] = report
            if report.status == DriftStatus.CLEAN:
                clean += 1
            elif report.status == DriftStatus.DRIFTED:
                drifted += 1
            else:
                errored += 1

        return BatchDriftReport(
            reports=reports,
            total=len(pairs),
            clean=clean,
            drifted=drifted,
            errored=errored,
        )

    # ---- Event emission -----------------------------------------------------

    async def emit_event(self, report: DriftReport) -> ObservabilityEvent:
        event = ObservabilityEvent(
            event_type=STATUS_TO_EVENT_TYPE[report.status],
            device_id=report.device_id,
            device_name=report.device_name,
            report=report,
            timestamp=_utc_now(),
        )
        self.event_bus.emit(event)
        self.audit_log.append(
            AuditEntry(
                operation=AuditOperation.EMIT_EVENT,
                device_id=report.device_id,
                component=self.component_name,
                outcome=AuditOutcome.SUCCESS,
                detail={"event_type": event.event_type.value},
                timestamp=_utc_now(),
            )
        )
        return event

    # ---- Audit logging ------------------------------------------------------

    async def log_audit(self, entry: AuditEntry) -> None:
        self.audit_log.append(entry)
