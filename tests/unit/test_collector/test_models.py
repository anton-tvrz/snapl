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


# ---------------------------------------------------------------------------
# Spec 016 T001 — telemetry models
# ---------------------------------------------------------------------------


class TestTelemetryTarget:
    def test_equal_when_the_same_device_and_items_are_named(self, make_device):
        from snapl_collector.models import TelemetryTarget

        device = make_device("leaf-01", device_id=_DEVICE_ID)
        a = TelemetryTarget(device=device, interfaces=("ethernet-1/49", "lo0"), peers=("10.10.1.0",))
        b = TelemetryTarget(device=device, interfaces=("lo0", "ethernet-1/49"), peers=("10.10.1.0",))
        assert a == b

    def test_differs_when_an_item_changes(self, make_device):
        from snapl_collector.models import TelemetryTarget

        device = make_device("leaf-01", device_id=_DEVICE_ID)
        a = TelemetryTarget(device=device, interfaces=("ethernet-1/49",), peers=())
        b = TelemetryTarget(device=device, interfaces=("ethernet-1/49", "ethernet-1/50"), peers=())
        c = TelemetryTarget(device=device, interfaces=("ethernet-1/49",), peers=("10.10.1.0",))
        assert a != b
        assert a != c

    def test_differs_when_the_device_changes(self, make_device):
        from snapl_collector.models import TelemetryTarget

        a = TelemetryTarget(device=make_device("leaf-01", device_id=_DEVICE_ID), interfaces=("lo0",), peers=())
        b = TelemetryTarget(device=make_device("leaf-02", device_id=_DEVICE_ID), interfaces=("lo0",), peers=())
        assert a != b

    def test_items_are_held_sorted_and_once(self, make_device):
        from snapl_collector.models import TelemetryTarget

        target = TelemetryTarget(
            device=make_device("leaf-01"),
            interfaces=("lo0", "ethernet-1/49", "lo0"),
            peers=("10.10.2.0", "10.10.1.0"),
        )
        assert target.interfaces == ("ethernet-1/49", "lo0")
        assert target.peers == ("10.10.1.0", "10.10.2.0")

    def test_is_frozen(self, make_device):
        from dataclasses import FrozenInstanceError

        from snapl_collector.models import TelemetryTarget

        target = TelemetryTarget(device=make_device("leaf-01"), interfaces=(), peers=())
        with pytest.raises(FrozenInstanceError):
            target.interfaces = ("lo0",)  # type: ignore[misc]


class TestTelemetrySamples:
    def test_interface_sample_accepts_none_for_every_value(self):
        from snapl_collector.models import InterfaceSample

        s = InterfaceSample(
            name="lo0",
            oper_up=None,
            in_octets=None,
            out_octets=None,
            in_errors=None,
            out_errors=None,
            in_discards=None,
            out_discards=None,
        )
        assert s.oper_up is None
        assert s.in_octets is None

    def test_interface_sample_values_default_to_none(self):
        from snapl_collector.models import InterfaceSample

        s = InterfaceSample(name="ethernet-1/49", oper_up=True)
        assert (s.in_octets, s.out_octets, s.in_errors, s.out_errors, s.in_discards, s.out_discards) == (None,) * 6

    def test_bgp_session_sample_accepts_none(self):
        from snapl_collector.models import BgpSessionSample

        s = BgpSessionSample(peer="10.10.1.1", state=None, prefixes_received=None)
        assert s.state is None
        assert s.prefixes_received is None

    def test_platform_sample_accepts_none(self):
        from snapl_collector.models import PlatformSample

        s = PlatformSample(cpu_percent=None, memory_percent=None)
        assert s.cpu_percent is None
        assert s.memory_percent is None

    def test_telemetry_sample_holds_items(self):
        from datetime import datetime

        from snapl_collector.models import BgpSessionSample, InterfaceSample, PlatformSample, TelemetrySample

        ts = datetime(2026, 11, 19, tzinfo=UTC)
        s = TelemetrySample(
            device_name="spine-01",
            timestamp=ts,
            interfaces=(InterfaceSample(name="ethernet-1/1", oper_up=True),),
            sessions=(BgpSessionSample(peer="10.10.1.1", state="active", prefixes_received=0),),
            platform=PlatformSample(cpu_percent=5, memory_percent=94),
            sync=True,
        )
        assert s.timestamp == ts
        assert s.sync is True
        assert s.platform is not None

    def test_telemetry_sample_accepts_no_platform(self):
        from datetime import datetime

        from snapl_collector.models import TelemetrySample

        s = TelemetrySample(
            device_name="leaf-01",
            timestamp=datetime(2026, 11, 19, tzinfo=UTC),
            interfaces=(),
            sessions=(),
            platform=None,
            sync=False,
        )
        assert s.platform is None

    @pytest.mark.parametrize("name", ["InterfaceSample", "BgpSessionSample", "PlatformSample", "TelemetrySample"])
    def test_samples_are_frozen(self, name):
        import dataclasses

        from snapl_collector import models

        cls = getattr(models, name)
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen  # type: ignore[attr-defined]

    def test_models_are_exported_from_the_package(self):
        import snapl_collector

        for name in ("TelemetryTarget", "InterfaceSample", "BgpSessionSample", "PlatformSample", "TelemetrySample"):
            assert hasattr(snapl_collector, name), name
