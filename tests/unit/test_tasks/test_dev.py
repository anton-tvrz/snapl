"""Unit tests for the dev environment tasks (dev.deps / dev.stop / dev.down)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from invoke import Context

from tasks import dev, ns

pytestmark = pytest.mark.unit

# Read before the autouse fixture below points it at a temp dir.
REAL_PROMETHEUS_DATA = getattr(dev, "PROMETHEUS_DATA", None)


def _ctx() -> MagicMock:
    return MagicMock(spec=Context)


def _run_commands(ctx: MagicMock) -> list[str]:
    return [call.args[0] for call in ctx.run.call_args_list]


@pytest.fixture(autouse=True)
def prometheus_data(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """dev.deps creates the metrics store's directory; never the real checkout's."""
    path = tmp_path / ".snapl" / "prometheus"
    monkeypatch.setattr(dev, "PROMETHEUS_DATA", path)
    return path


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


# --- the metrics store's data directory (spec 012 T024, research R6) ----------


def test_deps_creates_the_prometheus_data_dir_before_compose_up(prometheus_data) -> None:
    """The container runs as `nobody`; a directory Docker creates on demand is root's."""
    seen: list[bool] = []
    ctx = _ctx()
    ctx.run.side_effect = lambda *_a, **_k: seen.append(prometheus_data.is_dir())

    dev.deps(ctx)

    assert seen == [True]
    assert prometheus_data.stat().st_mode & 0o777 == 0o777


def test_deps_keeps_an_existing_prometheus_data_dir(prometheus_data) -> None:
    prometheus_data.mkdir(parents=True)
    (prometheus_data / "wal").mkdir()

    dev.deps(_ctx())

    assert (prometheus_data / "wal").is_dir()
    assert prometheus_data.stat().st_mode & 0o777 == 0o777


def test_the_prometheus_data_dir_is_the_one_compose_mounts() -> None:
    import yaml

    from tasks.shared import PROJECT_ROOT

    compose_dir = PROJECT_ROOT / "development"
    service = yaml.safe_load((compose_dir / "docker-compose.yml").read_text())["services"]["prometheus"]
    [source] = [m.split(":")[0] for m in service["volumes"] if m.split(":")[1] == "/prometheus"]
    assert (compose_dir / source).resolve() == REAL_PROMETHEUS_DATA.resolve()


def test_down_with_volumes_never_names_snapl() -> None:
    """FR-009: a reset must not delete metrics. Only named volumes go."""
    ctx = _ctx()

    dev.down(ctx, volumes=True)

    assert not any(".snapl" in command for command in _run_commands(ctx))
