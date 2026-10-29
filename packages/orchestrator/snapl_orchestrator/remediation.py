"""Which drifted devices a scheduled repair may touch (spec 009 FR-013, research R5).

Pure and deterministic, so ``DriftResponseWorkflow`` calls it inline. It
deliberately differs from ``snapl reconcile --drifted``, which takes every
DRIFTED device: a human running that command has read the scan first, and a
schedule has not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from snapl_observability.models import DriftStatus
from snapl_orchestrator.models import ExclusionReason

if TYPE_CHECKING:
    from uuid import UUID

    from snapl_orchestrator.models import DriftScanResult


def select_remediation(scan: DriftScanResult) -> tuple[list[UUID], dict[UUID, ExclusionReason]]:
    """Split a scan into devices to reconcile and devices deliberately left alone.

    A device is reconciled when it has at least one forward drift item (intent
    wants something the device lacks or has wrong). An undesired-only device is
    excluded: a deploy cannot remove that config, so repairing it would rewrite
    the device every interval without converging (#121). An errored device is
    excluded because the scan could not see it. Clean devices are in neither.
    Scan order is preserved.
    """
    remediated: list[UUID] = []
    excluded: dict[UUID, ExclusionReason] = {}
    for device_id, report in scan.reports.items():
        if report.status == DriftStatus.ERROR:
            excluded[device_id] = ExclusionReason.ERRORED
        elif report.status == DriftStatus.DRIFTED:
            if any(not item.undesired for item in report.items):
                remediated.append(device_id)
            else:
                excluded[device_id] = ExclusionReason.UNDESIRED_ONLY
    return remediated, excluded
