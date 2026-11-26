"""The `invoke dashboard.*` tasks (spec 011 T022, contracts/cli-and-build.md).

Node builds the designed UI and is never needed to run snapl. These tasks are
the only place it is called from, always inside the UI's own directory.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from invoke import Context, Exit

from tasks import dashboard, ns
from tasks.shared import PROJECT_ROOT

pytestmark = pytest.mark.unit

UI = PROJECT_ROOT / "packages" / "presentation" / "ui"


def _ctx(node: str | None = "v22.12.0") -> MagicMock:
    """A context whose `node --version` answers ``node`` (None: node is missing)."""
    ctx = MagicMock(spec=Context)

    def run(command: str, **kwargs):
        result = MagicMock()
        if command == "node --version":
            result.ok = node is not None
            result.stdout = f"{node}\n" if node else ""
        return result

    ctx.run.side_effect = run
    ctx.cd.return_value.__enter__.return_value = None
    return ctx


def _commands(ctx: MagicMock) -> list[str]:
    return [call.args[0] for call in ctx.run.call_args_list if call.args[0] != "node --version"]


def test_the_tasks_are_registered_under_dashboard():
    names = set(ns.collections["dashboard"].task_names)

    assert names == {"build", "dev", "test", "types", "e2e"}


def test_ui_directory_is_the_one_inside_the_presentation_package():
    assert dashboard.UI_DIR == UI
    assert (UI / "package.json").is_file()


def test_build_installs_from_the_lockfile_then_builds_in_the_ui_directory():
    ctx = _ctx()

    dashboard.build(ctx)

    assert _commands(ctx) == ["npm ci --no-audit --no-fund", "npm run build"]
    ctx.cd.assert_called_with(str(UI))


@pytest.mark.parametrize("node", [None, "v20.19.0", "v22.11.0", "garbage"])
def test_build_refuses_without_a_new_enough_node_and_says_it_is_only_to_build(node):
    ctx = _ctx(node)

    with pytest.raises(Exit) as raised:
        dashboard.build(ctx)

    assert raised.value.code == 1
    assert "Node 22.12 or later is needed to build the UI (only to build it)" in str(raised.value.message)
    assert _commands(ctx) == []


@pytest.mark.parametrize("node", ["v22.12.0", "v22.20.1", "v24.0.0", "v26.8.1"])
def test_build_accepts_node_22_12_and_later(node):
    ctx = _ctx(node)

    dashboard.build(ctx)

    assert _commands(ctx)[-1] == "npm run build"


def test_test_checks_types_lint_generated_types_and_unit_tests_with_coverage():
    ctx = _ctx()

    dashboard.unit_checks(ctx)

    assert _commands(ctx) == [
        "npm ci --no-audit --no-fund",
        "npm run typecheck",
        "npm run lint",
        "npm run types:check",
        "npm run test:coverage",
    ]


def test_types_regenerates_the_contract_types():
    ctx = _ctx()

    dashboard.types(ctx)

    assert _commands(ctx) == ["npm ci --no-audit --no-fund", "npm run types"]


def test_dev_starts_the_dev_server():
    ctx = _ctx()

    dashboard.dev(ctx)

    assert _commands(ctx) == ["npm ci --no-audit --no-fund", "npm run dev"]


def test_e2e_builds_then_runs_the_browser_check():
    ctx = _ctx()

    dashboard.e2e(ctx)

    assert _commands(ctx) == [
        "npm ci --no-audit --no-fund",
        "npm run build",
        "npx playwright install chromium",
        "npm run e2e",
    ]


def test_install_can_be_skipped_when_dependencies_are_already_there():
    ctx = _ctx()

    dashboard.build(ctx, install=False)

    assert _commands(ctx) == ["npm run build"]


def test_check_all_never_needs_node():
    """`invoke check-all` stays Python-only (contracts/cli-and-build.md)."""
    from tasks import main

    before = {task.name for task in main.check_all.pre}

    assert before
    assert before.isdisjoint({"build", "test", "types", "dev", "e2e"})
