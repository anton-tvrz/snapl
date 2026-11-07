"""Connection settings, resolved from the worker's environment contract.

Spec 006 FR-009: the CLI must not restate the worker's defaults. It imports
them, so a change to where the worker looks is automatically a change to where
the CLI looks. A CLI pointed at a different Temporal or a different SoT than
the worker is the most confusing failure available here — it succeeds at
everything except doing anything.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from snapl_intent.infrahub.client import DEFAULT_ADDRESS as DEFAULT_INFRAHUB_ADDRESS
from snapl_orchestrator.worker.run import (
    DEFAULT_AUDIT_DB,
    DEFAULT_NAMESPACE,
    DEFAULT_TASK_QUEUE,
    DEFAULT_TEMPORAL_HOST,
)

DEFAULT_CONNECT_TIMEOUT = 10.0

# Host ports from dev/knowledge/host-resource-registry.md.
DEFAULT_DASHBOARD_PORT = 18030
DEFAULT_TEMPORAL_UI_PORT = 18034
# Loopback only: the dashboard has no authentication (spec 010 FR-002).
DEFAULT_DASHBOARD_HOST = "127.0.0.1"
DEFAULT_TEST_RUNS_DIR = ".snapl/test-runs"


@dataclass(frozen=True)
class CliSettings:
    """Where the CLI talks to, and which env var moves each one."""

    temporal_host: str = DEFAULT_TEMPORAL_HOST
    temporal_namespace: str = DEFAULT_NAMESPACE
    task_queue: str = DEFAULT_TASK_QUEUE
    infrahub_address: str = DEFAULT_INFRAHUB_ADDRESS
    infrahub_token: str | None = None
    audit_db: str = DEFAULT_AUDIT_DB
    # Temporal's client retries its initial handshake forever; without a cap
    # the CLI hangs silently against a cluster that is not running.
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    # Dashboard (spec 010).
    dashboard_port: int = DEFAULT_DASHBOARD_PORT
    dashboard_host: str = DEFAULT_DASHBOARD_HOST
    temporal_ui_address: str = f"http://localhost:{DEFAULT_TEMPORAL_UI_PORT}"
    # None means "resolve from `git remote get-url origin`".
    github_repo: str | None = None
    github_token: str | None = field(default=None, repr=False)
    test_runs_dir: str = DEFAULT_TEST_RUNS_DIR

    @classmethod
    def from_env(cls) -> CliSettings:
        return cls(
            temporal_host=os.environ.get("TEMPORAL_HOST") or DEFAULT_TEMPORAL_HOST,
            temporal_namespace=os.environ.get("TEMPORAL_NAMESPACE") or DEFAULT_NAMESPACE,
            task_queue=os.environ.get("TEMPORAL_TASK_QUEUE") or DEFAULT_TASK_QUEUE,
            infrahub_address=os.environ.get("INFRAHUB_ADDRESS") or DEFAULT_INFRAHUB_ADDRESS,
            infrahub_token=os.environ.get("INFRAHUB_API_TOKEN"),
            audit_db=os.environ.get("SNAPL_AUDIT_DB") or DEFAULT_AUDIT_DB,
            connect_timeout=float(os.environ.get("SNAPL_CONNECT_TIMEOUT") or DEFAULT_CONNECT_TIMEOUT),
            dashboard_port=int(os.environ.get("DASHBOARD_PORT") or DEFAULT_DASHBOARD_PORT),
            dashboard_host=os.environ.get("DASHBOARD_HOST") or DEFAULT_DASHBOARD_HOST,
            temporal_ui_address=os.environ.get("TEMPORAL_UI_ADDRESS")
            or f"http://localhost:{os.environ.get('TEMPORAL_UI_PORT') or DEFAULT_TEMPORAL_UI_PORT}",
            github_repo=os.environ.get("SNAPL_GITHUB_REPO") or None,
            github_token=os.environ.get("GITHUB_TOKEN") or None,
            test_runs_dir=os.environ.get("SNAPL_TEST_RUNS_DIR") or DEFAULT_TEST_RUNS_DIR,
        )
