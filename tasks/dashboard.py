"""Dashboard UI tasks: build, test and run the designed UI (spec 011).

Node is needed to build the UI and never to run snapl: the built files ship
inside the wheel (ADR-0005 Decision 2). These tasks are the only place Node is
called from, always in the UI's own directory, and always from its lockfile.
"""

from __future__ import annotations

import re

from invoke import Exit, task

from .shared import PROJECT_ROOT

UI_DIR = PROJECT_ROOT / "packages" / "presentation" / "ui"
# Vite 8's floor on the 22 line (spec 011 research R1).
MIN_NODE = (22, 12)
_INSTALL = "npm ci --no-audit --no-fund"
_NO_NODE = "Node 22.12 or later is needed to build the UI (only to build it). Running snapl does not need Node."


def _require_node(ctx) -> None:
    result = ctx.run("node --version", hide=True, warn=True)
    found = re.match(r"v(\d+)\.(\d+)\.", (result.stdout or "").strip()) if result.ok else None
    if found is None or (int(found.group(1)), int(found.group(2))) < MIN_NODE:
        have = (result.stdout or "").strip() or "none found"
        raise Exit(f"{_NO_NODE} Found: {have}.", code=1)


def _npm(ctx, *commands: str, install: bool = True) -> None:
    _require_node(ctx)
    with ctx.cd(str(UI_DIR)):
        for command in ((_INSTALL,) if install else ()) + commands:
            ctx.run(command, pty=False)


_INSTALL_HELP = {"install": "Install dependencies from the lockfile first (default). --no-install skips it."}


@task(help=_INSTALL_HELP)
def build(ctx, install=True):
    """Build the designed UI into snapl_presentation/dashboard/static/ui."""
    _npm(ctx, "npm run build", install=install)


# Named `unit_checks` so that neither pytest nor its lint rules take it for a test.
@task(name="test", help=_INSTALL_HELP)
def unit_checks(ctx, install=True):
    """Type-check, lint, check the generated types, and run the UI's unit tests with coverage."""
    _npm(
        ctx,
        "npm run typecheck",
        "npm run lint",
        "npm run types:check",
        "npm run test:coverage",
        install=install,
    )


@task(help=_INSTALL_HELP)
def types(ctx, install=True):
    """Regenerate the UI's contract types from the OpenAPI snapshot."""
    _npm(ctx, "npm run types", install=install)


@task(help=_INSTALL_HELP)
def dev(ctx, install=True):
    """Run the UI's dev server, proxying /api to a running `snapl dashboard`."""
    _npm(ctx, "npm run dev", install=install)


@task(help=_INSTALL_HELP)
def e2e(ctx, install=True):
    """Build the UI, then check it in a headless browser against fixtures."""
    _npm(ctx, "npm run build", "npx playwright install chromium", "npm run e2e", install=install)
