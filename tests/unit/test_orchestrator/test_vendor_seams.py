"""Constitution Principle VI: workflows never name a vendor (#119).

Vendor-specific code lives only in named seams: the gNMI block
implementations, templates, the drift field map and the collect-to-diff
adapter. A workflow that imports one of those has pulled vendor knowledge
into the vendor-neutral layer.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import snapl_orchestrator.workflows

pytestmark = pytest.mark.unit

_WORKFLOWS_DIR = Path(snapl_orchestrator.workflows.__file__).parent

_VENDOR_MODULE_PREFIXES = (
    "snapl_orchestrator.adapters",
    "snapl_executor.gnmi",
    "snapl_collector.gnmi",
)


def _imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def _workflow_files() -> list[Path]:
    return sorted(p for p in _WORKFLOWS_DIR.glob("*.py") if p.name != "__init__.py")


def test_workflow_files_are_found() -> None:
    assert _workflow_files(), f"no workflow modules under {_WORKFLOWS_DIR}"


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_workflow_imports_no_vendor_seam(path: Path) -> None:
    leaks = sorted(m for m in _imported_modules(path.read_text()) if m.startswith(_VENDOR_MODULE_PREFIXES))
    assert not leaks, f"{path.name} imports vendor seam module(s) {leaks}; route through an activity instead"
