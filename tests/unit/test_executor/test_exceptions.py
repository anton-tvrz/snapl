"""Unit tests for the executor exception hierarchy."""

from __future__ import annotations

import pytest

from snapl_executor.exceptions import ExecutorConfigError, ExecutorError, ExecutorRenderError

pytestmark = pytest.mark.unit


class TestExceptionHierarchy:
    def test_executor_error_is_exception(self):
        assert issubclass(ExecutorError, Exception)

    def test_render_error_is_executor_error(self):
        assert issubclass(ExecutorRenderError, ExecutorError)

    def test_config_error_is_executor_error(self):
        assert issubclass(ExecutorConfigError, ExecutorError)

    def test_executor_error_can_be_raised_and_caught(self):
        with pytest.raises(ExecutorError):
            raise ExecutorError("base error")

    def test_render_error_caught_as_executor_error(self):
        with pytest.raises(ExecutorError):
            raise ExecutorRenderError("template syntax error")

    def test_config_error_caught_as_executor_error(self):
        with pytest.raises(ExecutorError):
            raise ExecutorConfigError("missing credentials")

    def test_subclasses_are_distinct(self):
        assert ExecutorRenderError is not ExecutorConfigError
