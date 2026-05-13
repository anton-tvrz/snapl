"""Unit tests for CollectResult and BatchCollectResult models (T005)."""

from __future__ import annotations

from datetime import UTC
from uuid import UUID, uuid4

import pytest

pytestmark = pytest.mark.unit

_DEVICE_ID = UUID("00000000-0000-0000-0000-000000000001")


class TestCollectResult:
    def test_success_true_implies_no_error(self):
        from snapl_collector.models import CollectResult

        r = CollectResult(device_id=_DEVICE_ID, device_name="spine-01", success=True)
        assert r.error is None

    def test_success_false_implies_error_set(self):
        from snapl_collector.models import CollectResult

        r = CollectResult(
            device_id=_DEVICE_ID,
            device_name="spine-01",
            success=False,
            error="connectivity error: connection refused",
        )
        assert r.error is not None
        assert r.success is False

    def test_success_false_data_defaults_to_empty_dict(self):
        from snapl_collector.models import CollectResult

        r = CollectResult(
            device_id=_DEVICE_ID,
            device_name="spine-01",
            success=False,
            error="timeout after 30s",
        )
        assert r.data == {}

    def test_paths_defaults_to_empty_list(self):
        from snapl_collector.models import CollectResult

        r = CollectResult(device_id=_DEVICE_ID, device_name="spine-01", success=True)
        assert r.paths == []

    def test_duration_ms_defaults_to_zero(self):
        from snapl_collector.models import CollectResult

        r = CollectResult(device_id=_DEVICE_ID, device_name="spine-01", success=True)
        assert r.duration_ms == 0

    def test_timestamp_defaults_to_utc(self):
        from snapl_collector.models import CollectResult

        r = CollectResult(device_id=_DEVICE_ID, device_name="spine-01", success=True)
        assert r.timestamp is not None
        assert r.timestamp.tzinfo == UTC

    def test_is_frozen_dataclass(self):
        from snapl_collector.models import CollectResult

        r = CollectResult(device_id=_DEVICE_ID, device_name="spine-01", success=True)
        with pytest.raises((AttributeError, TypeError)):
            r.success = False  # type: ignore[misc]

    def test_data_populated_on_success(self):
        from snapl_collector.models import CollectResult

        data = {"/interface": [{"name": "ethernet-1/1"}]}
        r = CollectResult(
            device_id=_DEVICE_ID,
            device_name="spine-01",
            success=True,
            data=data,
            paths=["/interface"],
        )
        assert r.data == data
        assert r.paths == ["/interface"]


class TestBatchCollectResult:
    def test_defaults_to_empty(self):
        from snapl_collector.models import BatchCollectResult

        b = BatchCollectResult()
        assert b.total == 0
        assert b.succeeded == 0
        assert b.failed == 0
        assert b.results == {}

    def test_succeeded_plus_failed_equals_total(self):
        from snapl_collector.models import BatchCollectResult, CollectResult

        id1, id2, id3 = uuid4(), uuid4(), uuid4()
        results = {
            id1: CollectResult(device_id=id1, device_name="d1", success=True),
            id2: CollectResult(device_id=id2, device_name="d2", success=True),
            id3: CollectResult(device_id=id3, device_name="d3", success=False, error="err"),
        }
        b = BatchCollectResult(results=results, total=3, succeeded=2, failed=1)
        assert b.succeeded + b.failed == b.total

    def test_results_keyed_by_uuid(self):
        from snapl_collector.models import BatchCollectResult, CollectResult

        dev_id = uuid4()
        r = CollectResult(device_id=dev_id, device_name="spine-01", success=True)
        b = BatchCollectResult(results={dev_id: r}, total=1, succeeded=1, failed=0)
        assert b.results[dev_id] is r

    def test_is_frozen_dataclass(self):
        from snapl_collector.models import BatchCollectResult

        b = BatchCollectResult()
        with pytest.raises((AttributeError, TypeError)):
            b.total = 5  # type: ignore[misc]
