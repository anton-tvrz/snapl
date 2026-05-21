"""Unit tests for ReconcileDevicesWorkflow.

Workflow-environment tests are skipped pending the harness fix (see
test_workflow_deploy_intent.py for context). The non-workflow validation tests
(input validation, model invariants) run normally.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from snapl_orchestrator.workflows.reconcile_devices import ReconcileDevicesWorkflow

pytestmark = pytest.mark.unit


def test_reconcile_devices_workflow_class_registered() -> None:
    """ReconcileDevicesWorkflow is a Temporal workflow class with the right name."""
    # The @workflow.defn decorator sets __temporal_workflow_definition.
    defn = getattr(ReconcileDevicesWorkflow, "__temporal_workflow_definition", None)
    assert defn is not None
    assert defn.name == "ReconcileDevices"


@pytest.mark.asyncio
async def test_reconcile_devices_workflow_rejects_empty_device_ids() -> None:
    """Calling run() with an empty list raises ValueError before any child workflow."""
    wf = ReconcileDevicesWorkflow()
    with pytest.raises(ValueError, match="device_ids must be non-empty"):
        await wf.run([])


@pytest.mark.skip(
    reason=(
        "ReconcileDevicesWorkflow tests rely on the same WorkflowEnvironment harness "
        "that currently hangs on full activity graphs. Tracked alongside DeployIntent "
        "workflow tests; Phase 7 integration tests exercise the full graph."
    ),
)
def test_reconcile_devices_workflow_per_device_outcomes() -> None:
    """Tracked: child workflow per device with USE_EXISTING serialization."""
    _ = uuid4()  # placeholder to mark referenced symbol
