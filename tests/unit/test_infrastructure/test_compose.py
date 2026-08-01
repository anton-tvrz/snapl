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


# ---------------------------------------------------------------------------
# Isolation from sibling projects (#115)
#
# snapl shares this machine with projects running near-identical stacks. Ports
# (#107) and lab subnets (#90) are separated; these pin the two dimensions that
# are not a networking problem — the compose project namespace, and the
# credential that decides whether a misaimed write is refused or accepted.
# ---------------------------------------------------------------------------

# The Infrahub admin token that shipped as snapl's default until #115, and is
# also project-network-synapse-quattro's default. Identical tokens mean an
# accidentally misaimed snapl authenticates against a neighbour's Source of
# Truth as admin, instead of being refused.
SHARED_NEIGHBOUR_TOKEN = "06438eb2-8019-4776-878c-0941b1f1d1ec"  # noqa: S105 # pragma: allowlist secret


def test_compose_declares_its_own_project_name(compose: dict) -> None:
    """Without `name:`, compose falls back to the project directory — and both
    snapl and the neighbour keep their compose file in `development/`, so the
    stacks would share one `development_*` volume and container namespace.
    Volumes named `temporal_data` exist in both projects, so the collision is
    exact rather than theoretical.
    """
    assert compose.get("name") == "snapl", (
        "docker-compose.yml must declare `name: snapl` — without it the project "
        "name defaults to the 'development' directory, which the neighbour shares"
    )


def test_the_admin_token_is_not_the_one_the_neighbour_ships(compose: dict) -> None:
    token = compose["services"]["server"]["environment"].get("INFRAHUB_INITIAL_ADMIN_TOKEN") or ""
    assert str(token) != SHARED_NEIGHBOUR_TOKEN, (
        "snapl's Infrahub admin token is the neighbouring project's default too — "
        "a misaimed write authenticates instead of being refused (#115)"
    )


def test_the_example_env_token_matches_the_stack(compose: dict) -> None:
    """A mismatch is a 401 on every task, so these two must move together."""
    # dotenv_values rather than tasks.load_env_file: the latter reports only
    # what it *applied*, and importing tasks already put these in os.environ.
    from dotenv import dotenv_values

    example = dotenv_values(COMPOSE_PATH.with_name(".env.example"))
    assert example["INFRAHUB_API_TOKEN"] == compose["services"]["server"]["environment"]["INFRAHUB_INITIAL_ADMIN_TOKEN"]
