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

            def evaluate_health(self, expectation, observation, *, now, interval_s):
                return None

        Complete()  # no exception

    def test_missing_evaluate_health_raises(self):
        """Spec 016 T013: every observer judges health."""
        from snapl_observability.abc import Observer

        class Incomplete(Observer):
            async def detect_drift(self, desired, actual):
                return None

            async def detect_drift_batch(self, pairs):
                return None

            async def emit_event(self, report):
                return None

            async def log_audit(self, entry):
                return None

        with pytest.raises(TypeError, match="evaluate_health"):
            Incomplete()

    def test_evaluate_health_signature(self):
        import inspect

        from snapl_observability.abc import Observer

        params = inspect.signature(Observer.evaluate_health).parameters
        assert list(params) == ["self", "expectation", "observation", "now", "interval_s"]
        assert params["now"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["interval_s"].kind is inspect.Parameter.KEYWORD_ONLY
        assert not inspect.iscoroutinefunction(Observer.evaluate_health)
