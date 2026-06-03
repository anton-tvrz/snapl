"""Unit tests for ReconcileDevicesWorkflow.

Input-validation and class-registration tests run normally. The full
WorkflowEnvironment per-device outcome test is a placeholder — real coverage
is tracked in #11 (see test_workflow_deploy_intent.py for the reference pattern).
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


@pytest.mark.skip(reason="Placeholder — real WorkflowEnvironment coverage tracked in #11")
def test_reconcile_devices_workflow_per_device_outcomes() -> None:
    """Tracked: child workflow per device with USE_EXISTING serialization."""
    _ = uuid4()  # placeholder to mark referenced symbol
