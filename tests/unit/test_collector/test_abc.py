"""Unit tests for Collector ABC contract enforcement (T006)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class TestCollectorABC:
    def test_cannot_instantiate_abstract_class(self):
        from snapl_collector.abc import Collector

        with pytest.raises(TypeError):
            Collector()  # type: ignore[abstract]

    def test_concrete_missing_collect_raises(self):
        from snapl_collector.abc import Collector

        class Incomplete(Collector):
            async def get_running_config(self, device):
                pass

            async def collect_batch(self, devices, paths):
                pass

        with pytest.raises(TypeError):
            Incomplete()

    def test_concrete_missing_get_running_config_raises(self):
        from snapl_collector.abc import Collector

        class Incomplete(Collector):
            async def collect(self, device, paths):
                pass

            async def collect_batch(self, devices, paths):
                pass

        with pytest.raises(TypeError):
            Incomplete()

    def test_concrete_missing_collect_batch_raises(self):
        from snapl_collector.abc import Collector

        class Incomplete(Collector):
            async def collect(self, device, paths):
                pass

            async def get_running_config(self, device):
                pass

        with pytest.raises(TypeError):
            Incomplete()

    def test_full_concrete_implementation_instantiates(self):
        from snapl_collector.abc import Collector

        class Complete(Collector):
            async def collect(self, device, paths):
                pass

            async def get_running_config(self, device):
                pass

            async def collect_batch(self, devices, paths):
                pass

            async def stream_telemetry(self, target, *, interval_s):
                yield

        instance = Complete()
        assert isinstance(instance, Collector)

    def test_concrete_missing_stream_telemetry_raises(self):
        """Spec 016 T003: every collector streams telemetry."""
        from snapl_collector.abc import Collector

        class Incomplete(Collector):
            async def collect(self, device, paths):
                pass

            async def get_running_config(self, device):
                pass

            async def collect_batch(self, devices, paths):
                pass

        with pytest.raises(TypeError, match="stream_telemetry"):
            Incomplete()

    def test_stream_telemetry_signature(self):
        import inspect

        from snapl_collector.abc import Collector

        params = inspect.signature(Collector.stream_telemetry).parameters
        assert list(params) == ["self", "target", "interval_s"]
        assert params["interval_s"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_telemetry_stream_error_is_not_a_programming_error(self):
        """A broken stream is the device's doing, so it stands beside CollectorError (research R4)."""
        from snapl_collector import CollectorError, TelemetryStreamError

        assert issubclass(TelemetryStreamError, Exception)
        assert not issubclass(TelemetryStreamError, CollectorError)
