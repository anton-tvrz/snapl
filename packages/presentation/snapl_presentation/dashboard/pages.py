"""The pages and files the dashboard serves (spec 011 research R2 to R4).

Two pages can sit at the root. The designed UI is built from
``packages/presentation/ui`` into ``static/ui`` and is not committed, so a git
checkout without Node does not have it. Then the root serves 010's plain page,
with a notice naming the command that builds the UI. The plain page itself is
always at ``/plain``.

Built files are served by an explicit route, not a static mount, so the
read-only route test still sees every route and method (research R3). Only
files inside ``static/ui/assets`` are served.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

BUILD_COMMAND = "uv run invoke dashboard.build"
NOT_BUILT = f"The designed UI is not built: serving the plain page. Build it with: {BUILD_COMMAND}"

# Where the UI build writes, inside the installed package.
BUILT_UI = Path(str(resources.files("snapl_presentation.dashboard").joinpath("static/ui")))

# No request leaves this origin (FR-005). `style-src-attr` allows style
# attributes, which React and the chart library use to position elements. It
# allows no script and no request.
_POLICY = {
    "default-src": "'self'",
    "script-src": "'self'",
    "style-src": "'self'",
    "style-src-attr": "'unsafe-inline'",
    "img-src": "'self' data:",
    "font-src": "'self'",
    "connect-src": "'self'",
    "frame-ancestors": "'none'",
    "base-uri": "'none'",
    "form-action": "'none'",
}
# The plain page is one file with its script and style inline. It may run
# them, and `connect-src 'self'` still keeps it from reaching anywhere else.
_PLAIN_POLICY = {key: value for key, value in _POLICY.items() if key != "style-src-attr"} | {
    "script-src": "'unsafe-inline'",
    "style-src": "'unsafe-inline'",
}


def _header(policy: dict[str, str]) -> str:
    return "; ".join(f"{directive} {value}" for directive, value in policy.items())


UI_POLICY = _header(_POLICY)
PLAIN_POLICY = _header(_PLAIN_POLICY)

IMMUTABLE = "public, max-age=31536000, immutable"


def find_ui(ui_dir: Path | None) -> Path | None:
    """The built UI's directory, or None when there is no built UI in it."""
    if ui_dir is not None and (ui_dir / "index.html").is_file():
        return ui_dir
    return None


def plain_page() -> str:
    """010's plain page, as shipped."""
    return resources.files("snapl_presentation.dashboard").joinpath("static/index.html").read_text(encoding="utf-8")


def fallback_page() -> str:
    """The plain page for the root, saying the designed UI is not built."""
    notice = f'<p class="problem stale">{NOT_BUILT}</p>'
    return plain_page().replace("<body>", f"<body>\n{notice}", 1)


def asset_path(ui_dir: Path, name: str) -> Path | None:
    """The built file ``name`` inside ``ui_dir/assets``, or None.

    The path is resolved, symlinks included, and must stay inside the assets
    directory: nothing else on disk is ever served.
    """
    assets = (ui_dir / "assets").resolve()
    candidate = (assets / name).resolve()
    if candidate.is_file() and candidate.is_relative_to(assets) and candidate != assets:
        return candidate
    return None
