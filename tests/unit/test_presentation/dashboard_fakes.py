"""Shared inputs for the spec 011 dashboard tests.

- ``seed_states`` builds ``DesiredState``s from a use case's seed file, so the
  topology tests run against the interfaces and peers snapl actually seeds.
- ``arc_events`` reads the audit trail captured from a live demo arc
  (fixtures/README.md), and ``ARC`` names its devices.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

import yaml

from snapl_intent.models import DesiredState, Device, Interface
from snapl_orchestrator.audit.sqlite import SqliteAuditLog
from snapl_orchestrator.models import AuditEvent, AuditEventType

REPO = Path(__file__).resolve().parents[3]
SEEDS = REPO / "packages" / "intent" / "snapl_intent" / "seed"
ARC_FIXTURE = Path(__file__).parent / "fixtures" / "audit_demo_arc.sqlite"
# Spec 015 T042 (FR-019): set to the path `write_arc_with_scan_steps` wrote to run
# every suite on the arc with a scan's new step events added. Unset in CI.
ARC_FIXTURE = Path(os.environ.get("SNAPL_ARC_FIXTURE") or ARC_FIXTURE)
# One deploy that failed at apply, captured from the lab (spec 011 T057).
FAILED_DEPLOY_FIXTURE = Path(__file__).parent / "fixtures" / "audit_failed_deploy.sqlite"

# name -> (role, device id) for the devices in the captured demo arc.
ARC: dict[str, tuple[str, UUID]] = {
    "spine-01": ("spine", UUID("18e29d6d-e0c5-0ba7-32d1-c510fc3ef077")),
    "spine-02": ("spine", UUID("18e29d6e-1959-3451-32dc-c51d9065aa58")),
    "leaf-01": ("leaf", UUID("18e29d6e-246e-5cd1-32d2-c51d13d4de9e")),
    "leaf-02": ("leaf", UUID("18e29d6e-2e5b-c496-32da-c510ade043a0")),
    "leaf-03": ("leaf", UUID("18e29d6e-3794-c788-32d5-c519c5f18e3a")),
    "leaf-04": ("leaf", UUID("18e29d6e-422b-a401-32d9-c5162961ffbe")),
}

# The three scans of the arc ended at these times (fixtures/README.md).
SCAN_1_END = datetime(2026, 11, 7, 21, 20, 13, 770294, tzinfo=UTC)
SCAN_2_END = datetime(2026, 11, 7, 21, 20, 15, 389714, tzinfo=UTC)
SCAN_3_END = datetime(2026, 11, 7, 21, 20, 18, 919877, tzinfo=UTC)


def state(name: str, role: str, device_id: UUID | None = None, links: list[tuple] | None = None) -> DesiredState:
    """A device with interfaces given as ``(name, peer_device, peer_interface)``."""
    device_id = device_id or uuid5(NAMESPACE_DNS, name)
    interfaces = [
        Interface(
            id=uuid5(NAMESPACE_DNS, f"{name}:{interface}"),
            device_id=device_id,
            name=interface,
            peer_device=peer_device,
            peer_interface=peer_interface,
        )
        for interface, peer_device, peer_interface in links or []
    ]
    return DesiredState(
        device=Device(id=device_id, name=name, management_address="10.0.0.1", role=role, use_case="dcfabric"),
        interfaces=interfaces,
    )


def seed_states(use_case: str, ids: dict[str, UUID] | None = None) -> list[DesiredState]:
    """The use case's devices and interfaces, as its seed file declares them."""
    seed = yaml.safe_load((SEEDS / use_case / "topology.yml").read_text())
    by_device: dict[str, list[tuple]] = {}
    for interface in seed["interfaces"]:
        by_device.setdefault(interface["device"], []).append(
            (interface["name"], interface.get("peer_device"), interface.get("peer_interface"))
        )
    return [
        state(device["name"], device["role"], (ids or {}).get(device["name"]), by_device.get(device["name"]))
        for device in seed["devices"]
    ]


def arc_states() -> list[DesiredState]:
    """dcfabric's seed, with the device ids the captured arc recorded."""
    return seed_states("dcfabric", {name: device_id for name, (_, device_id) in ARC.items()})


async def arc_events() -> list[AuditEvent]:
    return await _fixture_events(ARC_FIXTURE)


async def failed_deploy_events() -> list[AuditEvent]:
    return await _fixture_events(FAILED_DEPLOY_FIXTURE)


async def _fixture_events(path: Path) -> list[AuditEvent]:
    log = SqliteAuditLog(database_url=str(path), read_only=True)
    await log.initialize()
    try:
        return await log.query_by_time_range(datetime(1970, 1, 1, tzinfo=UTC), datetime(9999, 1, 1, tzinfo=UTC))
    finally:
        await log.close()


def with_scan_steps(events: list[AuditEvent]) -> list[AuditEvent]:
    """The arc as a scan records it since spec 015 (FR-016): the new step events added.

    Each ScanDrift run gets its ``fetch_devices_for_use_case`` step just after
    it starts, and each drift check gets a ``collect_drift_state`` step for its
    device just before it, failed when the check failed on a failed collect.
    Nothing already in the trail is changed.
    """
    added: list[AuditEvent] = []
    tick = timedelta(microseconds=1)
    for event in events:
        if event.workflow_type != "ScanDrift":
            continue
        common = {"workflow_id": event.workflow_id, "workflow_type": "ScanDrift", "actor": event.actor}
        if event.event_type is AuditEventType.WORKFLOW_STARTED:
            added.append(
                AuditEvent(
                    event_id=uuid4(),
                    target_id=event.target_id,
                    event_type=AuditEventType.ACTIVITY_COMPLETED,
                    activity_name="fetch_devices_for_use_case",
                    outcome="success",
                    payload={"devices": len(ARC)},
                    timestamp=event.timestamp + tick,
                    **common,
                )
            )
        elif event.activity_name == "detect_drift":
            error = str(event.payload.get("error") or "")
            failed = error.startswith("collect failed")
            added.append(
                AuditEvent(
                    event_id=uuid4(),
                    target_id=event.target_id,
                    event_type=AuditEventType.ACTIVITY_FAILED if failed else AuditEventType.ACTIVITY_COMPLETED,
                    activity_name="collect_drift_state",
                    outcome="failure" if failed else "success",
                    payload={"error": error} if failed else {},
                    timestamp=event.timestamp - tick,
                    **common,
                )
            )
    return sorted([*events, *added], key=lambda e: e.timestamp)


async def write_arc_with_scan_steps(path: Path) -> Path:
    """Write the arc with a scan's step events added (``with_scan_steps``) to a new SQLite trail."""
    log = SqliteAuditLog(database_url=str(path))
    await log.initialize()
    try:
        for event in with_scan_steps(await arc_events()):
            await log.append(event)
    finally:
        await log.close()
    return path
