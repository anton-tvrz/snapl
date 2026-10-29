"""Exception hierarchy for the NAF Orchestrator module.

Programming errors (invalid arguments, missing config) are raised; device/network
outcomes flow through `WorkflowResult` and never escape as exceptions.
"""

from __future__ import annotations


class OrchestratorError(Exception):
    """Base class for programming errors in the Orchestrator module."""


class OrchestratorConfigError(OrchestratorError):
    """Invalid Orchestrator configuration — missing endpoint, bad DB URL, etc."""


class AuditLogError(OrchestratorError):
    """Append or query failed against the durable audit store after retries."""


class ScheduleNotFoundError(OrchestratorError):
    """The use case has no drift schedule."""


class UnreadableScheduleError(OrchestratorError):
    """The drift schedule was changed outside snapl into a shape snapl cannot read."""


class ScheduleRunInProgressError(OrchestratorError):
    """A scheduled run is in progress, so a trigger would be skipped by the overlap policy."""
