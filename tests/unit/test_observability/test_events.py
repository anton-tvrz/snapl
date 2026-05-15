"""Unit tests for EventBus (T008)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

import pytest

pytestmark = pytest.mark.unit

_DEVICE_ID = UUID("00000000-0000-0000-0000-000000000001")
_NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


def _event():
    from snapl_observability.models import (
        DriftReport,
        DriftStatus,
        EventType,
        ObservabilityEvent,
    )

    report = DriftReport(
        device_id=_DEVICE_ID,
        device_name="spine-01",
        status=DriftStatus.CLEAN,
        items=[],
        timestamp=_NOW,
    )
    return ObservabilityEvent(
        event_type=EventType.STATE_CLEAN,
        device_id=_DEVICE_ID,
        device_name="spine-01",
        report=report,
        timestamp=_NOW,
    )


class TestEventBus:
    def test_register_adds_handler(self):
        from snapl_observability.events import EventBus

        bus = EventBus()

        def h(_event):
            pass

        bus.register(h)
        assert h in bus.handlers

    def test_emit_with_no_handlers_is_silent(self):
        from snapl_observability.events import EventBus

        bus = EventBus()
        bus.emit(_event())  # no exception

    def test_emit_invokes_all_handlers_in_order(self):
        from snapl_observability.events import EventBus

        bus = EventBus()
        order = []

        def h1(ev):
            order.append("h1")

        def h2(ev):
            order.append("h2")

        bus.register(h1)
        bus.register(h2)
        bus.emit(_event())
        assert order == ["h1", "h2"]

    def test_handler_exception_does_not_block_others(self, caplog):
        from snapl_observability.events import EventBus

        bus = EventBus()
        called = []

        def bad(ev):
            raise RuntimeError("boom")

        def good(ev):
            called.append(ev)

        bus.register(bad)
        bus.register(good)
        with caplog.at_level(logging.WARNING):
            bus.emit(_event())
        assert len(called) == 1
        assert any("boom" in rec.message or "RuntimeError" in rec.message for rec in caplog.records)

    def test_handlers_property_returns_tuple(self):
        from snapl_observability.events import EventBus

        bus = EventBus()
        assert isinstance(bus.handlers, tuple)

    def test_register_non_callable_raises(self):
        from snapl_observability.events import EventBus
        from snapl_observability.exceptions import ObserverError

        bus = EventBus()
        with pytest.raises(ObserverError):
            bus.register("not callable")  # type: ignore[arg-type]
