"""How the designed UI is packaged and checked (spec 011 T021, research R2, R16).

The UI's built files are not committed. They must still ship in the wheel, stay
out of git, and be checked by CI when the UI or the contract changes. Each of
those is one line in a config file that is easy to lose, so each is pinned.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
BUILT = "packages/presentation/snapl_presentation/dashboard/static/ui"


def _ignored(path: str) -> bool:
    # Bandit B603/B607: a fixed git command, no shell.
    result = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, check=False)
    return result.returncode == 0


def test_the_wheel_and_the_sdist_ship_the_built_ui():
    build = tomllib.loads((ROOT / "packages/presentation/pyproject.toml").read_text())["tool"]["hatch"]["build"]

    for target in ("wheel", "sdist"):
        # Hatchling leaves git-ignored files out unless they are listed as artifacts.
        assert "snapl_presentation/dashboard/static/ui/**" in build["targets"][target]["artifacts"], target


def test_the_wheel_does_not_ship_the_ui_source_or_node_modules():
    build = tomllib.loads((ROOT / "packages/presentation/pyproject.toml").read_text())["tool"]["hatch"]["build"]

    assert build["targets"]["wheel"]["packages"] == ["snapl_presentation"]
    assert "ui" in build["targets"]["sdist"]["exclude"]


def test_built_files_and_installed_dependencies_are_not_committed():
    assert _ignored(f"{BUILT}/index.html")
    assert _ignored(f"{BUILT}/assets/app.js")
    assert _ignored("packages/presentation/ui/node_modules/react/index.js")
    assert _ignored("packages/presentation/ui/coverage/index.html")


def test_the_plain_page_and_the_ui_source_are_committed():
    assert not _ignored("packages/presentation/snapl_presentation/dashboard/static/index.html")
    assert not _ignored("packages/presentation/ui/src/App.tsx")
    assert not _ignored("packages/presentation/ui/package-lock.json")


def test_the_lockfile_and_generated_types_are_committed_files():
    assert (ROOT / "packages/presentation/ui/package-lock.json").is_file()
    assert (ROOT / "packages/presentation/ui/src/api/schema.d.ts").is_file()
    assert (ROOT / "packages/presentation/ui/.nvmrc").read_text().strip() == "22"


def test_every_ui_dependency_is_pinned_exactly():
    import json

    package = json.loads((ROOT / "packages/presentation/ui/package.json").read_text())

    for group in ("dependencies", "devDependencies"):
        for name, version in package[group].items():
            assert version[0].isdigit(), f"{name} is not pinned exactly: {version}"


def test_the_secrets_hook_skips_the_lockfiles_integrity_hashes():
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    hooks = {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}

    assert "package-lock" in hooks["detect-secrets"]["exclude"]
    assert "uv\\.lock" in hooks["detect-secrets"]["exclude"]


def test_the_ui_job_runs_when_the_ui_or_the_contract_changes():
    filters = yaml.safe_load((ROOT / ".github/file-filters.yml").read_text())

    assert filters["ui"] == [
        "packages/presentation/ui/**",
        "packages/presentation/snapl_presentation/dashboard/**",
        "tests/unit/test_presentation/fixtures/openapi_v1.json",
        "dev/scripts/serve_dashboard_fixtures.py",
        ".github/workflows/quality.yml",
    ]


def test_the_quality_gate_counts_the_ui_job():
    workflow = yaml.safe_load((ROOT / ".github/workflows/quality.yml").read_text())
    jobs = workflow["jobs"]

    assert "ui" in jobs
    assert jobs["ui"]["needs"] == ["changes"]
    assert "needs.changes.outputs.ui == 'true'" in jobs["ui"]["if"]
    assert "ui" in jobs["quality-gate"]["needs"]
    assert "ui" in jobs["changes"]["outputs"]
    gate = jobs["quality-gate"]["steps"][0]["run"]
    assert "needs.ui.result" in gate

    steps = " ".join(step.get("run", "") for step in jobs["ui"]["steps"])
    for command in ("npm ci", "npm run typecheck", "npm run lint", "npm run types:check", "npm run test:coverage"):
        assert command in steps, command
    assert "npm run build" in steps
    assert "npm run e2e" in steps


def test_the_release_build_builds_the_ui_before_the_presentation_wheel():
    workflow = yaml.safe_load((ROOT / ".github/workflows/build-artifacts.yml").read_text())
    steps = workflow["jobs"]["build-packages"]["steps"]
    runs = [step.get("run", "") for step in steps]

    ui = next(i for i, run in enumerate(runs) if "npm run build" in run)
    wheel = next(i for i, run in enumerate(runs) if "uv build --package snapl-presentation" in run)
    check = next(i for i, run in enumerate(runs) if "static/ui/index.html" in run)
    assert ui < wheel < check
    assert any("setup-node" in step.get("uses", "") for step in steps)
