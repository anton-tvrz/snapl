"""Unit tests for the dev dependency stack's compose file (Issue #81).

Temporal's workflow history *is* the demo's durability receipt: the Web UI is
what shows that a deploy survived a worker restart, and the audit story points
at it. `temporal server start-dev` keeps that history in memory, so before #81
any container restart silently emptied it — found the hard way after a host
reboot, when the UI showed zero workflows despite a validated closed-loop run
five days earlier.

The failure mode is what makes this worth a test: nothing errors, the stack
comes up healthy, and the loss is only visible as an absence. A regression
here would be found the same way it was found the first time — mid-demo.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

COMPOSE_PATH = Path(__file__).resolve().parents[3] / "development" / "docker-compose.yml"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text())


@pytest.fixture(scope="module")
def temporal(compose: dict) -> dict:
    service = compose["services"].get("temporal")
    assert service, "the stack has no temporal service"
    return service


def test_temporal_persists_history_to_a_file(temporal: dict) -> None:
    """`start-dev` without --db-filename is in-memory — the #81 defect."""
    command = temporal["command"]
    if isinstance(command, list):
        command = " ".join(command)

    assert "--db-filename" in command, (
        "temporal runs start-dev without --db-filename, so workflow history is "
        "held in memory and lost on every container restart (#81)"
    )


def test_the_history_file_lives_on_a_named_volume(compose: dict, temporal: dict) -> None:
    """A db file inside the container's writable layer is no better than memory.

    `docker compose down` (which `demo.reset` runs) removes the container, so
    the history has to sit on a volume that outlives it.
    """
    command = temporal["command"]
    if isinstance(command, list):
        command = " ".join(command)
    db_path = Path(command.split("--db-filename", 1)[1].split()[0])

    mounts = {}
    for mount in temporal.get("volumes", []):
        source, _, target = mount.partition(":")
        mounts[Path(target.split(":")[0])] = source

    covering = [target for target in mounts if target == db_path.parent or target in db_path.parents]
    assert covering, f"{db_path} is not under any mount of the temporal service: {sorted(map(str, mounts))}"

    volume_name = mounts[covering[0]]
    assert not volume_name.startswith((".", "/")), (
        f"{volume_name!r} is a bind mount — use a named volume, as the rest of the stack does"
    )
    assert volume_name in (compose.get("volumes") or {}), (
        f"{volume_name!r} is mounted but never declared in the top-level volumes block"
    )


def test_the_comment_does_not_still_promise_in_memory_persistence() -> None:
    """The compose file documented the old behaviour in a comment, and that
    comment is where a reader checks before believing history survives."""
    text = COMPOSE_PATH.read_text()
    # Comment prose wraps, so normalise whitespace before matching — the claim
    # was split as "in-memory\n  # persistence".
    header = " ".join(text[: text.index("  temporal:")].split())
    assert "workflow history is lost" not in header, (
        "the temporal service comment still tells the reader history is lost on restart, which is the thing #81 fixed"
    )
