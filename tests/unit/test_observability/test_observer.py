"""Unit tests for StructuralObserver (T016, T017)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

pytestmark = pytest.mark.unit


def _matching_actual_data(desired):
    iface = desired.interfaces[0]
    bgp = desired.bgp_sessions[0]
    return {
        f"/interface[name={iface.name}]": {
            "description": iface.description,
            "ip_address": iface.ip_address,
            "prefix_length": iface.prefix_length,
            "enabled": iface.enabled,
            "mtu": iface.mtu,
        },
        f"/network-instance[name=default]/protocols/bgp/neighbor[peer-address={bgp.peer_address}]": {
            "peer_address": bgp.peer_address,
            "peer_asn": bgp.peer_asn,
            "peer_group": bgp.peer_group,
            "enabled": bgp.enabled,
        },
    }


def _build_desired(name="spine-01", device_id=None, mtu=9000):
    from snapl_intent.models import BGPSession, DesiredState, Device, Interface

    dev_id = device_id or UUID("00000000-0000-0000-0000-000000000001")
    device = Device(
        id=dev_id,
        name=name,
        management_address="10.0.0.1",
        role="spine",
        use_case="dcfabric",
        platform="nokia-srlinux",
        description="spine node",
    )
    iface = Interface(
        id=uuid4(),
        device_id=dev_id,
        name="ethernet-1/1",
        description="link",
        ip_address="10.1.1.0",
        prefix_length=31,
        enabled=True,
        mtu=mtu,
    )
    bgp = BGPSession(
        id=uuid4(),
        device_id=dev_id,
        local_asn=65000,
        peer_address="10.1.1.1",
        peer_asn=65001,
        peer_group="underlay-ipv4",
        enabled=True,
    )
    return DesiredState(device=device, interfaces=[iface], bgp_sessions=[bgp])


def _collect_result(device_id, *, success=True, data=None, error=None):
    from snapl_collector.models import CollectResult

    return CollectResult(
        device_id=device_id,
        device_name="spine-01",
        success=success,
        data=data if data is not None else {},
        paths=["/"],
        error=error,
    )


# ---------------------------------------------------------------------------
# T016: detect_drift
# ---------------------------------------------------------------------------


class TestDetectDriftClean:
    @pytest.mark.asyncio
    async def test_matching_state_is_clean(self):
        from snapl_observability.models import DriftStatus
        from snapl_observability.structural.observer import StructuralObserver

        desired = _build_desired()
        actual = _collect_result(desired.device.id, data=_matching_actual_data(desired))
        obs = StructuralObserver()
        report = await obs.detect_drift(desired, actual)
        assert report.status == DriftStatus.CLEAN
        assert report.items == []
        assert report.error is None
        assert report.device_id == desired.device.id


class TestDetectDriftDrifted:
    @pytest.mark.asyncio
    async def test_one_field_mismatch_drifted(self):
        from snapl_observability.models import DriftStatus
        from snapl_observability.structural.observer import StructuralObserver

        desired = _build_desired(mtu=9000)
        data = _matching_actual_data(desired)
        data["/interface[name=ethernet-1/1]"]["mtu"] = 1500
        actual = _collect_result(desired.device.id, data=data)
        obs = StructuralObserver()
        report = await obs.detect_drift(desired, actual)
        assert report.status == DriftStatus.DRIFTED
        assert len(report.items) == 1
        assert report.error is None


class TestDetectDriftError:
    @pytest.mark.asyncio
    async def test_failed_collect_result_yields_error_status(self):
        from snapl_observability.models import DriftStatus
        from snapl_observability.structural.observer import StructuralObserver

        desired = _build_desired()
        actual = _collect_result(desired.device.id, success=False, error="connectivity error: refused")
        obs = StructuralObserver()
        report = await obs.detect_drift(desired, actual)
        assert report.status == DriftStatus.ERROR
        assert report.items == []
        assert report.error == "connectivity error: refused"


class TestDetectDriftValidation:
    @pytest.mark.asyncio
    async def test_mismatched_device_ids_raise(self):
        from snapl_observability.structural.observer import StructuralObserver

        desired = _build_desired(device_id=UUID(int=1))
        actual = _collect_result(UUID(int=2))
        obs = StructuralObserver()
        with pytest.raises(ValueError, match=r"device.*id"):
            await obs.detect_drift(desired, actual)


class TestDetectDriftAuditSideEffect:
    @pytest.mark.asyncio
    async def test_clean_appends_audit_entry(self):
        from snapl_observability.audit import AuditLog
        from snapl_observability.models import AuditOperation, AuditOutcome
        from snapl_observability.structural.observer import StructuralObserver

        log = AuditLog()
        desired = _build_desired()
        actual = _collect_result(desired.device.id, data=_matching_actual_data(desired))
        obs = StructuralObserver(audit_log=log)
        await obs.detect_drift(desired, actual)
        entries = log.query_by_device(desired.device.id)
        assert len(entries) == 1
        assert entries[0].operation == AuditOperation.DETECT_DRIFT
        assert entries[0].outcome == AuditOutcome.SUCCESS

    @pytest.mark.asyncio
    async def test_error_status_still_audits_as_success(self):
        """The detect_drift operation succeeded — error came from upstream Collector."""
        from snapl_observability.audit import AuditLog
        from snapl_observability.models import AuditOutcome
        from snapl_observability.structural.observer import StructuralObserver

        log = AuditLog()
        desired = _build_desired()
        actual = _collect_result(desired.device.id, success=False, error="boom")
        obs = StructuralObserver(audit_log=log)
        await obs.detect_drift(desired, actual)
        entries = log.query_by_device(desired.device.id)
        assert len(entries) == 1
        assert entries[0].outcome == AuditOutcome.SUCCESS


# ---------------------------------------------------------------------------
# T017: detect_drift_batch
# ---------------------------------------------------------------------------


class TestDetectDriftBatch:
    @pytest.mark.asyncio
    async def test_three_clean_pairs(self):
        from snapl_observability.models import DriftStatus
        from snapl_observability.structural.observer import StructuralObserver

        pairs = []
        for i in range(3):
            d = _build_desired(name=f"spine-{i}", device_id=UUID(int=i + 1))
            a = _collect_result(d.device.id, data=_matching_actual_data(d))
            pairs.append((d, a))
        obs = StructuralObserver()
        batch = await obs.detect_drift_batch(pairs)
        assert batch.total == 3
        assert batch.clean == 3
        assert batch.drifted == 0
        assert batch.errored == 0
        for _, report in batch.reports.items():
            assert report.status == DriftStatus.CLEAN

    @pytest.mark.asyncio
    async def test_mixed_outcomes(self):
        from snapl_observability.structural.observer import StructuralObserver

        # one clean
        d1 = _build_desired(name="a", device_id=UUID(int=1))
        a1 = _collect_result(d1.device.id, data=_matching_actual_data(d1))
        # one drifted
        d2 = _build_desired(name="b", device_id=UUID(int=2))
        data2 = _matching_actual_data(d2)
        data2["/interface[name=ethernet-1/1]"]["mtu"] = 1500
        a2 = _collect_result(d2.device.id, data=data2)
        # one errored
        d3 = _build_desired(name="c", device_id=UUID(int=3))
        a3 = _collect_result(d3.device.id, success=False, error="x")

        obs = StructuralObserver()
        batch = await obs.detect_drift_batch([(d1, a1), (d2, a2), (d3, a3)])
        assert batch.total == 3
        assert batch.clean == 1
        assert batch.drifted == 1
        assert batch.errored == 1

    @pytest.mark.asyncio
    async def test_empty_pairs_raises(self):
        from snapl_observability.structural.observer import StructuralObserver

        obs = StructuralObserver()
        with pytest.raises(ValueError, match=r"non-empty"):
            await obs.detect_drift_batch([])

    @pytest.mark.asyncio
    async def test_mismatched_pair_raises_before_diff(self):
        from snapl_observability.structural.observer import StructuralObserver

        d = _build_desired(device_id=UUID(int=1))
        a = _collect_result(UUID(int=999))
        obs = StructuralObserver()
        with pytest.raises(ValueError, match=r"device.*id"):
            await obs.detect_drift_batch([(d, a)])

    @pytest.mark.asyncio
    async def test_one_audit_entry_per_pair(self):
        from snapl_observability.audit import AuditLog
        from snapl_observability.structural.observer import StructuralObserver

        log = AuditLog()
        pairs = []
        for i in range(3):
            d = _build_desired(name=f"x-{i}", device_id=UUID(int=i + 100))
            a = _collect_result(d.device.id, data=_matching_actual_data(d))
            pairs.append((d, a))
        obs = StructuralObserver(audit_log=log)
        await obs.detect_drift_batch(pairs)
        assert len(log) == 3


# ---------------------------------------------------------------------------
# T021: emit_event
# ---------------------------------------------------------------------------


async def _drifted_report(observer):
    desired = _build_desired(mtu=9000)
    data = _matching_actual_data(desired)
    data["/interface[name=ethernet-1/1]"]["mtu"] = 1500
    actual = _collect_result(desired.device.id, data=data)
    return await observer.detect_drift(desired, actual)


async def _clean_report(observer):
    desired = _build_desired()
    actual = _collect_result(desired.device.id, data=_matching_actual_data(desired))
    return await observer.detect_drift(desired, actual)


async def _error_report(observer):
    desired = _build_desired()
    actual = _collect_result(desired.device.id, success=False, error="boom")
    return await observer.detect_drift(desired, actual)


class TestEmitEvent:
    @pytest.mark.asyncio
    async def test_drifted_emits_drift_detected(self):
        from snapl_observability.models import EventType
        from snapl_observability.structural.observer import StructuralObserver

        obs = StructuralObserver()
        report = await _drifted_report(obs)
        event = await obs.emit_event(report)
        assert event.event_type == EventType.DRIFT_DETECTED
        assert event.device_id == report.device_id
        assert event.device_name == report.device_name
        assert event.report is report

    @pytest.mark.asyncio
    async def test_clean_emits_state_clean(self):
        from snapl_observability.models import EventType
        from snapl_observability.structural.observer import StructuralObserver

        obs = StructuralObserver()
        report = await _clean_report(obs)
        event = await obs.emit_event(report)
        assert event.event_type == EventType.STATE_CLEAN

    @pytest.mark.asyncio
    async def test_error_emits_drift_error(self):
        from snapl_observability.models import EventType
        from snapl_observability.structural.observer import StructuralObserver

        obs = StructuralObserver()
        report = await _error_report(obs)
        event = await obs.emit_event(report)
        assert event.event_type == EventType.DRIFT_ERROR

    @pytest.mark.asyncio
    async def test_dispatches_to_handlers_in_order(self):
        from snapl_observability.events import EventBus
        from snapl_observability.structural.observer import StructuralObserver

        bus = EventBus()
        order = []
        bus.register(lambda ev: order.append(("h1", ev.event_type)))
        bus.register(lambda ev: order.append(("h2", ev.event_type)))
        obs = StructuralObserver(event_bus=bus)
        report = await _drifted_report(obs)
        await obs.emit_event(report)
        assert [name for name, _ in order] == ["h1", "h2"]

    @pytest.mark.asyncio
    async def test_handler_failure_does_not_block_subsequent(self):
        from snapl_observability.events import EventBus
        from snapl_observability.structural.observer import StructuralObserver

        bus = EventBus()
        called = []

        def bad(ev):
            raise RuntimeError("boom")

        def good(ev):
            called.append(ev)

        bus.register(bad)
        bus.register(good)
        obs = StructuralObserver(event_bus=bus)
        report = await _clean_report(obs)
        await obs.emit_event(report)
        assert len(called) == 1

    @pytest.mark.asyncio
    async def test_audit_entry_appended_for_emit(self):
        from snapl_observability.audit import AuditLog
        from snapl_observability.models import AuditOperation, AuditOutcome
        from snapl_observability.structural.observer import StructuralObserver

        log = AuditLog()
        obs = StructuralObserver(audit_log=log)
        report = await _clean_report(obs)
        # detect_drift produced one audit entry; emit_event should add a second
        baseline = len(log)
        await obs.emit_event(report)
        all_entries = log.all()
        emit_entries = [e for e in all_entries if e.operation == AuditOperation.EMIT_EVENT]
        assert len(emit_entries) == 1
        assert emit_entries[0].outcome == AuditOutcome.SUCCESS
        assert emit_entries[0].device_id == report.device_id
        assert len(log) == baseline + 1


# ---------------------------------------------------------------------------
# T023: log_audit
# ---------------------------------------------------------------------------


def _audit_entry(device_id, component="orchestrator.workflow"):
    from datetime import UTC, datetime

    from snapl_observability.models import AuditEntry, AuditOperation, AuditOutcome

    return AuditEntry(
        operation=AuditOperation.DETECT_DRIFT,
        device_id=device_id,
        component=component,
        outcome=AuditOutcome.SUCCESS,
        timestamp=datetime.now(tz=UTC),
    )


class TestLogAudit:
    @pytest.mark.asyncio
    async def test_log_audit_appends_entry_verbatim(self):
        from snapl_observability.audit import AuditLog
        from snapl_observability.structural.observer import StructuralObserver

        log = AuditLog()
        obs = StructuralObserver(audit_log=log)
        dev = UUID(int=1)
        entry = _audit_entry(dev)
        await obs.log_audit(entry)
        result = log.query_by_device(dev)
        assert len(result) == 1
        assert result[0] is entry

    @pytest.mark.asyncio
    async def test_multiple_log_audit_chronological(self):
        from snapl_observability.audit import AuditLog
        from snapl_observability.structural.observer import StructuralObserver

        log = AuditLog()
        obs = StructuralObserver(audit_log=log)
        dev = UUID(int=1)
        await obs.log_audit(_audit_entry(dev, component="a"))
        await obs.log_audit(_audit_entry(dev, component="b"))
        result = log.query_by_device(dev)
        assert len(result) == 2
        components = [e.component for e in result]
        assert "a" in components
        assert "b" in components

    @pytest.mark.asyncio
    async def test_arbitrary_component_name(self):
        from snapl_observability.audit import AuditLog
        from snapl_observability.structural.observer import StructuralObserver

        log = AuditLog()
        obs = StructuralObserver(audit_log=log)
        dev = UUID(int=42)
        await obs.log_audit(_audit_entry(dev, component="some.external.thing"))
        result = log.query_by_device(dev)
        assert result[0].component == "some.external.thing"

    @pytest.mark.asyncio
    async def test_audit_log_accumulates_from_all_three_sources(self):
        """detect_drift + emit_event + log_audit all share the same AuditLog."""
        from snapl_observability.audit import AuditLog
        from snapl_observability.models import AuditOperation
        from snapl_observability.structural.observer import StructuralObserver

        log = AuditLog()
        obs = StructuralObserver(audit_log=log)
        report = await _clean_report(obs)  # 1 detect_drift entry
        await obs.emit_event(report)  # 1 emit_event entry
        await obs.log_audit(_audit_entry(report.device_id))  # 1 log_audit-driven entry
        all_entries = log.all()
        ops = [e.operation for e in all_entries]
        assert AuditOperation.DETECT_DRIFT in ops
        assert AuditOperation.EMIT_EVENT in ops
        assert len(all_entries) == 3


# ---------------------------------------------------------------------------
# T025/T026: performance and batch-scale assertions (Phase 6 polish)
# ---------------------------------------------------------------------------


def _build_large_desired(*, n_interfaces=10, n_bgp=5):
    """A DesiredState with many entities — used for SC-001 perf assertion."""
    from snapl_intent.models import BGPSession, DesiredState, Device, Interface

    dev_id = UUID("00000000-0000-0000-0000-0000000000aa")
    device = Device(
        id=dev_id,
        name="spine-01",
        management_address="10.0.0.1",
        role="spine",
        use_case="dcfabric",
        platform="nokia-srlinux",
    )
    interfaces = [
        Interface(
            id=uuid4(),
            device_id=dev_id,
            name=f"ethernet-1/{i}",
            ip_address=f"10.1.{i}.0",
            prefix_length=31,
            enabled=True,
            mtu=9232,
        )
        for i in range(1, n_interfaces + 1)
    ]
    bgp_sessions = [
        BGPSession(
            id=uuid4(),
            device_id=dev_id,
            local_asn=65000,
            peer_address=f"10.1.{i}.1",
            peer_asn=65000 + i,
            peer_group="underlay-ipv4",
            enabled=True,
        )
        for i in range(1, n_bgp + 1)
    ]
    return DesiredState(device=device, interfaces=interfaces, bgp_sessions=bgp_sessions)


class TestPerformance:
    @pytest.mark.asyncio
    async def test_sc001_detect_drift_under_100ms(self):
        """SC-001: detect_drift completes in <100 ms with 10 interfaces and 5 BGP sessions."""
        import time

        from snapl_observability.structural.observer import StructuralObserver

        desired = _build_large_desired(n_interfaces=10, n_bgp=5)
        # Empty actual data → every field drifts (worst case)
        actual = _collect_result(desired.device.id, data={})
        obs = StructuralObserver()
        start = time.perf_counter()
        await obs.detect_drift(desired, actual)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 100, f"detect_drift took {elapsed_ms:.2f}ms, expected <100ms"

    @pytest.mark.asyncio
    async def test_sc005_batch_returns_result_for_every_device(self):
        """SC-005: 10-device batch with mixed outcomes returns a result for each."""
        from snapl_observability.structural.observer import StructuralObserver

        pairs = []
        for i in range(10):
            d = _build_desired(name=f"d-{i}", device_id=UUID(int=1000 + i))
            if i < 4:
                # clean
                a = _collect_result(d.device.id, data=_matching_actual_data(d))
            elif i < 8:
                # drifted
                data = _matching_actual_data(d)
                data["/interface[name=ethernet-1/1]"]["mtu"] = 1500
                a = _collect_result(d.device.id, data=data)
            else:
                # errored
                a = _collect_result(d.device.id, success=False, error="boom")
            pairs.append((d, a))

        obs = StructuralObserver()
        batch = await obs.detect_drift_batch(pairs)
        assert batch.total == 10
        assert batch.clean == 4
        assert batch.drifted == 4
        assert batch.errored == 2
        for desired, _ in pairs:
            assert desired.device.id in batch.reports
