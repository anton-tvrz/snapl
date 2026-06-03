"""Unit tests for the AuditLog ABC — enforces that all four methods are abstract."""

from __future__ import annotations

import pytest

from snapl_orchestrator.audit.abc import AuditLog

pytestmark = pytest.mark.unit


def test_cannot_instantiate_audit_log_abc_directly() -> None:
    with pytest.raises(TypeError):
        AuditLog()  # type: ignore[abstract]


def test_subclass_missing_method_cannot_be_instantiated() -> None:
    class Partial(AuditLog):
        async def append(self, event):  # type: ignore[override]
            return None

        # Missing query_by_workflow / query_by_device / query_by_time_range.

    with pytest.raises(TypeError):
        Partial()  # type: ignore[abstract]


def test_subclass_implementing_all_methods_can_be_instantiated() -> None:
    class Full(AuditLog):
        async def append(self, event):  # type: ignore[override]
            return None

        async def query_by_workflow(self, workflow_id):  # type: ignore[override]
            return []

        async def query_by_device(self, device_id):  # type: ignore[override]
            return []

        async def query_by_time_range(self, start, end):  # type: ignore[override]
            return []

    assert isinstance(Full(), AuditLog)
