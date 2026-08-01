"""The demo arc, asserted end to end (issue #100).

This is `docs/demo-scenarios.md` turned into a test: clean SoT → provision →
seed → deploy → induce drift → scan DRIFTED → reconcile → scan CLEAN → audit
trail. If it passes, the demo works.

The value over `tests/integration/test_orchestrator/` is the prefix: that suite
begins from an environment already seeded and dialable, and skips when it is
not. Every failure that actually cost time lived in exactly that skipped
prefix (#87, #78, #90, #96).

Run it::

    uv run invoke dev.deps
    uv run invoke dev.lab-deploy
    SNAPL_E2E=1 uv run pytest tests/e2e -m e2e

The phases share state through the module-scoped `arc` fixture and run in file
order, so a failure in an early phase makes the later ones fail too — which is
correct here: they are one scenario, not five independent ones.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest

from snapl_observability.models import DriftStatus
from snapl_orchestrator.models import AuditEventType, WorkflowReason
from snapl_orchestrator.workflows.deploy_intent import DeployIntentWorkflow
from snapl_orchestrator.workflows.reconcile_devices import ReconcileDevicesWorkflow
from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow
from tests.e2e.conftest import EXPECTED_DEVICE_COUNT, USE_CASE, can_reach

if TYPE_CHECKING:
    from snapl_intent.models import DesiredState

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

_EXECUTION_TIMEOUT = timedelta(seconds=180)
_DRIFT_PATH = "/interface[name=ethernet-1/1]"
_DRIFT_DESCRIPTION = "e2e-injected-drift"
_TARGET = "spine-01"


@dataclass
class Arc:
    """State carried across the arc's phases."""

    task_queue: str = field(default_factory=lambda: f"snapl-e2e-{uuid4()}")
    states: list = field(default_factory=list)
    target_id: UUID | None = None
    deploy_workflow_id: str | None = None
    original_description: str | None = None


@pytest.fixture(scope="module")
def arc() -> Arc:
    return Arc()


def _pick(states: list[DesiredState], name: str) -> DesiredState:
    match = [state for state in states if state.device.name == name]
    assert match, f"device {name!r} absent from the seeded SoT (found: {[s.device.name for s in states]})"
    return match[0]


def _gnmi_set(state: DesiredState, credentials, *, description: str) -> None:
    """Change an interface description out of band — real drift, applied the
    way an operator at a CLI would, not through our own executor."""
    from pygnmi.client import gNMIclient

    username, password, port = credentials
    target = state.device.lab_node_name or state.device.management_address
    with gNMIclient(target=(target, port), username=username, password=password, insecure=True) as client:
        client.set(update=[(_DRIFT_PATH, {"description": description})], encoding="json_ietf")


# ---------------------------------------------------------------------------
# Phase 1 — bootstrap: the part the integration suite skips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase1_provision_and_seed(arc: Arc, intent_store, lab_topology_pins, srlinux_credentials) -> None:
    """Provision the schema, seed, and prove the result is actually usable.

    `provision_schema` must block until Infrahub has registered the extension
    attributes: seeding into an unregistered schema is the #87 race, where the
    SDK silently drops attributes it does not know and the seed appears to
    succeed while producing unusable rows.
    """
    provision = await intent_store.provision_schema(USE_CASE)
    assert provision.schemas_loaded > 0, "no schema files were loaded"

    await intent_store.seed(USE_CASE)

    states = await intent_store.get_desired_state(use_case=USE_CASE)
    assert len(states) == EXPECTED_DEVICE_COUNT, (
        f"expected {EXPECTED_DEVICE_COUNT} seeded devices, got {len(states)}: {sorted(s.device.name for s in states)}"
    )
    arc.states = states

    # #96: lab_node_name is the gNMI dial target. A clab hostname here is
    # unresolvable from the host and every apply and collect fails — the exact
    # failure that used to require hand-patching Infrahub after every seed.
    _, _, port = srlinux_credentials
    for state in states:
        target = state.device.lab_node_name
        assert target, f"{state.device.name} has no lab_node_name — nothing can dial it"
        assert target == lab_topology_pins[state.device.name], (
            f"{state.device.name}: SoT dials {target!r} but the lab pins "
            f"{lab_topology_pins[state.device.name]!r} — seed and topology have diverged"
        )
        assert can_reach(target, port), f"{state.device.name} unreachable at {target}:{port}"


# ---------------------------------------------------------------------------
# Phase 2 — deploy (demo scenario 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase2_deploy_succeeds_and_verifies(arc: Arc, activities, worker_factory) -> None:
    """A deploy is not done until the device's running config verifies clean."""
    assert arc.states, "phase 1 did not seed — later phases cannot run"
    target = _pick(arc.states, _TARGET)
    arc.target_id = target.device.id
    arc.deploy_workflow_id = f"e2e-deploy-{uuid4()}"

    client, worker = await worker_factory(arc.task_queue)
    started = time.monotonic()
    async with worker:
        result = await client.execute_workflow(
            DeployIntentWorkflow.run,
            target.device.id,
            id=arc.deploy_workflow_id,
            task_queue=arc.task_queue,
            execution_timeout=_EXECUTION_TIMEOUT,
        )
    elapsed = time.monotonic() - started

    assert result.success, f"deploy failed: {result.reason} {result.detail} drift={result.drift_items}"
    assert result.reason is WorkflowReason.SUCCEEDED
    assert elapsed < 60, f"005 SC-001 violated: deploy took {elapsed:.1f}s"


