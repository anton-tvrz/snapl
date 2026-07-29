"""Connection settings, resolved from the worker's environment contract.

Spec 006 FR-009: the CLI must not restate the worker's defaults. It imports
them, so a change to where the worker looks is automatically a change to where
the CLI looks. A CLI pointed at a different Temporal or a different SoT than
the worker is the most confusing failure available here — it succeeds at
everything except doing anything.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from snapl_intent.infrahub.client import DEFAULT_ADDRESS as DEFAULT_INFRAHUB_ADDRESS
from snapl_orchestrator.worker.run import (
    DEFAULT_AUDIT_DB,
    DEFAULT_NAMESPACE,
    DEFAULT_TASK_QUEUE,
    DEFAULT_TEMPORAL_HOST,
)

DEFAULT_CONNECT_TIMEOUT = 10.0


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
        )
