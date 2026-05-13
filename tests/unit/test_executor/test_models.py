"""Unit tests for snapl_executor result models (T004)."""

from __future__ import annotations

from uuid import UUID

import pytest

from snapl_executor.models import ApplyResult, BatchResult, DryRunResult

_DEV_ID = UUID("00000000-0000-0000-0000-000000000001")


class TestApplyResult:
    def test_success_implies_no_error(self):
        r = ApplyResult(device_id=_DEV_ID, device_name="spine-01", success=True, payload={})
        assert r.error is None

    def test_failure_has_error(self):
        r = ApplyResult(device_id=_DEV_ID, device_name="spine-01", success=False, payload={}, error="timeout")
        assert r.error == "timeout"

    def test_is_rollback_default_false(self):
        r = ApplyResult(device_id=_DEV_ID, device_name="spine-01", success=True, payload={})
        assert r.is_rollback is False

    def test_is_rollback_flag(self):
        r = ApplyResult(device_id=_DEV_ID, device_name="spine-01", success=True, payload={}, is_rollback=True)
        assert r.is_rollback is True

    def test_duration_ms_default_zero(self):
        r = ApplyResult(device_id=_DEV_ID, device_name="spine-01", success=True, payload={})
        assert r.duration_ms == 0

    def test_immutable(self):
        r = ApplyResult(device_id=_DEV_ID, device_name="spine-01", success=True, payload={})
        with pytest.raises((AttributeError, TypeError)):
            r.success = False  # type: ignore[misc]


class TestDryRunResult:
    def test_success_has_payload(self):
        payload = {"interface": []}
        r = DryRunResult(device_id=_DEV_ID, device_name="spine-01", success=True, payload=payload)
        assert r.payload == payload
        assert r.render_error is None

    def test_failure_has_render_error(self):
        r = DryRunResult(device_id=_DEV_ID, device_name="spine-01", success=False, render_error="undefined: foo")
        assert r.render_error == "undefined: foo"
        assert r.payload is None

    def test_immutable(self):
        r = DryRunResult(device_id=_DEV_ID, device_name="spine-01", success=True, payload={})
        with pytest.raises((AttributeError, TypeError)):
            r.success = False  # type: ignore[misc]


class TestBatchResult:
    def test_succeeded_plus_failed_equals_total(self):
        r1 = ApplyResult(device_id=_DEV_ID, device_name="d1", success=True, payload={})
        d2 = UUID("00000000-0000-0000-0000-000000000002")
        r2 = ApplyResult(device_id=d2, device_name="d2", success=False, payload={}, error="timeout")
        batch = BatchResult(
            results={_DEV_ID: r1, d2: r2},
            total=2,
            succeeded=1,
            failed=1,
        )
        assert batch.succeeded + batch.failed == batch.total

    def test_empty_batch(self):
        batch = BatchResult(results={}, total=0, succeeded=0, failed=0)
        assert batch.total == 0

    def test_immutable(self):
        batch = BatchResult(results={}, total=0, succeeded=0, failed=0)
        with pytest.raises((AttributeError, TypeError)):
            batch.total = 5  # type: ignore[misc]
