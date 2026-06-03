"""Unit tests for ScanDriftWorkflow — placeholders; real coverage tracked in #10."""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skip(reason="Placeholder — real WorkflowEnvironment coverage tracked in #10"),
]


def test_scan_drift_workflow_placeholder() -> None:
    """Tracked: 3-device scan with one drifted produces correct DriftScanResult counts."""


def test_scan_drift_workflow_records_terminal_audit_event() -> None:
    """Tracked: WORKFLOW_STARTED and WORKFLOW_TERMINATED audit events recorded."""


def test_scan_drift_workflow_per_device_error_isolated() -> None:
    """Tracked: one device's collect failure does not abort the scan; errored count incremented."""
