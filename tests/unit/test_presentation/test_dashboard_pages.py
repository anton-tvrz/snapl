"""Serving the designed UI, the plain page and built files (spec 011 T020).

The UI directory is injected, so nothing here needs a build. With a UI the root
serves it; without one the root serves 010's plain page and says how to build
the UI. Either way the plain page stays at ``/plain``, every response carries a
Content-Security-Policy, and nothing answers a method other than GET.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — fixture annotations
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from snapl_presentation.dashboard import pages
from snapl_presentation.dashboard.app import create_app
from snapl_presentation.dashboard.models import SourceName, View
from snapl_presentation.dashboard.sources import Sources
from snapl_presentation.settings import CliSettings

pytestmark = pytest.mark.unit

UI_INDEX = '<!doctype html><html><head><script type="module" src="/assets/app-abc123.js"></script></head></html>'
OUTSIDE = "a file outside the assets directory"


class _Down:
    names: ClassVar[dict] = {}

    def __init__(self, source: SourceName) -> None:
        self._source = source

    async def refresh(self) -> View[Any]:
        return View(source=self._source, available=False, reason="not asked", remedy="-")


def _client(ui_dir: Path | None) -> TestClient:
    sources = Sources(
        audit=_Down(SourceName.AUDIT),
        sot=_Down(SourceName.SOT),
        temporal=_Down(SourceName.TEMPORAL),
        ci=_Down(SourceName.CI),
        lab_runs=_Down(SourceName.LAB_RUNS),
        prometheus=_Down(SourceName.PROMETHEUS),
    )
    return TestClient(create_app(sources, CliSettings(), "dcfabric", ui_dir=ui_dir))


@pytest.fixture
def ui(tmp_path: Path) -> Path:
    """A stub of the built UI, and a file beside it that must never be served."""
    built = tmp_path / "ui"
    (built / "assets").mkdir(parents=True)
    (built / "index.html").write_text(UI_INDEX)
    (built / "assets" / "app-abc123.js").write_text("console.log('ui')")
    (built / "assets" / "app-abc123.css").write_text("body{}")
    (built / "snapshot.html").write_text("<!doctype html>snapshot template")
    (tmp_path / "outside.txt").write_text(OUTSIDE)
    return built


# --- with the UI built -------------------------------------------------------------


def test_root_serves_the_built_ui(ui: Path):
    response = _client(ui).get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == UI_INDEX
    assert response.headers["cache-control"] == "no-cache"


def test_plain_still_serves_the_plain_page_when_the_ui_is_built(ui: Path):
    response = _client(ui).get("/plain")

    assert response.status_code == 200
    assert "<h1>snapl dashboard</h1>" in response.text
    assert "/api/v1/overview" in response.text
    assert pages.BUILD_COMMAND not in response.text


def test_a_built_file_is_served_to_be_cached_for_good(ui: Path):
    client = _client(ui)

    script = client.get("/assets/app-abc123.js")
    style = client.get("/assets/app-abc123.css")

    assert (script.status_code, script.text) == (200, "console.log('ui')")
    assert "javascript" in script.headers["content-type"]
    assert style.headers["content-type"].startswith("text/css")
    assert script.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize(
    "path",
    [
        "/assets/missing.js",
        "/assets/../index.html",
        "/assets/%2e%2e/index.html",
        "/assets/..%2f..%2foutside.txt",
        "/assets/../../outside.txt",
        "/assets//etc/passwd",
        "/assets/",
        "/snapshot.html",
        "/index.html",
    ],
)
def test_nothing_outside_the_assets_directory_is_served(ui: Path, path: str):
    response = _client(ui).get(path)

    assert response.status_code == 404
    assert OUTSIDE not in response.text
    assert "snapshot template" not in response.text


def test_asset_path_refuses_what_resolves_outside_the_assets_directory(ui: Path):
    assert pages.asset_path(ui, "app-abc123.js") == (ui / "assets" / "app-abc123.js").resolve()
    for escaping in ("../index.html", "../../outside.txt", "/etc/passwd", "", ".", "nested/../../index.html"):
        assert pages.asset_path(ui, escaping) is None, escaping


def test_asset_path_refuses_a_symlink_that_leaves_the_assets_directory(ui: Path, tmp_path: Path):
    (ui / "assets" / "link.txt").symlink_to(tmp_path / "outside.txt")

    assert pages.asset_path(ui, "link.txt") is None
    assert _client(ui).get("/assets/link.txt").status_code == 404


# --- without the UI built ----------------------------------------------------------


@pytest.mark.parametrize("missing", ["none", "no-directory", "empty-directory"])
def test_root_falls_back_to_the_plain_page_and_says_how_to_build_the_ui(tmp_path: Path, missing: str):
    ui_dir = {"none": None, "no-directory": tmp_path / "absent", "empty-directory": tmp_path}[missing]

    response = _client(ui_dir).get("/")

    assert response.status_code == 200
    assert "<h1>snapl dashboard</h1>" in response.text
    assert "The designed UI is not built" in response.text
    assert pages.BUILD_COMMAND == "uv run invoke dashboard.build"
    assert pages.BUILD_COMMAND in response.text
    # The page still works: the notice is added, nothing is replaced.
    assert "/api/v1/overview" in response.text


def test_without_the_ui_there_are_no_assets(tmp_path: Path):
    assert _client(None).get("/assets/app-abc123.js").status_code == 404
    assert _client(tmp_path / "absent").get("/assets/app-abc123.js").status_code == 404


def test_find_ui_needs_an_index_page(ui: Path, tmp_path: Path):
    assert pages.find_ui(ui) == ui
    assert pages.find_ui(tmp_path) is None
    assert pages.find_ui(None) is None


def test_the_packaged_ui_location_is_inside_the_dashboard_package():
    assert pages.BUILT_UI.parts[-4:] == ("snapl_presentation", "dashboard", "static", "ui")


# --- read-only ---------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/plain", "/assets/app-abc123.js"])
@pytest.mark.parametrize("method", ["HEAD", "POST", "PUT", "PATCH", "DELETE"])
def test_pages_and_files_answer_get_only(ui: Path, method: str, path: str):
    assert _client(ui).request(method, path).status_code == 405


# --- Content-Security-Policy (research R4) -------------------------------------------


def _policy(response) -> dict[str, str]:
    directives = [d.strip().split(" ", 1) for d in response.headers["content-security-policy"].split(";")]
    return {d[0]: d[1] if len(d) > 1 else "" for d in directives}


def test_the_ui_its_files_and_the_api_carry_the_strict_policy(ui: Path):
    client = _client(ui)

    for path in ("/", "/assets/app-abc123.js", "/api/v1/mode", "/api/v1/openapi.json", "/metrics", "/no-such-route"):
        policy = _policy(client.get(path))
        assert policy == {
            "default-src": "'self'",
            "script-src": "'self'",
            "style-src": "'self'",
            # React and the chart library position elements with style attributes.
            "style-src-attr": "'unsafe-inline'",
            "img-src": "'self' data:",
            "font-src": "'self'",
            "connect-src": "'self'",
            "frame-ancestors": "'none'",
            "base-uri": "'none'",
            "form-action": "'none'",
        }, path


def test_the_plain_page_may_run_its_inline_script_but_reach_nowhere_else(ui: Path, tmp_path: Path):
    for client, path in ((_client(ui), "/plain"), (_client(None), "/"), (_client(None), "/plain")):
        policy = _policy(client.get(path))
        assert policy["script-src"] == "'unsafe-inline'"
        assert policy["style-src"] == "'unsafe-inline'"
        assert policy["default-src"] == policy["connect-src"] == "'self'"
        assert "style-src-attr" not in policy


def test_no_policy_names_another_origin(ui: Path):
    for client, path in ((_client(ui), "/"), (_client(ui), "/plain")):
        header = client.get(path).headers["content-security-policy"]
        assert "http" not in header
        assert "*" not in header
