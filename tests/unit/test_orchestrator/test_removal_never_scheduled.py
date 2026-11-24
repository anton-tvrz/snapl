"""Scheduled runs never remove config (spec 013 FR-012, T030).

A removal runs only when an operator confirms it with ``snapl remove --apply``.
The workflows a schedule or a deploy reaches must not import the removal
workflows or activities, so none of them can start one. ``schedules.py`` builds
only ``DriftResponse`` actions, which its own tests assert.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import snapl_orchestrator

pytestmark = pytest.mark.unit

_WORKFLOWS = Path(snapl_orchestrator.__file__).parent / "workflows"
_SCHEDULED = ["drift_response.py", "reconcile_devices.py", "deploy_intent.py"]
_FORBIDDEN = {"remove_config", "dry_run_remove", "plan_removal"}


def _imports(source: str) -> set[str]:
    """Every module and imported name in ``source``, fully qualified where it can be."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            found.add(module)
            found.update(alias.name for alias in node.names)
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return found


def _offences(source: str) -> set[str]:
    names = _imports(source)
    return {n for n in names if n.endswith("workflows.removal") or n == "removal"} | (names & _FORBIDDEN)


@pytest.mark.parametrize("module", _SCHEDULED)
def test_a_scheduled_workflow_cannot_reach_removal(module: str) -> None:
    assert _offences((_WORKFLOWS / module).read_text()) == set()


@pytest.mark.parametrize(
    "line",
    [
        "from snapl_orchestrator.workflows.removal import RemoveConfigWorkflow",
        "from snapl_orchestrator.workflows import removal",
        "import snapl_orchestrator.workflows.removal",
        "from snapl_orchestrator.activities.executor import apply_config, remove_config",
        "from snapl_orchestrator.activities.executor import dry_run_remove",
        "from snapl_orchestrator.activities.observability import plan_removal",
    ],
)
def test_the_check_catches_each_way_in(line: str) -> None:
    assert _offences(line)
