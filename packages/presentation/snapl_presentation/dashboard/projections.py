"""Pure projections from audit events to dashboard views (spec 010 research R4).

The ``AuditLog`` ABC has no "latest per device" query, and adding one would
widen a block contract for one reader (research R3). So the dashboard reads
events and folds them here. These functions touch no I/O, which keeps every
rule below testable from hand-built events.

Events are folded in ``timestamp`` order, never arrival order: a row can be
inserted well after its timestamp (research R3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from snapl_orchestrator.models import AuditEventType
from snapl_presentation.dashboard.models import DeployOutcome, DeviceView, DriftState

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from snapl_intent.models import DesiredState
    from snapl_orchestrator.models import AuditEvent

# Undesired config is reported, never removed automatically (#65).
UNDESIRED_NOTE = "Config on the device but not in intent is not removed automatically (#65)."

_DEPLOY_TERMINAL = {AuditEventType.WORKFLOW_TERMINATED, AuditEventType.WORKFLOW_CANCELLED}


def _device_id(event: AuditEvent) -> UUID | None:
    """The device an event is about, or None for a use-case-level event."""
    return event.target_id if isinstance(event.target_id, UUID) else None


def _is_scan_record(event: AuditEvent) -> bool:
    """A ScanDrift per-device record (``scan_drift._audit_device``).

    DeployIntent also writes ``detect_drift`` against the device, for its own
    post-apply check, with an empty payload. That is not a scan of the fabric.
    """
    return (
        event.workflow_type == "ScanDrift"
        and event.activity_name == "detect_drift"
        and event.event_type in {AuditEventType.ACTIVITY_COMPLETED, AuditEventType.ACTIVITY_FAILED}
        and _device_id(event) is not None
    )


def _is_deploy_end(event: AuditEvent) -> bool:
    return event.workflow_type == "DeployIntent" and event.event_type in _DEPLOY_TERMINAL


def _scan_fields(event: AuditEvent | None, name: str) -> dict:
    """The DeviceView fields one scan record implies."""
    if event is None:
        return {"drift_state": DriftState.NEVER_SCANNED}
    base = {"last_scanned_at": event.timestamp, "last_scan_workflow_id": event.workflow_id}
    payload = event.payload
    if event.event_type is AuditEventType.ACTIVITY_FAILED:
        return {**base, "drift_state": DriftState.ERROR, "error": payload.get("error") or "scan failed"}
    paths: list[str] = list(payload.get("paths") or [])
    if not paths:
        return {**base, "drift_state": DriftState.IN_SYNC}
    return {**base, **_drift_fields(paths, payload, name)}


def _drift_fields(paths: list[str], payload: dict, name: str) -> dict:
    """Classify a drifted scan by direction (FR-012a).

    Reconcile is suggested whenever intent may be missing from the device,
    including a pre-FR-012a scan whose direction is unknown. Undesired config is
    not removed by reconcile (#65), so it gets a note and no command.
    """
    reconcile = f"snapl reconcile {name}"
    if "undesired_paths" not in payload:
        # Recorded before FR-012a: the direction is unknown, so do not guess it.
        return {"drift_state": DriftState.DRIFTED, "unclassified_paths": paths, "suggested_command": reconcile}
    undesired_set = set(payload["undesired_paths"])
    missing = [path for path in paths if path not in undesired_set]
    undesired = [path for path in paths if path in undesired_set]
    if not undesired:
        return {"drift_state": DriftState.MISSING, "missing_paths": missing, "suggested_command": reconcile}
    if not missing:
        return {"drift_state": DriftState.UNDESIRED, "undesired_paths": undesired, "note": UNDESIRED_NOTE}
    return {
        "drift_state": DriftState.MISSING_AND_UNDESIRED,
        "missing_paths": missing,
        "undesired_paths": undesired,
        "suggested_command": reconcile,
        "note": UNDESIRED_NOTE,
    }


def _deploy_outcome(event: AuditEvent | None) -> DeployOutcome | None:
    if event is None:
        return None
    return DeployOutcome(
        workflow_id=event.workflow_id,
        success=event.outcome == "success",
        reason=event.reason,
        ended_at=event.timestamp,
    )


def project_devices(
    events: Sequence[AuditEvent],
    states: Sequence[DesiredState] | None,
    names: Mapping[UUID, str],
) -> list[DeviceView]:
    """Each device's drift state, last scan and last deploy.

    Args:
        events: audit events, in any order.
        states: the Source of Truth's devices for the use case, or None when it
            could not be read. Then the list is the degraded one (research
            R13): every device the audit trail mentions, with no role.
        names: device names from the last successful SoT read in this process,
            used to name devices in the degraded list.

    Returns:
        The devices, spines first and then by name.
    """
    last_scan: dict[UUID, AuditEvent] = {}
    last_deploy: dict[UUID, AuditEvent] = {}
    seen: set[UUID] = set()
    for event in sorted(events, key=lambda e: e.timestamp):
        device_id = _device_id(event)
        if device_id is None:
            continue
        seen.add(device_id)
        if _is_scan_record(event):
            last_scan[device_id] = event
        elif _is_deploy_end(event):
            last_deploy[device_id] = event

    devices: list[tuple[UUID, str, str | None, bool]]
    if states is not None:
        devices = [(s.device.id, s.device.name, s.device.role, True) for s in states]
    else:
        devices = [(d, names.get(d, str(d)), None, d in names) for d in seen]

    views = [
        DeviceView(
            device_id=device_id,
            name=name,
            role=role,
            in_intent=in_intent,
            last_deploy=_deploy_outcome(last_deploy.get(device_id)),
            **_scan_fields(last_scan.get(device_id), name),
        )
        for device_id, name, role, in_intent in devices
    ]
    return sorted(views, key=lambda view: (view.role != "spine", view.name))
