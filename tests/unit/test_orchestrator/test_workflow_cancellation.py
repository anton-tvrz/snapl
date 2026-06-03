"""Cancellation handling — placeholders; real coverage tracked in #12."""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skip(reason="Placeholder — real WorkflowEnvironment coverage tracked in #12"),
]


def test_deploy_intent_cancellation_records_workflow_cancelled_event() -> None:
    """Tracked: handle.cancel() → WORKFLOW_CANCELLED audit event + WorkflowResult reason=CANCELLED."""


def test_scan_drift_cancellation_returns_partial_result() -> None:
    """Tracked: cancellation during scan emits cancelled audit event and bounded cleanup."""


def test_reconcile_devices_cancellation_propagates_to_children() -> None:
    """Tracked: cancellation propagates to in-flight child DeployIntent workflows."""
