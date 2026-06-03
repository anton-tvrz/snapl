"""Unit tests for AuditLog (T007)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

pytestmark = pytest.mark.unit


def _entry(device_id, component="caller", offset_s=0):
    from snapl_observability.models import AuditEntry, AuditOperation, AuditOutcome

    return AuditEntry(
        operation=AuditOperation.DETECT_DRIFT,
        device_id=device_id,
        component=component,
        outcome=AuditOutcome.SUCCESS,
        timestamp=datetime.now(tz=UTC) + timedelta(seconds=offset_s),
    )


class TestAuditLog:
    def test_append_increases_length(self):
        from snapl_observability.audit import AuditLog

        log = AuditLog()
        assert len(log) == 0
        log.append(_entry(uuid4()))
        assert len(log) == 1
        log.append(_entry(uuid4()))
        assert len(log) == 2

    def test_query_by_device_filters(self):
        from snapl_observability.audit import AuditLog

        log = AuditLog()
        d1, d2 = uuid4(), uuid4()
        log.append(_entry(d1))
        log.append(_entry(d2))
        log.append(_entry(d1))
        result = log.query_by_device(d1)
        assert len(result) == 2
        assert all(e.device_id == d1 for e in result)

    def test_query_by_device_unknown_returns_empty(self):
        from snapl_observability.audit import AuditLog

        log = AuditLog()
        log.append(_entry(uuid4()))
        result = log.query_by_device(UUID(int=99999))
        assert result == []

    def test_query_chronological_order(self):
        from snapl_observability.audit import AuditLog

        log = AuditLog()
        d = uuid4()
        log.append(_entry(d, offset_s=10))
        log.append(_entry(d, offset_s=0))
        log.append(_entry(d, offset_s=5))
        result = log.query_by_device(d)
        timestamps = [e.timestamp for e in result]
        assert timestamps == sorted(timestamps)

    def test_all_returns_chronological_list(self):
        from snapl_observability.audit import AuditLog

        log = AuditLog()
        log.append(_entry(uuid4(), offset_s=20))
        log.append(_entry(uuid4(), offset_s=0))
        log.append(_entry(uuid4(), offset_s=10))
        all_entries = log.all()
        timestamps = [e.timestamp for e in all_entries]
        assert timestamps == sorted(timestamps)
        assert len(all_entries) == 3

    def test_query_returns_copy(self):
        from snapl_observability.audit import AuditLog

        log = AuditLog()
        d = uuid4()
        log.append(_entry(d))
        result = log.query_by_device(d)
        result.clear()
        # Internal storage should still have the entry
        assert len(log) == 1
        assert len(log.query_by_device(d)) == 1

    def test_all_returns_copy(self):
        from snapl_observability.audit import AuditLog

        log = AuditLog()
        log.append(_entry(uuid4()))
        all_entries = log.all()
        all_entries.clear()
        assert len(log) == 1

    def test_concurrent_appends_are_safe(self):
        from snapl_observability.audit import AuditLog

        log = AuditLog()
        per_thread = 100
        n_threads = 8

        def worker():
            for _ in range(per_thread):
                log.append(_entry(uuid4()))

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(log) == per_thread * n_threads
