"""Collector exception hierarchy.

Only raised for programming errors (invalid constructor args, bad call arguments).
Device-side errors are returned as CollectResult(success=False, error=...).
"""

from __future__ import annotations


class CollectorError(Exception):
    """Base class for programming errors in the Collector module."""


class CollectorConfigError(CollectorError):
    """Invalid Collector configuration (missing credentials, bad timeout, etc.)."""
