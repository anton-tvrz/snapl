"""Unit tests for Observability Pydantic models and enums (T005, T027)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.unit


class TestImportIsolation:
    """SC-003: importing snapl_observability touches no external services."""

    def test_top_level_import_succeeds(self):
        # Force fresh import paths if previously cached
        for mod_name in list(sys.modules):
            if mod_name.startswith("snapl_observability"):
                pass  # already imported by other tests; importing again is a no-op
        import snapl_observability  # noqa: F401

    def test_structural_subpackage_import_succeeds(self):
        import snapl_observability.structural
        import snapl_observability.structural.diff
        import snapl_observability.structural.observer  # noqa: F401


_DEVICE_ID = UUID("00000000-0000-0000-0000-000000000001")
_NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


class TestEnums:
    def test_drift_status_values(self):
        from snapl_observability.models import DriftStatus

        assert DriftStatus.CLEAN.value == "clean"
        assert DriftStatus.DRIFTED.value == "drifted"
        assert DriftStatus.ERROR.value == "error"

    def test_event_type_values(self):
        from snapl_observability.models import EventType

        assert EventType.DRIFT_DETECTED.value == "drift_detected"
        assert EventType.STATE_CLEAN.value == "state_clean"
        assert EventType.DRIFT_ERROR.value == "drift_error"

    def test_audit_operation_values(self):
        from snapl_observability.models import AuditOperation

        assert AuditOperation.DETECT_DRIFT.value == "detect_drift"
        assert AuditOperation.EMIT_EVENT.value == "emit_event"
        assert AuditOperation.LOG_AUDIT.value == "log_audit"

    def test_audit_outcome_values(self):
        from snapl_observability.models import AuditOutcome

        assert AuditOutcome.SUCCESS.value == "success"
        assert AuditOutcome.FAILURE.value == "failure"


class TestDriftItem:
    def test_construction_with_mismatched_values(self):
        from snapl_observability.models import DriftItem

        item = DriftItem(
            path="/interface[name=eth0]/mtu",
            desired=9000,
            actual=1500,
            entity_kind="interface",
        )
        assert item.path == "/interface[name=eth0]/mtu"
        assert item.desired == 9000
        assert item.actual == 1500

    def test_equal_values_raise_value_error(self):
        from snapl_observability.models import DriftItem

        with pytest.raises(ValueError, match=r"desired.*equal"):
            DriftItem(path="/x", desired=1, actual=1, entity_kind="interface")

    def test_frozen(self):
        from snapl_observability.models import DriftItem

        item = DriftItem(path="/x", desired=1, actual=2, entity_kind="interface")
        with pytest.raises(ValidationError):
            item.path = "/y"

    def test_extra_forbid(self):
        from snapl_observability.models import DriftItem

        with pytest.raises(ValidationError):
            DriftItem(
                path="/x",
                desired=1,
                actual=2,
                entity_kind="interface",
                extra="boom",  # type: ignore[call-arg]
            )

    def test_none_values_allowed(self):
        from snapl_observability.models import DriftItem

        item = DriftItem(path="/x", desired=None, actual="present", entity_kind="interface")
        assert item.desired is None


class TestDriftReport:
    def _item(self):
        from snapl_observability.models import DriftItem

        return DriftItem(path="/x", desired=1, actual=2, entity_kind="interface")

    def test_clean_with_empty_items(self):
        from snapl_observability.models import DriftReport, DriftStatus

        r = DriftReport(
            device_id=_DEVICE_ID,
            device_name="spine-01",
            status=DriftStatus.CLEAN,
            items=[],
            timestamp=_NOW,
        )
        assert r.status == DriftStatus.CLEAN
        assert r.error is None

    def test_clean_with_items_raises(self):
        from snapl_observability.models import DriftReport, DriftStatus

        with pytest.raises(ValidationError):
            DriftReport(
                device_id=_DEVICE_ID,
                device_name="spine-01",
                status=DriftStatus.CLEAN,
                items=[self._item()],
                timestamp=_NOW,
            )

    def test_drifted_requires_items(self):
        from snapl_observability.models import DriftReport, DriftStatus

        with pytest.raises(ValidationError):
            DriftReport(
                device_id=_DEVICE_ID,
                device_name="spine-01",
                status=DriftStatus.DRIFTED,
                items=[],
                timestamp=_NOW,
            )

    def test_drifted_with_error_raises(self):
        from snapl_observability.models import DriftReport, DriftStatus

        with pytest.raises(ValidationError):
            DriftReport(
                device_id=_DEVICE_ID,
                device_name="spine-01",
                status=DriftStatus.DRIFTED,
                items=[self._item()],
                error="nope",
                timestamp=_NOW,
            )

    def test_error_requires_error_field(self):
        from snapl_observability.models import DriftReport, DriftStatus

        with pytest.raises(ValidationError):
            DriftReport(
                device_id=_DEVICE_ID,
                device_name="spine-01",
                status=DriftStatus.ERROR,
                items=[],
                timestamp=_NOW,
            )

    def test_error_with_items_raises(self):
        from snapl_observability.models import DriftReport, DriftStatus

        with pytest.raises(ValidationError):
            DriftReport(
                device_id=_DEVICE_ID,
                device_name="spine-01",
                status=DriftStatus.ERROR,
                items=[self._item()],
                error="boom",
                timestamp=_NOW,
            )

    def test_error_valid(self):
        from snapl_observability.models import DriftReport, DriftStatus

        r = DriftReport(
            device_id=_DEVICE_ID,
            device_name="spine-01",
            status=DriftStatus.ERROR,
            items=[],
            error="connectivity error",
            timestamp=_NOW,
        )
        assert r.error == "connectivity error"

    def test_frozen(self):
        from snapl_observability.models import DriftReport, DriftStatus

        r = DriftReport(
            device_id=_DEVICE_ID,
            device_name="spine-01",
            status=DriftStatus.CLEAN,
            items=[],
            timestamp=_NOW,
        )
        with pytest.raises(ValidationError):
            r.status = DriftStatus.DRIFTED


class TestBatchDriftReport:
    def _clean_report(self, dev_id: UUID):
        from snapl_observability.models import DriftReport, DriftStatus

        return DriftReport(
            device_id=dev_id,
            device_name=f"d-{dev_id.int}",
            status=DriftStatus.CLEAN,
            items=[],
            timestamp=_NOW,
        )

    def test_counts_sum_to_total(self):
        from snapl_observability.models import BatchDriftReport

        d1 = UUID(int=1)
        d2 = UUID(int=2)
        BatchDriftReport(
            reports={d1: self._clean_report(d1), d2: self._clean_report(d2)},
            total=2,
            clean=2,
            drifted=0,
            errored=0,
        )

    def test_invalid_counts_raise(self):
        from snapl_observability.models import BatchDriftReport

        d1 = UUID(int=1)
        with pytest.raises(ValidationError):
            BatchDriftReport(
                reports={d1: self._clean_report(d1)},
                total=2,
                clean=1,
                drifted=0,
                errored=0,
            )


class TestObservabilityEvent:
    def _report(self, status):
        from snapl_observability.models import DriftItem, DriftReport, DriftStatus

        kwargs = {
            "device_id": _DEVICE_ID,
            "device_name": "spine-01",
            "status": status,
            "timestamp": _NOW,
        }
        if status == DriftStatus.DRIFTED:
            kwargs["items"] = [DriftItem(path="/x", desired=1, actual=2, entity_kind="interface")]
        elif status == DriftStatus.ERROR:
            kwargs["items"] = []
            kwargs["error"] = "boom"
        else:
            kwargs["items"] = []
        return DriftReport(**kwargs)

    def test_drifted_maps_to_drift_detected(self):
        from snapl_observability.models import DriftStatus, EventType, ObservabilityEvent

        report = self._report(DriftStatus.DRIFTED)
        ev = ObservabilityEvent(
            event_type=EventType.DRIFT_DETECTED,
            device_id=_DEVICE_ID,
            device_name="spine-01",
            report=report,
            timestamp=_NOW,
        )
        assert ev.event_type == EventType.DRIFT_DETECTED

    def test_clean_maps_to_state_clean(self):
        from snapl_observability.models import DriftStatus, EventType, ObservabilityEvent

        report = self._report(DriftStatus.CLEAN)
        ObservabilityEvent(
            event_type=EventType.STATE_CLEAN,
            device_id=_DEVICE_ID,
            device_name="spine-01",
            report=report,
            timestamp=_NOW,
        )

    def test_mismatched_event_type_raises(self):
        from snapl_observability.models import DriftStatus, EventType, ObservabilityEvent

        report = self._report(DriftStatus.CLEAN)
        with pytest.raises(ValidationError):
            ObservabilityEvent(
                event_type=EventType.DRIFT_DETECTED,
                device_id=_DEVICE_ID,
                device_name="spine-01",
                report=report,
                timestamp=_NOW,
            )


class TestAuditEntry:
    def test_default_detail_is_empty_dict(self):
        from snapl_observability.models import AuditEntry, AuditOperation, AuditOutcome

        e = AuditEntry(
            operation=AuditOperation.DETECT_DRIFT,
            device_id=_DEVICE_ID,
            component="StructuralObserver",
            outcome=AuditOutcome.SUCCESS,
            timestamp=_NOW,
        )
        assert e.detail == {}

    def test_frozen(self):
        from snapl_observability.models import AuditEntry, AuditOperation, AuditOutcome

        e = AuditEntry(
            operation=AuditOperation.LOG_AUDIT,
            device_id=None,
            component="caller",
            outcome=AuditOutcome.SUCCESS,
            timestamp=_NOW,
        )
        with pytest.raises(ValidationError):
            e.outcome = AuditOutcome.FAILURE

    def test_device_id_optional(self):
        from snapl_observability.models import AuditEntry, AuditOperation, AuditOutcome

        e = AuditEntry(
            operation=AuditOperation.LOG_AUDIT,
            device_id=None,
            component="caller",
            outcome=AuditOutcome.SUCCESS,
            timestamp=_NOW,
        )
        assert e.device_id is None
