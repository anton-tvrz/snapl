"""Temporal activities for the NAF Orchestrator.

Activities wrap downstream NAF block calls behind Temporal's durable, retryable
boundary. Concrete dependencies (IntentStore, Executor, Collector, Observer,
AuditLog) are resolved via the module-level `_activities` container set by the
worker bootstrap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from snapl_orchestrator.exceptions import OrchestratorConfigError

if TYPE_CHECKING:
    from snapl_collector.abc import Collector
    from snapl_executor.abc import Executor
    from snapl_intent.abc import IntentStore
    from snapl_observability.abc import Observer
    from snapl_orchestrator.audit.abc import AuditLog


@dataclass
class Activities:
    """Dependency container resolved by activities at runtime."""

    intent_store: IntentStore
    executor: Executor
    collector: Collector
    observer: Observer
    audit_log: AuditLog


_activities: Activities | None = None


def set_activities(activities: Activities) -> None:
    """Install the activities container. Called once by the worker bootstrap."""
    global _activities  # noqa: PLW0603 — singleton bootstrap
    _activities = activities


def get_activities() -> Activities:
    """Return the installed activities container, or raise if uninstalled."""
    if _activities is None:
        raise OrchestratorConfigError("Activities container not installed — call set_activities() in worker bootstrap")
    return _activities
