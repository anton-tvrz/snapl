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

        instance = Complete()
        assert isinstance(instance, Collector)
