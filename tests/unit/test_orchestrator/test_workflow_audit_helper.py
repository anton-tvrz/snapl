"""One workflow-side audit helper for the four workflows (#139).

Each workflow had its own copy of "build an AuditEvent, run record_audit_event"
with its own copy of the retry policy and timeout.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import snapl_orchestrator.workflows as workflows_package

pytestmark = pytest.mark.unit

_WORKFLOWS = Path(workflows_package.__file__).resolve().parent
_MODULES = ("deploy_intent", "scan_drift", "reconcile_devices", "drift_response")


def _calls(tree: ast.AST, name: str) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    )


@pytest.mark.parametrize("module", _MODULES)
def test_a_workflow_builds_no_audit_event_and_holds_no_audit_retry_policy_of_its_own(module: str) -> None:
    source = (_WORKFLOWS / f"{module}.py").read_text()
    tree = ast.parse(source)

    assert _calls(tree, "AuditEvent") == 0, f"{module} builds AuditEvent itself"
    assert "_AUDIT_RETRY" not in source
    assert "record_audit_event" not in source, f"{module} runs the audit activity itself"


def test_the_shared_helper_is_the_one_place_that_does() -> None:
    tree = ast.parse((_WORKFLOWS / "audit.py").read_text())

    assert _calls(tree, "AuditEvent") == 1


def test_the_retry_policy_and_timeout_are_what_every_copy_had() -> None:
    from datetime import timedelta

    from snapl_orchestrator.workflows.audit import AUDIT_RETRY, AUDIT_TIMEOUT

    assert AUDIT_RETRY.initial_interval == timedelta(seconds=1)
    assert AUDIT_RETRY.maximum_attempts == 5
    assert timedelta(seconds=10) == AUDIT_TIMEOUT
