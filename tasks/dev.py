"""Dev environment tasks — manage the docker compose dependency stack."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING

from invoke import task

from .shared import PROJECT_ROOT, execute_command

if TYPE_CHECKING:
    from pathlib import Path

COMPOSE = f"docker compose -f {PROJECT_ROOT / 'development' / 'docker-compose.yml'}"

# Containerlab runs Docker-outside-of-Docker (no native binary needed):
# the clab container drives the host Docker daemon through the mounted socket.
CLAB_IMAGE = "ghcr.io/srl-labs/clab:latest"
DEFAULT_LAB = "dcfabric"


@dataclass(frozen=True)
class Lab:
    """One use case's containerlab topology (spec 008 research R15)."""

    topology: Path
    # Container names are clab-<lab name>-<node>.
    prefix: str
    # The SR Linux nodes. Hosts are left out: they have no gNMI to fix.
    routers: tuple[str, ...]


LABS: dict[str, Lab] = {
    "dcfabric": Lab(
        topology=PROJECT_ROOT / "containerlab" / "dcfabric.yml",
        prefix="clab-dcfabric-",
        routers=("spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04"),
    ),
    "site_edge": Lab(
        topology=PROJECT_ROOT / "containerlab" / "site_edge.yml",
        prefix="clab-site-edge-",
        routers=("site-edge-01", "carrier-a"),
    ),
}

# Prometheus's data, bind-mounted by the compose file (spec 012 R6). Under
# .snapl/ so that `down --volumes` (demo.reset) never touches it.
PROMETHEUS_DATA = PROJECT_ROOT / ".snapl" / "prometheus"


def _clab_command(clab_args: str) -> str:
    root = shlex.quote(str(PROJECT_ROOT))
    return (
        "docker run --rm --privileged --pid host --network host "
        "-v /var/run/docker.sock:/var/run/docker.sock "
        "-v /var/run/netns:/var/run/netns "
        "-v /etc/hosts:/etc/hosts "
        "-v /var/lib/docker/containers:/var/lib/docker/containers "
        f"-v {root}:{root} -w {root} "
        f"{CLAB_IMAGE} {clab_args}"
    )


@task
def deps(ctx):
    """Start the dev dependency stack (Infrahub + backing stores + Temporal + Prometheus) and wait for health."""
    # Created before compose up: a directory Docker creates for a bind mount is
    # root's, and the Prometheus image runs as `nobody`. chmod, not mkdir's
    # mode, which the umask would narrow.
    PROMETHEUS_DATA.mkdir(parents=True, exist_ok=True)
    PROMETHEUS_DATA.chmod(0o777)  # nosec B103 — container user differs; dir is under the git-ignored .snapl/
    print("Starting dev dependency stack...")
    execute_command(ctx, f"{COMPOSE} up -d --wait")
    print("Stack is healthy.")
    print("  Infrahub:        http://localhost:18000  (admin / infrahub)")
    print("  Temporal:        localhost:18033")
    print("  Temporal Web UI: http://localhost:18034")
    print("  Prometheus:      http://127.0.0.1:18090")


@task
def stop(ctx):
    """Stop the dev dependency stack without removing containers."""
    execute_command(ctx, f"{COMPOSE} stop")


@task
def down(ctx, volumes=False):
    """Tear down the dev dependency stack. Pass --volumes to also delete data."""
    command = f"{COMPOSE} down"
    if volumes:
        command += " --volumes"
    execute_command(ctx, command)


def _lab(use_case: str) -> Lab:
    try:
        return LABS[use_case]
    except KeyError:
        raise ValueError(f"no lab for use case {use_case!r}; known: {', '.join(sorted(LABS))}") from None


@task(help={"use-case": f"Whose lab to deploy: {', '.join(sorted(LABS))} (default: {DEFAULT_LAB})"})
def lab_deploy(ctx, use_case: str = DEFAULT_LAB):
    """Deploy a use case's Containerlab topology (dcfabric: 2 spines, 4 leaves; site_edge: R13)."""
    lab = _lab(use_case)
    execute_command(ctx, _clab_command(f"containerlab deploy --topo {shlex.quote(str(lab.topology))}"))
    _strip_gnmi_tls(ctx, lab)
    print(f"Lab is up. gNMI (plaintext) on {', '.join(lab.prefix + r for r in lab.routers)}:57400, admin/NokiaSrl1!")


def _strip_gnmi_tls(ctx, lab: Lab):
    """Force plaintext gNMI on 57400 on every node, waiting for boot first.

    The topology ships srlinux-insecure-gnmi.partial.cfg for this, but
    containerlab does not reliably apply partial startup configs in the
    dockerized setup (observed as a silent no-op with clab 0.74/0.77 against
    SR Linux 26.x), so the deploy task enforces it post-deploy. Idempotent:
    nodes already running plaintext are left untouched.
    """
    script = "\n".join(
        f"""
        c={lab.prefix}{node}
        until docker exec $c sr_cli 'info flat system grpc-server mgmt' >/dev/null 2>&1; do sleep 2; done
        if docker exec $c sr_cli 'info flat system grpc-server mgmt' | grep -q tls-profile; then
          docker exec $c sr_cli -ec 'delete / system grpc-server mgmt tls-profile' >/dev/null
          echo "{node}: stripped TLS from gNMI (plaintext on 57400)"
        else
          echo "{node}: gNMI already plaintext"
        fi
        """
        for node in lab.routers
    )
    execute_command(ctx, script)


@task(help={"use-case": f"Whose lab to destroy: {', '.join(sorted(LABS))} (default: {DEFAULT_LAB})"})
def lab_destroy(ctx, use_case: str = DEFAULT_LAB):
    """Destroy a use case's Containerlab topology."""
    lab = _lab(use_case)
    execute_command(ctx, _clab_command(f"containerlab destroy --topo {shlex.quote(str(lab.topology))}"))
