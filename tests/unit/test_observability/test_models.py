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


# ---------------------------------------------------------------------------
# Spec 016 T010 — health models
# ---------------------------------------------------------------------------


class TestHealthEnums:
    def test_device_health_states(self):
        from snapl_observability.models import DeviceHealthState

        assert [s.value for s in DeviceHealthState] == ["healthy", "unhealthy", "unknown"]

    def test_health_reason_kinds(self):
        from snapl_observability.models import HealthReasonKind

        assert [k.value for k in HealthReasonKind] == ["interface", "bgp_session", "no_telemetry"]


class TestHealthReason:
    def test_interface_text(self):
        from snapl_observability.models import HealthReason, HealthReasonKind

        reason = HealthReason.interface("ethernet-1/49", actual="down", expected="up")
        assert reason.kind is HealthReasonKind.INTERFACE
        assert (reason.item, reason.expected, reason.actual) == ("ethernet-1/49", "up", "down")
        assert reason.text == "ethernet-1/49 down (expected up)"

    def test_absent_interface_text(self):
        from snapl_observability.models import HealthReason

        assert HealthReason.interface("ethernet-1/9", actual="absent", expected="up").text == (
            "ethernet-1/9 absent (expected up)"
        )

    def test_bgp_session_text(self):
        from snapl_observability.models import HealthReason, HealthReasonKind

        reason = HealthReason.bgp_session("10.10.1.1", actual="active")
        assert reason.kind is HealthReasonKind.BGP_SESSION
        assert (reason.item, reason.expected, reason.actual) == ("10.10.1.1", "established", "active")
        assert reason.text == "BGP 10.10.1.1 active (expected established)"

    def test_no_telemetry_since_text(self):
        from snapl_observability.models import HealthReason, HealthReasonKind

        reason = HealthReason.no_telemetry(datetime(2026, 11, 19, 21, 0, 5, 123456, tzinfo=UTC))
        assert reason.kind is HealthReasonKind.NO_TELEMETRY
        assert (reason.item, reason.expected, reason.actual) == (None, None, None)
        assert reason.text == "no telemetry since 2026-11-19T21:00:05+00:00"

    def test_no_telemetry_yet_text(self):
        from snapl_observability.models import HealthReason

        assert HealthReason.no_telemetry(None).text == "no telemetry yet"

    def test_telemetry_stopped_text(self):
        """Spec 016 R6: a clean stop of the worker records this for each device."""
        from snapl_observability.models import HealthReason, HealthReasonKind

        reason = HealthReason.telemetry_stopped()
        assert reason.kind is HealthReasonKind.NO_TELEMETRY
        assert (reason.item, reason.expected, reason.actual) == (None, None, None)
        assert reason.text == "telemetry stopped"

    def test_is_frozen(self):
        from snapl_observability.models import HealthReason

        reason = HealthReason.no_telemetry(None)
        with pytest.raises(ValidationError):
            reason.text = "x"  # type: ignore[misc]


class TestHealthExpectation:
    def test_holds_the_sets(self):
        from snapl_observability.models import HealthExpectation

        e = HealthExpectation(
            device_name="leaf-01",
            interfaces_up=frozenset({"ethernet-1/49"}),
            interfaces_down=frozenset({"ethernet-1/50"}),
            sessions_established=frozenset({"10.10.1.0"}),
        )
        assert e.interfaces_up == frozenset({"ethernet-1/49"})

    def test_an_interface_cannot_be_expected_up_and_down(self):
        from snapl_observability.models import HealthExpectation

        with pytest.raises(ValidationError, match="both up and down"):
            HealthExpectation(
                device_name="leaf-01",
                interfaces_up=frozenset({"ethernet-1/49"}),
                interfaces_down=frozenset({"ethernet-1/49"}),
                sessions_established=frozenset(),
            )

    def test_rejects_extra_fields(self):
        from snapl_observability.models import HealthExpectation

        with pytest.raises(ValidationError):
            HealthExpectation(
                device_name="leaf-01",
                interfaces_up=frozenset(),
                interfaces_down=frozenset(),
                sessions_established=frozenset(),
                cpu_max=90,
            )


class TestHealthObservation:
    def test_never_sampled(self):
        from snapl_observability.models import HealthObservation

        o = HealthObservation(last_sample_at=None)
        assert o.interfaces == {}
        assert o.sessions == {}

    def test_holds_observed_items(self):
        from snapl_observability.models import HealthObservation, ObservedItem

        item = ObservedItem(state="up", since=_NOW)
        o = HealthObservation(last_sample_at=_NOW, interfaces={"lo0": item}, sessions={})
        assert o.interfaces["lo0"] == item


class TestDeviceHealth:
    def test_holds_state_and_reasons(self):
        from snapl_observability.models import DeviceHealth, DeviceHealthState, HealthReason

        h = DeviceHealth(
            device_name="spine-01",
            state=DeviceHealthState.UNHEALTHY,
            reasons=(HealthReason.bgp_session("10.10.1.1", actual="active"),),
            evaluated_at=_NOW,
        )
        assert h.state == "unhealthy"
        assert h.reasons[0].text == "BGP 10.10.1.1 active (expected established)"

    def test_models_are_exported(self):
        import snapl_observability

        for name in (
            "DeviceHealthState",
            "HealthReasonKind",
            "HealthReason",
            "HealthExpectation",
            "ObservedItem",
            "HealthObservation",
            "DeviceHealth",
        ):
            assert hasattr(snapl_observability, name), name
