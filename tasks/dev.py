"""Dev environment tasks — manage the docker compose dependency stack."""

from __future__ import annotations

from invoke import task

from .shared import PROJECT_ROOT, execute_command

COMPOSE = f"docker compose -f {PROJECT_ROOT / 'development' / 'docker-compose.yml'}"


@task
def deps(ctx):
    """Start the dev dependency stack (Infrahub + backing stores + Temporal) and wait for health."""
    print("Starting dev dependency stack...")
    execute_command(ctx, f"{COMPOSE} up -d --wait")
    print("Stack is healthy.")
    print("  Infrahub:        http://localhost:8000  (admin / infrahub)")
    print("  Temporal:        localhost:7233")
    print("  Temporal Web UI: http://localhost:8233")


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
