"""Dev environment tasks — manage the docker compose dependency stack."""

from __future__ import annotations

import shlex

from invoke import task

from .shared import PROJECT_ROOT, execute_command

COMPOSE = f"docker compose -f {PROJECT_ROOT / 'development' / 'docker-compose.yml'}"

# Containerlab runs Docker-outside-of-Docker (no native binary needed):
# the clab container drives the host Docker daemon through the mounted socket.
CLAB_IMAGE = "ghcr.io/srl-labs/clab:latest"
LAB_TOPOLOGY = PROJECT_ROOT / "containerlab" / "dcfabric.yml"
LAB_NODES = ("spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04")


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
    """Start the dev dependency stack (Infrahub + backing stores + Temporal) and wait for health."""
    print("Starting dev dependency stack...")
    execute_command(ctx, f"{COMPOSE} up -d --wait")
    print("Stack is healthy.")
    print("  Infrahub:        http://localhost:18000  (admin / infrahub)")
    print("  Temporal:        localhost:18033")
    print("  Temporal Web UI: http://localhost:18034")


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


@task
def lab_deploy(ctx):
    """Deploy the dcfabric Containerlab topology (2 spines, 4 leaves, SR Linux)."""
    execute_command(ctx, _clab_command(f"containerlab deploy --topo {shlex.quote(str(LAB_TOPOLOGY))}"))
    _strip_gnmi_tls(ctx)
    print("Lab is up. gNMI (plaintext) on clab-dcfabric-{spine,leaf}-NN:57400, admin/NokiaSrl1!")


def _strip_gnmi_tls(ctx):
    """Force plaintext gNMI on 57400 on every node, waiting for boot first.

    The topology ships srlinux-insecure-gnmi.partial.cfg for this, but
    containerlab does not reliably apply partial startup configs in the
    dockerized setup (observed as a silent no-op with clab 0.74/0.77 against
    SR Linux 26.x), so the deploy task enforces it post-deploy. Idempotent:
    nodes already running plaintext are left untouched.
    """
    script = "\n".join(
        f"""
        c=clab-dcfabric-{node}
        until docker exec $c sr_cli 'info flat system grpc-server mgmt' >/dev/null 2>&1; do sleep 2; done
        if docker exec $c sr_cli 'info flat system grpc-server mgmt' | grep -q tls-profile; then
          docker exec $c sr_cli -ec 'delete / system grpc-server mgmt tls-profile' >/dev/null
          echo "{node}: stripped TLS from gNMI (plaintext on 57400)"
        else
          echo "{node}: gNMI already plaintext"
        fi
        """
        for node in LAB_NODES
    )
    execute_command(ctx, script)


@task
def lab_destroy(ctx):
    """Destroy the dcfabric Containerlab topology."""
    execute_command(ctx, _clab_command(f"containerlab destroy --topo {shlex.quote(str(LAB_TOPOLOGY))}"))
