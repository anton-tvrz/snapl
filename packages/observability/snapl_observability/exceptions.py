"""Observer exception hierarchy.

Raised only for programming errors (mismatched device IDs in detect_drift inputs,
empty batch, registering a non-callable handler). Drift outcomes — including the
upstream Collector's failures — are returned as DriftReport(status=ERROR).
"""

from __future__ import annotations


class ObserverError(Exception):
    """Base class for programming errors in the Observability module."""
