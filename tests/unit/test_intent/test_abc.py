"""Unit tests for the IntentStore abstract base class."""

from __future__ import annotations

import inspect
from abc import ABC

import pytest

from snapl_intent.abc import IntentStore

pytestmark = pytest.mark.unit


class TestIntentStoreABC:
    def test_is_abstract_base_class(self):
        assert issubclass(IntentStore, ABC)

    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            IntentStore()  # type: ignore[abstract]

    @pytest.mark.parametrize(
        "method_name",
        [
            "get_desired_state",
            "get_schema",
            "provision_schema",
            "seed",
            "delete_device",
        ],
    )
    def test_exposes_required_abstract_method(self, method_name: str):
        method = getattr(IntentStore, method_name)
        assert getattr(method, "__isabstractmethod__", False), (
            f"{method_name} must be marked @abstractmethod"
        )

    @pytest.mark.parametrize(
        "method_name",
        [
            "get_desired_state",
            "get_schema",
            "provision_schema",
            "seed",
            "delete_device",
        ],
    )
    def test_method_is_async(self, method_name: str):
        method = getattr(IntentStore, method_name)
        assert inspect.iscoroutinefunction(method), f"{method_name} must be async"

    def test_incomplete_subclass_cannot_instantiate(self):
        class Incomplete(IntentStore):
            # Intentionally missing most methods.
            async def get_desired_state(self, **kwargs):
                return []

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_complete_subclass_can_instantiate(self):
        class Complete(IntentStore):
            async def get_desired_state(self, **kwargs):
                return []

            async def get_schema(self, use_case):
                return None

            async def provision_schema(self, use_case):
                return None

            async def seed(self, use_case, *, data_path=None, branch=None):
                return None

            async def delete_device(self, device_id):
                return None

        # Should not raise — all abstract methods implemented.
        instance = Complete()
        assert isinstance(instance, IntentStore)
