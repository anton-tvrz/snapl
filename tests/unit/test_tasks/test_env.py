"""Unit tests for task env loading.

`docs/demo-scenarios.md` tells an operator to `cp development/.env.example
development/.env` and promises that "the worker and the demo tasks both read
them". Nothing made that true: docker compose picks the file up automatically
because it sits beside the compose file, but the invoke process never did, so
`demo.seed` died on an unset INFRAHUB_API_TOKEN and `orchestrator.start` came
up without a token or a device password. The documented setup path could not
work on a clean checkout.

These tests pin the loader that closes that gap, and the two things it must
not get wrong: an operator's explicit export outranks the file, and an inline
comment is not part of the value.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from tasks.shared import ENV_FILE, load_env_file

pytestmark = pytest.mark.unit


def test_env_file_points_at_the_file_the_docs_name() -> None:
    """The doc says `development/.env`; the loader must read that exact path."""
    assert ENV_FILE.parent.name == "development"
    assert ENV_FILE.name == ".env"


def test_loads_values_into_os_environ(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INFRAHUB_API_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("INFRAHUB_API_TOKEN=tok-from-file\n")  # pragma: allowlist secret

    applied = load_env_file(env_file)

    assert os.environ["INFRAHUB_API_TOKEN"] == "tok-from-file"  # noqa: S105 — test fixture, not a credential
    assert applied == {"INFRAHUB_API_TOKEN": "tok-from-file"}


def test_shell_environment_wins_over_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit export is a deliberate override — the file must not clobber it.

    This is what lets an operator point at an offset port or a different SoT
    for one command without editing the file.
    """
    monkeypatch.setenv("INFRAHUB_ADDRESS", "http://localhost:19999")
    env_file = tmp_path / ".env"
    env_file.write_text("INFRAHUB_ADDRESS=http://localhost:18000\n")

    applied = load_env_file(env_file)

    assert os.environ["INFRAHUB_ADDRESS"] == "http://localhost:19999"
    assert "INFRAHUB_ADDRESS" not in applied


def test_inline_comment_is_not_part_of_the_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env.example` carries `NokiaSrl1!  # pragma: allowlist secret`.

    A naive `line.split("=")` parser sends the pragma to the device as part of
    the gNMI password and every deploy fails authentication.
    """
    monkeypatch.delenv("SRLINUX_PASSWORD", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("SRLINUX_PASSWORD=NokiaSrl1!  # pragma: allowlist secret\n")

    load_env_file(env_file)

    assert os.environ["SRLINUX_PASSWORD"] == "NokiaSrl1!"  # noqa: S105 # pragma: allowlist secret


def test_missing_file_is_a_noop(tmp_path: Path) -> None:
    """A checkout with no `.env` is valid — the committed defaults work (#109)."""
    assert load_env_file(tmp_path / "does-not-exist") == {}


def test_committed_example_parses_with_the_values_the_stack_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards the example against drift from the compose stack it configures."""
    for key in ("INFRAHUB_ADDRESS", "INFRAHUB_API_TOKEN", "TEMPORAL_HOST", "SRLINUX_PASSWORD"):
        monkeypatch.delenv(key, raising=False)

    applied = load_env_file(ENV_FILE.with_name(".env.example"))

    assert applied["INFRAHUB_ADDRESS"] == "http://localhost:18000"
    assert applied["TEMPORAL_HOST"] == "localhost:18033"
    assert applied["SRLINUX_PASSWORD"] == "NokiaSrl1!"  # noqa: S105 # pragma: allowlist secret
    assert applied["INFRAHUB_API_TOKEN"]
