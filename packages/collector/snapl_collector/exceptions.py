"""Collector exception hierarchy.

``CollectorError`` and its subclasses are only raised for programming errors
(invalid constructor args, bad call arguments). Device-side errors are returned
as CollectResult(success=False, error=...), except on a telemetry stream, which
has no result to carry one: it ends by raising ``TelemetryStreamError``.
"""

from __future__ import annotations


class CollectorError(Exception):
    """Base class for programming errors in the Collector module."""


class CollectorConfigError(CollectorError):
    """Invalid Collector configuration (missing credentials, bad timeout, etc.)."""


class TelemetryStreamError(Exception):
    """A telemetry stream broke: the dial failed, the device closed it, or it stopped answering.

    Not a programming error, so not a ``CollectorError``. The caller restarts the stream.
    """
