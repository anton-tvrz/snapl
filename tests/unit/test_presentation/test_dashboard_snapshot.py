"""The snapshot: one file that shows the loop with no server (spec 011 T079, research R14).

A stub template stands in for the built `snapshot.html`: the UI build makes the
real one (PR H2). The app is the fixture server's, with every source up, so the
bundle holds what a live dashboard would answer.
"""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from snapl_presentation import cli
from snapl_presentation.dashboard.snapshot import (
    FORMAT,
    SnapshotError,
    collect_bundle,
    render,
    summary,
    write,
)
from snapl_presentation.exit_codes import ExitCode

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
SERVER = ROOT / "dev" / "scripts" / "serve_dashboard_fixtures.py"
TEMPLATE = (
    "<!doctype html><html><head><title>snapl</title></head><body><div id=root></div>"
    '<script id="snapl-bundle" type="application/json"></script>'
    "<script>/* the app */</script></body></html>"
)
BUNDLE = re.compile(r'<script id="snapl-bundle" type="application/json">(.*?)</script>', re.S)


def _server():
    spec = importlib.util.spec_from_file_location("serve_dashboard_fixtures", SERVER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _app(up: bool = True):
    return _server().build_app(ui_dir=ROOT / "no-built-ui", up=up)


@pytest.fixture(scope="module")
def bundle() -> dict:
    return collect_bundle(_app(), days=7, commit="6baff39", source="live")


def _parsed(html: str) -> dict:
    [payload] = BUNDLE.findall(html)
    return json.loads(payload)


# --- collect_bundle ---------------------------------------------------------------


def test_the_bundle_holds_every_view_the_page_reads(bundle: dict):
    assert set(bundle) == {
        "manifest",
        "overview",
        "devices",
        "runs",
        "topology",
        "timeline",
        "history",
        "steps",
        "links",
        "sources",
        "metrics",
    }
    assert bundle["devices"]["devices"]["data"]
    assert len(bundle["topology"]["topology"]["data"]["links"]) == 8
    assert set(bundle["metrics"]) == {
        "workflow-duration",
        "gnmi-duration",
        "gnmi-errors",
        "worker-lag",
        "worker-up",
        "source-health",
    }


def test_the_manifest_says_what_and_when_and_ends_at_the_apps_clock(bundle: dict):
    manifest = bundle["manifest"]

    assert manifest["format"] == FORMAT == "snapl-snapshot/1"
    assert manifest["created_at"] == manifest["end"] == "2026-11-07T21:20:48.919877Z"
    assert manifest["start"] == "2026-10-31T21:20:48.919877Z"
    assert (manifest["snapl_commit"], manifest["use_case"], manifest["source"]) == ("6baff39", "dcfabric", "live")


def test_the_views_cover_the_range(bundle: dict):
    timeline = bundle["timeline"]["timeline"]["data"]
    assert (timeline["start"], timeline["end"]) == (bundle["manifest"]["start"], bundle["manifest"]["end"])
    assert bundle["history"]["series"][2]["bucket"] == "1d"
    assert bundle["metrics"]["worker-up"]["metrics"]["data"]["step"] >= 2520


def test_every_deploy_run_has_its_steps(bundle: dict):
    deploys = [run for run in bundle["runs"]["runs"]["data"] if run["workflow_type"] == "DeployIntent"]

    assert len(deploys) == 8
    for run in deploys:
        steps = bundle["steps"][f"{run['workflow_id']}@{run['started_at']}"]["steps"]["data"]
        assert steps["recorded"] is True


def test_the_latest_runs_of_any_type_have_their_steps(bundle: dict):
    latest = bundle["runs"]["runs"]["data"][0]

    assert f"{latest['workflow_id']}@{latest['started_at']}" in bundle["steps"]


def test_a_source_that_did_not_answer_is_in_the_bundle_as_unavailable():
    down = collect_bundle(_app(up=False), days=7, commit=None, source="live")

    schedule = down["overview"]["schedule"]
    assert schedule["available"] is False
    assert "temporal is not part of the fixture server" in schedule["reason"]
    assert down["metrics"]["worker-up"]["metrics"]["available"] is False


# --- render ------------------------------------------------------------------------


def test_render_puts_the_bundle_in_its_element(bundle: dict):
    html = render(TEMPLATE, bundle)

    assert _parsed(html) == bundle
    assert html.startswith("<!doctype html>")
    assert "<script>/* the app */</script>" in html


def test_no_recorded_text_can_end_the_script_element(bundle: dict):
    hostile = "</script><script>alert(1)</script><!-- & \u2028 \u2029 >"
    tampered = json.loads(json.dumps(bundle))
    tampered["devices"]["devices"]["data"][0]["error"] = hostile

    html = render(TEMPLATE, tampered)

    assert len(re.findall(r'<script id="snapl-bundle"', html)) == 1
    payload = BUNDLE.findall(html)[0]
    for raw in ("</script", "<!--", "&", "\u2028", "\u2029", ">"):
        assert raw not in payload
    assert _parsed(html)["devices"]["devices"]["data"][0]["error"] == hostile


def test_render_refuses_a_template_without_one_bundle_element(bundle: dict):
    with pytest.raises(SnapshotError, match="no snapl-bundle element"):
        render("<html></html>", bundle)


# --- write and summary --------------------------------------------------------------


def test_write_goes_through_a_partial_file_and_refuses_to_overwrite(tmp_path: Path):
    target = tmp_path / "demo.html"
    renames: list[tuple[str, str]] = []
    real_rename = Path.rename

    def spy(self: Path, other: Path) -> Path:
        renames.append((self.name, Path(other).name))
        return real_rename(self, other)

    with patch.object(Path, "rename", spy):
        write(target, "<html>one</html>")
    assert renames == [("demo.html.partial", "demo.html")]
    assert not (tmp_path / "demo.html.partial").exists()

    with pytest.raises(SnapshotError, match="already exists"):
        write(target, "<html>two</html>")
    write(target, "<html>two</html>", force=True)
    assert target.read_text() == "<html>two</html>"


def test_summary_says_what_was_written(bundle: dict, tmp_path: Path):
    target = tmp_path / "demo.html"
    target.write_text("x" * 412_000)

    lines = summary(target, bundle)

    assert lines[0] == "Snapshot written: demo.html (412 kB)"
    assert lines[1] == "  range: 2026-10-31 21:20 to 2026-11-07 21:20 UTC (7 days)"
    assert lines[2] == "  from: live dashboard sources, commit 6baff39"
    assert lines[3].startswith("  contains: devices, links, drift history, runs and their steps")
    assert lines[4] == (
        "  note: error text and paths recorded from devices are included as recorded. Read it before publishing."
    )
    assert len(lines) == 5


def test_summary_names_each_source_that_did_not_answer(tmp_path: Path):
    down = collect_bundle(_app(up=False), days=1, commit=None, source="live")
    target = tmp_path / "demo.html"
    target.write_text("x")

    lines = summary(target, down)

    assert lines[2] == "  from: live dashboard sources, commit unknown"
    assert lines[-1].startswith("  unavailable when written: temporal (temporal is not part of the fixture server)")
    assert "prometheus (prometheus is not part of the fixture server)" in lines[-1]


# --- the command ----------------------------------------------------------------------

runner = CliRunner()


@pytest.fixture
def template(tmp_path: Path):
    path = tmp_path / "ui" / "snapshot.html"
    path.parent.mkdir()
    path.write_text(TEMPLATE)
    with patch.object(cli, "_snapshot_template", return_value=path):
        yield path


@pytest.fixture
def live_app():
    with patch.object(cli, "_live_app", side_effect=lambda _settings, _use_case: _app()) as patched:
        yield patched


def test_snapshot_writes_the_file_prints_the_summary_and_binds_no_port(template, live_app, tmp_path: Path):
    target = tmp_path / "demo.html"

    with patch.object(cli, "_port_in_use", side_effect=AssertionError("port checked")):
        result = runner.invoke(cli.app, ["dashboard", "--snapshot", str(target), "--days", "3"])

    assert result.exit_code == 0, result.output
    assert "Snapshot written: demo.html" in result.output
    assert "(3 days)" in result.output
    assert _parsed(target.read_text())["manifest"]["start"] == "2026-11-04T21:20:48.919877Z"


def test_snapshot_carries_no_token_from_the_environment(template, live_app, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake_token_value_123")
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "18c0fake-infrahub-token")
    target = tmp_path / "demo.html"

    result = runner.invoke(cli.app, ["dashboard", "--snapshot", str(target)])

    assert result.exit_code == 0, result.output
    text = target.read_text()
    assert "ghp_fake_token_value_123" not in text
    assert "18c0fake-infrahub-token" not in text


def test_snapshot_refuses_to_overwrite_without_force(template, live_app, tmp_path: Path):
    target = tmp_path / "demo.html"
    target.write_text("keep me")

    result = runner.invoke(cli.app, ["dashboard", "--snapshot", str(target)])
    assert result.exit_code == int(ExitCode.ERROR)
    assert "already exists" in result.output
    assert target.read_text() == "keep me"

    assert runner.invoke(cli.app, ["dashboard", "--snapshot", str(target), "--force"]).exit_code == 0


def test_snapshot_needs_the_built_ui(live_app, tmp_path: Path):
    with patch.object(cli, "_snapshot_template", return_value=None):
        result = runner.invoke(cli.app, ["dashboard", "--snapshot", str(tmp_path / "demo.html")])

    assert result.exit_code == int(ExitCode.ERROR)
    assert "error: the designed UI is not built. Build it with: uv run invoke dashboard.build" in result.output
    live_app.assert_not_called()


@pytest.mark.parametrize("days", ["0", "91"])
def test_snapshot_days_are_1_to_90(template, live_app, tmp_path: Path, days: str):
    result = runner.invoke(cli.app, ["dashboard", "--snapshot", str(tmp_path / "x.html"), "--days", days])

    assert result.exit_code != 0


def test_snapshot_of_an_archive_says_so(template, tmp_path: Path):
    from tests.unit.test_presentation.test_dashboard_archive import build_archive

    archive = build_archive(tmp_path)
    target = tmp_path / "demo.html"

    result = runner.invoke(cli.app, ["dashboard", "--archive", str(archive), "--snapshot", str(target)])

    assert result.exit_code == 0, result.output
    assert f"  from: archive {archive.name}, commit 6baff39" in result.output
    manifest = _parsed(target.read_text())["manifest"]
    assert (manifest["source"], manifest["end"]) == ("archive", "2026-11-07T21:20:48Z")
    assert manifest["start"] == (
        (__import__("datetime").datetime.fromisoformat("2026-11-07T21:20:48+00:00") - timedelta(days=7))
        .isoformat()
        .replace("+00:00", "Z")
    )
