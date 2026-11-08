"""The worker's Prometheus endpoint, served by the Temporal SDK runtime (spec 012, R1 to R3).

One endpoint carries both the SDK's own metrics and the activities' gNMI timing.
A taken port fails at ``Runtime()`` construction (measured, R3), so
``build_runtime`` catches it there and the worker carries on without metrics
(FR-002).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal

from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig

from snapl_orchestrator.exceptions import OrchestratorConfigError

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_WORKER_METRICS_BIND",
    "DEFAULT_WORKER_METRICS_PORT",
    "MetricsSettings",
    "build_runtime",
    "metrics_settings_from_env",
]

# Host port from dev/knowledge/host-resource-registry.md. Loopback only: a
# container reaches it through host.docker.internal on macOS (R2).
DEFAULT_WORKER_METRICS_PORT = 18091
DEFAULT_WORKER_METRICS_BIND = "127.0.0.1"

Off = Literal["off"]


@dataclass(frozen=True)
class MetricsSettings:
    port: int | Off
    bind: str

    @property
    def url(self) -> str:
        return f"http://{self.bind}:{self.port}/metrics"


def metrics_settings_from_env() -> MetricsSettings:
    """Read ``WORKER_METRICS_PORT`` (a port or ``off``) and ``WORKER_METRICS_BIND``.

    Raises:
        OrchestratorConfigError: the port is neither a TCP port nor ``off``.
    """
    raw = (os.environ.get("WORKER_METRICS_PORT") or "").strip()
    bind = (os.environ.get("WORKER_METRICS_BIND") or "").strip() or DEFAULT_WORKER_METRICS_BIND
    if not raw:
        return MetricsSettings(port=DEFAULT_WORKER_METRICS_PORT, bind=bind)
    if raw.lower() == "off":
        return MetricsSettings(port="off", bind=bind)
    if not raw.isdigit() or not 0 < int(raw) < 65536:
        raise OrchestratorConfigError(f"WORKER_METRICS_PORT must be a port (1-65535) or 'off', got {raw!r}")
    return MetricsSettings(port=int(raw), bind=bind)


def build_runtime(*, port: int | Off, bind: str) -> Runtime | None:
    """A runtime that serves Prometheus metrics on ``bind:port``, or None.

    None means "connect with the default runtime": metrics are off, or the
    exporter could not start. A warning says which; the worker never stops for it.
    """
    if port == "off":
        return None
    try:
        return Runtime(telemetry=TelemetryConfig(metrics=PrometheusConfig(bind_address=f"{bind}:{port}")))
    except ValueError as exc:
        if "in use" in str(exc).lower():
            logger.warning("metrics off: port %s in use (set WORKER_METRICS_PORT to move it, or off)", port)
        else:
            logger.warning("metrics off: could not serve on %s:%s (%s)", bind, port, exc)
        return None
