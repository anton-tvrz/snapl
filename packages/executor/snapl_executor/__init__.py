"""NAF Executor building block — config deployment via gNMI, Jinja2 templates."""

from snapl_executor.abc import Executor
from snapl_executor.exceptions import ExecutorConfigError, ExecutorError, ExecutorRenderError
from snapl_executor.gnmi.executor import GnmiExecutor
from snapl_executor.models import ApplyResult, BatchResult, DryRunResult

__all__ = [
    "ApplyResult",
    "BatchResult",
    "DryRunResult",
    "Executor",
    "ExecutorConfigError",
    "ExecutorError",
    "ExecutorRenderError",
    "GnmiExecutor",
]
