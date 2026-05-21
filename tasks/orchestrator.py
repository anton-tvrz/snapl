"""Orchestrator tasks — start the Temporal worker."""

from __future__ import annotations

import asyncio
import logging

from invoke import task

from snapl_orchestrator.worker.run import run_worker


@task
def start(ctx):
    """Start the snapl-orchestrator Temporal worker.

    Reads env vars: TEMPORAL_HOST, TEMPORAL_NAMESPACE, TEMPORAL_TASK_QUEUE,
    SNAPL_AUDIT_DB, INFRAHUB_ADDRESS, INFRAHUB_API_TOKEN, SRLINUX_USERNAME,
    SRLINUX_PASSWORD. See quickstart.md for defaults and required values.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run_worker())