# ---------------------------------------------------------------------------
# Phase 2b — converge the rest of the fabric
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase2b_fabric_converges_before_drift_is_induced(arc: Arc, activities, worker_factory) -> None:
    """Bring every device to intent, so a later scan means what it claims.

    A freshly deployed lab has never had intent pushed to it: on the boot
    config all six devices are legitimately drifted. Scenario 2's headline
    ("6 devices: 5 clean, 1 drifted") is only true of a fabric that was
    converged first, and phase 3 cannot tell "the device we broke" apart from
    "the five nobody ever deployed" without this step. The demo doc carries
    the same step for the same reason — `snapl reconcile --drifted --yes`.
    """
    device_ids = [state.device.id for state in arc.states]

    client, worker = await worker_factory(arc.task_queue)
    async with worker:
        reconcile = await client.execute_workflow(
            ReconcileDevicesWorkflow.run,
            device_ids,
            id=f"e2e-converge-{uuid4()}",
            task_queue=arc.task_queue,
            execution_timeout=_EXECUTION_TIMEOUT,
        )

    assert reconcile.total == EXPECTED_DEVICE_COUNT
    assert reconcile.failed == 0, (
        f"fabric did not converge: {[(r.reason, r.detail) for r in reconcile.device_results.values() if r.detail]}"
    )


# ---------------------------------------------------------------------------
# Phase 3 — induce drift, detect it (demo scenario 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase3_induced_drift_is_detected_with_its_path(
    arc: Arc, activities, worker_factory, srlinux_credentials
) -> None:
    """The headline claim: not just *that* something changed, but which path."""
    target = _pick(arc.states, _TARGET)
    _gnmi_set(target, srlinux_credentials, description=_DRIFT_DESCRIPTION)

    client, worker = await worker_factory(arc.task_queue)
    async with worker:
        scan = await client.execute_workflow(
            ScanDriftWorkflow.run,
            USE_CASE,
            id=f"e2e-scan-drifted-{uuid4()}",
            task_queue=arc.task_queue,
            execution_timeout=_EXECUTION_TIMEOUT,
        )

    assert scan.total == EXPECTED_DEVICE_COUNT
    assert scan.errored == 0, f"devices errored during scan: {[r.error for r in scan.reports.values() if r.error]}"
    assert scan.drifted == 1, f"expected exactly the device we broke to drift, got {scan.drifted}"

    report = scan.reports[arc.target_id]
    assert report.status is DriftStatus.DRIFTED
    paths = [item.path for item in report.items]
    assert any("ethernet-1/1" in path for path in paths), f"the injected path is not among {paths}"
    assert any(item.actual == _DRIFT_DESCRIPTION for item in report.items), (
        f"the injected value is not reported as actual: {[(i.path, i.desired, i.actual) for i in report.items]}"
    )


# ---------------------------------------------------------------------------
# Phase 4 — heal it, prove it (demo scenario 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase4_reconcile_returns_the_fabric_to_clean(arc: Arc, activities, worker_factory) -> None:
    """Detection without remediation is a dashboard. This closes the loop."""
    client, worker = await worker_factory(arc.task_queue)
    async with worker:
        reconcile = await client.execute_workflow(
            ReconcileDevicesWorkflow.run,
            [arc.target_id],
            id=f"e2e-reconcile-{uuid4()}",
            task_queue=arc.task_queue,
            execution_timeout=_EXECUTION_TIMEOUT,
        )

        assert reconcile.total == 1
        assert reconcile.succeeded == 1, (
            f"reconcile did not succeed: {[(r.reason, r.detail) for r in reconcile.device_results.values()]}"
        )
        assert reconcile.failed == 0

        rescan = await client.execute_workflow(
            ScanDriftWorkflow.run,
            USE_CASE,
            id=f"e2e-scan-clean-{uuid4()}",
            task_queue=arc.task_queue,
            execution_timeout=_EXECUTION_TIMEOUT,
        )

    assert rescan.drifted == 0, (
        "the fabric did not return to clean — remaining drift: "
        f"{[(r.device_name, [i.path for i in r.items]) for r in rescan.reports.values() if r.items]}"
    )
    assert rescan.clean == EXPECTED_DEVICE_COUNT


# ---------------------------------------------------------------------------
# Phase 5 — the audit trail (demo scenario 8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase5_audit_trail_reads_as_the_story_just_run(arc: Arc, audit_db: str) -> None:
    """Everything above left a durable, queryable record."""
    from snapl_orchestrator.audit.sqlite import SqliteAuditLog

    log = SqliteAuditLog(database_url=audit_db)
    await log.initialize()
    try:
        deploy_events = await log.query_by_workflow(arc.deploy_workflow_id)
        device_events = await log.query_by_device(arc.target_id)
    finally:
        await log.close()

    types = [event.event_type for event in deploy_events]
    assert types, f"no audit events recorded for {arc.deploy_workflow_id}"
    assert types[0] is AuditEventType.WORKFLOW_STARTED
    assert types[-1] is AuditEventType.WORKFLOW_TERMINATED
    assert AuditEventType.ACTIVITY_COMPLETED in types, f"no activity events in {types}"

    timestamps = [event.timestamp for event in deploy_events]
    assert timestamps == sorted(timestamps), "audit events are not in chronological order"

    # The per-device query spans every workflow that touched it — the deploy
    # from phase 2, the scans from 3 and 4, and the reconcile's child deploy.
    workflow_types = {event.workflow_type for event in device_events}
    assert {"DeployIntent", "ScanDrift"} <= workflow_types, (
        f"per-device audit does not span workflow types: {workflow_types}"
    )
