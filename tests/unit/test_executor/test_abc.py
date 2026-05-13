"""Unit tests for Executor ABC contract enforcement (T005)."""

from __future__ import annotations

import pytest

from snapl_executor.abc import Executor


class TestExecutorABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            Executor()  # type: ignore[abstract]

    def test_concrete_missing_apply_raises(self):
        class BadExecutor(Executor):
            async def rollback(self, desired): ...
            async def dry_run(self, desired): ...
            async def apply_batch(self, states): ...

        with pytest.raises(TypeError):
            BadExecutor()  # type: ignore[abstract]

    def test_concrete_missing_rollback_raises(self):
        class BadExecutor(Executor):
            async def apply(self, desired): ...
            async def dry_run(self, desired): ...
            async def apply_batch(self, states): ...

        with pytest.raises(TypeError):
            BadExecutor()  # type: ignore[abstract]

    def test_concrete_missing_dry_run_raises(self):
        class BadExecutor(Executor):
            async def apply(self, desired): ...
            async def rollback(self, desired): ...
            async def apply_batch(self, states): ...

        with pytest.raises(TypeError):
            BadExecutor()  # type: ignore[abstract]

    def test_concrete_missing_apply_batch_raises(self):
        class BadExecutor(Executor):
            async def apply(self, desired): ...
            async def rollback(self, desired): ...
            async def dry_run(self, desired): ...

        with pytest.raises(TypeError):
            BadExecutor()  # type: ignore[abstract]

    def test_concrete_with_all_methods_instantiates(self):
        class GoodExecutor(Executor):
            async def apply(self, desired): ...
            async def rollback(self, desired): ...
            async def dry_run(self, desired): ...
            async def apply_batch(self, states): ...

        executor = GoodExecutor()
        assert isinstance(executor, Executor)
