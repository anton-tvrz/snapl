"""Unit tests for the dev environment tasks (dev.deps / dev.stop / dev.down)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from invoke import Context

from tasks import dev, ns

pytestmark = pytest.mark.unit


def _ctx() -> MagicMock:
    return MagicMock(spec=Context)


def _run_commands(ctx: MagicMock) -> list[str]:
    return [call.args[0] for call in ctx.run.call_args_list]


def test_deps_starts_stack_and_waits_for_health() -> None:
    ctx = _ctx()
    dev.deps(ctx)
    (command,) = _run_commands(ctx)
    assert "development/docker-compose.yml" in command
    assert "up -d --wait" in command


def test_stop_stops_containers_without_removing() -> None:
    ctx = _ctx()
    dev.stop(ctx)
    (command,) = _run_commands(ctx)
    assert "development/docker-compose.yml" in command
    assert command.endswith("stop")


def test_down_removes_containers_keeping_volumes() -> None:
    ctx = _ctx()
    dev.down(ctx)
    (command,) = _run_commands(ctx)
    assert "development/docker-compose.yml" in command
    assert command.endswith("down")
    assert "--volumes" not in command


def test_down_with_volumes_flag_removes_data() -> None:
    ctx = _ctx()
    dev.down(ctx, volumes=True)
    (command,) = _run_commands(ctx)
    assert command.endswith("down --volumes")


def test_dev_collection_is_registered() -> None:
    collection = ns.collections["dev"]
    assert set(collection.tasks) == {"deps", "stop", "down"}
