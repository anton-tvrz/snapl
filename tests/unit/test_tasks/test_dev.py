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


def test_lab_deploy_runs_containerlab_against_dcfabric() -> None:
    ctx = _ctx()
    dev.lab_deploy(ctx)
    commands = _run_commands(ctx)
    assert "containerlab deploy" in commands[0]
    assert "containerlab/dcfabric.yml" in commands[0]


def test_lab_deploy_strips_gnmi_tls_on_every_node() -> None:
    """Containerlab does not reliably apply the partial startup config, so the
    deploy task enforces plaintext gNMI itself, on all six nodes."""
    ctx = _ctx()
    dev.lab_deploy(ctx)
    commands = _run_commands(ctx)
    tls_strip = commands[1]
    assert "delete / system grpc-server mgmt tls-profile" in tls_strip
    for node in ("spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04"):
        assert node in tls_strip


def test_lab_destroy_runs_containerlab_against_dcfabric() -> None:
    ctx = _ctx()
    dev.lab_destroy(ctx)
    (command,) = _run_commands(ctx)
    assert "containerlab destroy" in command
    assert "containerlab/dcfabric.yml" in command


def test_lab_tasks_use_dockerized_containerlab() -> None:
    """No native containerlab binary is assumed — the wrapper runs ghcr.io/srl-labs/clab."""
    ctx = _ctx()
    dev.lab_deploy(ctx)
    command = _run_commands(ctx)[0]
    assert command.startswith("docker run")
    assert "ghcr.io/srl-labs/clab" in command
    assert "/var/run/docker.sock" in command


def test_dev_collection_is_registered() -> None:
    collection = ns.collections["dev"]
    assert set(collection.tasks) == {"deps", "stop", "down", "lab-deploy", "lab-destroy"}
