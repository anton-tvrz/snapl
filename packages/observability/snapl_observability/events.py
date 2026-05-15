"""Synchronous in-process event dispatcher.

Async event-bus integration (Temporal signals, message brokers) is the
Orchestrator block's responsibility — see research R3. This implementation
satisfies FR-004 / FR-005 / FR-006 with minimal surface.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from snapl_observability.exceptions import ObserverError

if TYPE_CHECKING:
    from snapl_observability.models import ObservabilityEvent

_logger = logging.getLogger(__name__)

EventHandler = Callable[["ObservabilityEvent"], None]


class EventBus:
    """Synchronous in-process dispatcher. Per-handler exceptions are isolated."""

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []

    def register(self, handler: EventHandler) -> None:
        if not callable(handler):
            raise ObserverError(f"handler must be callable, got {type(handler).__name__}")
        self._handlers.append(handler)

    def emit(self, event: ObservabilityEvent) -> None:
        for handler in self._handlers:
            try:
                handler(event)
            except Exception as exc:
                _logger.warning(
                    "EventBus handler %s raised %s: %s",
                    getattr(handler, "__qualname__", repr(handler)),
                    type(exc).__name__,
                    exc,
                )

    @property
    def handlers(self) -> tuple[EventHandler, ...]:
        return tuple(self._handlers)
