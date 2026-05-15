"""Unit tests for Observer ABC contract enforcement (T006)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class TestObserverABC:
    def test_cannot_instantiate_abstract_class(self):
        from snapl_observability.abc import Observer

        with pytest.raises(TypeError):
            Observer()  # type: ignore[abstract]

    def test_concrete_missing_detect_drift_raises(self):
        from snapl_observability.abc import Observer

        class Incomplete(Observer):
            async def detect_drift_batch(self, pairs):
                pass

            async def emit_event(self, report):
                pass

            async def log_audit(self, entry):
                pass

        with pytest.raises(TypeError):
            Incomplete()

    def test_concrete_missing_detect_drift_batch_raises(self):
        from snapl_observability.abc import Observer

        class Incomplete(Observer):
            async def detect_drift(self, desired, actual):
                pass

            async def emit_event(self, report):
                pass

            async def log_audit(self, entry):
                pass

        with pytest.raises(TypeError):
            Incomplete()

    def test_concrete_missing_emit_event_raises(self):
        from snapl_observability.abc import Observer

        class Incomplete(Observer):
            async def detect_drift(self, desired, actual):
                pass

            async def detect_drift_batch(self, pairs):
                pass

            async def log_audit(self, entry):
                pass

        with pytest.raises(TypeError):
            Incomplete()

    def test_concrete_missing_log_audit_raises(self):
        from snapl_observability.abc import Observer

        class Incomplete(Observer):
            async def detect_drift(self, desired, actual):
                pass

            async def detect_drift_batch(self, pairs):
                pass

            async def emit_event(self, report):
                pass

        with pytest.raises(TypeError):
            Incomplete()

    def test_complete_subclass_can_be_instantiated(self):
        from snapl_observability.abc import Observer

        class Complete(Observer):
            async def detect_drift(self, desired, actual):
                return None

            async def detect_drift_batch(self, pairs):
                return None

            async def emit_event(self, report):
                return None

            async def log_audit(self, entry):
                return None

        Complete()  # no exception
