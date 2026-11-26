"""The worker's metrics endpoint settings and runtime (spec 012, T005, research R3).

Every test binds its own free port. 18091 is the maintainer's worker's port and
is never touched here.
"""

from __future__ import annotations

import itertools
import logging
import socket

import pytest
from temporalio.runtime import Runtime

from snapl_orchestrator.exceptions import OrchestratorConfigError
from snapl_orchestrator.worker.metrics import (
    DEFAULT_WORKER_METRICS_BIND,
    DEFAULT_WORKER_METRICS_PORT,
    MetricsSettings,
    build_runtime,
    metrics_settings_from_env,
)

pytestmark = pytest.mark.unit


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestSettingsFromEnv:
    def test_defaults_match_the_host_resource_registry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WORKER_METRICS_PORT", raising=False)
        monkeypatch.delenv("WORKER_METRICS_BIND", raising=False)

        settings = metrics_settings_from_env()

        assert settings == MetricsSettings(port=18091, bind="127.0.0.1")
        assert (DEFAULT_WORKER_METRICS_PORT, DEFAULT_WORKER_METRICS_BIND) == (18091, "127.0.0.1")

    def test_reads_port_and_bind(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WORKER_METRICS_PORT", "28091")
        monkeypatch.setenv("WORKER_METRICS_BIND", "0.0.0.0")  # noqa: S104 — a value under test, not a bind

        assert metrics_settings_from_env() == MetricsSettings(port=28091, bind="0.0.0.0")  # noqa: S104

    @pytest.mark.parametrize("raw", ["off", "OFF", " off "])
    def test_off_disables_metrics(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv("WORKER_METRICS_PORT", raw)

        assert metrics_settings_from_env().port == "off"

    @pytest.mark.parametrize("raw", ["nine", "18091x", "0", "65536", "-1"])
    def test_rejects_a_port_that_is_neither_a_port_nor_off(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv("WORKER_METRICS_PORT", raw)

        with pytest.raises(OrchestratorConfigError, match="WORKER_METRICS_PORT"):
            metrics_settings_from_env()

    def test_empty_values_fall_back_to_the_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WORKER_METRICS_PORT", "")
        monkeypatch.setenv("WORKER_METRICS_BIND", "")

        assert metrics_settings_from_env() == MetricsSettings(port=18091, bind="127.0.0.1")


class TestBuildRuntime:
    def test_returns_a_runtime_on_a_free_port(self) -> None:
        port = _free_port()

        runtime = build_runtime(port=port, bind="127.0.0.1")

        assert isinstance(runtime, Runtime)
        # The exporter is listening: a second bind on the same port fails.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe, pytest.raises(OSError, match="in use"):
            probe.bind(("127.0.0.1", port))

    def test_a_taken_port_returns_none_and_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
            holder.bind(("127.0.0.1", 0))
            holder.listen()
            port = holder.getsockname()[1]

            with caplog.at_level(logging.WARNING, logger="snapl_orchestrator.worker.metrics"):
                runtime = build_runtime(port=port, bind="127.0.0.1")

        assert runtime is None
        [record] = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert record.getMessage() == (f"metrics off: port {port} in use (set WORKER_METRICS_PORT to move it, or off)")

    def test_off_returns_none_without_binding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import snapl_orchestrator.worker.metrics as metrics_mod

        def _no_runtime(*_args: object, **_kwargs: object) -> Runtime:
            raise AssertionError("off must not build a runtime")

        monkeypatch.setattr(metrics_mod, "Runtime", _no_runtime)

        assert build_runtime(port="off", bind="127.0.0.1") is None

    def test_an_unusable_bind_address_returns_none_and_says_why(self, caplog: pytest.LogCaptureFixture) -> None:
        """Any exporter failure, not just a taken port, leaves the worker running (FR-002)."""
        with caplog.at_level(logging.WARNING, logger="snapl_orchestrator.worker.metrics"):
            runtime = build_runtime(port=_free_port(), bind="not-an-ip")

        assert runtime is None
        [record] = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert record.getMessage().startswith("metrics off: could not serve on not-an-ip:")


class TestHistogramBuckets:
    """The runtime's default buckets are too coarse for a lab (#192).

    gNMI calls take 300 to 400 ms on dcfabric. With the defaults (50, 100, 500,
    1000, ...) they all land between 100 and 500, and the p95 reads 480 ms
    whatever the calls took.
    """

    @staticmethod
    def _bounds(exposition: str, metric: str) -> list[float]:
        bounds = []
        for line in exposition.splitlines():
            if line.startswith(f"{metric}_bucket{{") and 'le="+Inf"' not in line:
                bounds.append(float(line.split('le="', 1)[1].split('"', 1)[0]))
        return bounds

    def test_the_gnmi_histogram_separates_calls_in_the_range_the_lab_sees(self) -> None:
        from snapl_orchestrator.metrics import GNMI_CALL_DURATION
        from snapl_orchestrator.worker.metrics import HISTOGRAM_BUCKETS_MS

        bounds = HISTOGRAM_BUCKETS_MS[GNMI_CALL_DURATION]

        assert list(bounds) == sorted(set(bounds))
        # 297 ms and 387 ms, the two means measured in #192, land in different buckets.
        assert any(297 < bound < 387 for bound in bounds)
        # No step wider than 100 ms below one second.
        below_a_second = [0, *[bound for bound in bounds if bound <= 1000]]
        assert max(b - a for a, b in itertools.pairwise(below_a_second)) <= 100

    def test_the_runtime_serves_the_gnmi_histogram_with_those_buckets(self) -> None:
        import urllib.request
        from datetime import timedelta

        from snapl_orchestrator.metrics import GNMI_CALL_DURATION
        from snapl_orchestrator.worker.metrics import HISTOGRAM_BUCKETS_MS

        port = _free_port()
        runtime = build_runtime(port=port, bind="127.0.0.1")
        assert runtime is not None
        histogram = runtime.metric_meter.create_histogram_timedelta(GNMI_CALL_DURATION, "test", "ms")
        histogram.record(timedelta(milliseconds=350), {"device": "leaf-01"})

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as response:
            exposition = response.read().decode()

        assert self._bounds(exposition, GNMI_CALL_DURATION) == [
            float(b) for b in HISTOGRAM_BUCKETS_MS[GNMI_CALL_DURATION]
        ]

    @pytest.mark.parametrize(
        "metric",
        ["temporal_workflow_endtoend_latency", "temporal_activity_schedule_to_start_latency"],
    )
    def test_the_sdk_histograms_the_dashboard_charts_are_overridden_too(self, metric: str) -> None:
        from snapl_orchestrator.worker.metrics import HISTOGRAM_BUCKETS_MS

        bounds = HISTOGRAM_BUCKETS_MS[metric]

        assert list(bounds) == sorted(set(bounds))
        assert len(bounds) >= 12


class TestTelemetryFamiliesOnTheEndpoint:
    """Spec 016 T019: the runtime's own meter serves the eight families (research R5)."""

    @staticmethod
    def _series(exposition: str) -> dict[str, list[dict[str, str]]]:
        series: dict[str, list[dict[str, str]]] = {}
        for line in exposition.splitlines():
            if not line.startswith("snapl_") or "{" not in line:
                continue
            name, rest = line.split("{", 1)
            body = rest.rsplit("}", 1)[0]
            labels = dict(pair.split("=", 1) for pair in body.split(","))
            series.setdefault(name, []).append({k: v.strip('"') for k, v in labels.items()})
        return series

    @staticmethod
    def _types(exposition: str) -> dict[str, str]:
        return {
            parts[2]: parts[3]
            for parts in (line.split() for line in exposition.splitlines() if line.startswith("# TYPE snapl_"))
        }

    def test_each_family_appears_under_its_name_with_its_labels(self, make_device) -> None:
        import urllib.request

        from snapl_collector.models import BgpSessionSample, InterfaceSample, PlatformSample
        from snapl_orchestrator.metrics import (
            TELEMETRY_FAMILIES,
            CounterTotals,
            record_interface,
            record_platform,
            record_session,
        )

        port = _free_port()
        runtime = build_runtime(port=port, bind="127.0.0.1")
        assert runtime is not None
        meter = runtime.metric_meter
        device = make_device("leaf-01")
        totals = CounterTotals()
        for total in (100, 130):
            values = dict.fromkeys(
                ("in_octets", "out_octets", "in_errors", "out_errors", "in_discards", "out_discards"), total
            )
            record_interface(meter, device, InterfaceSample(name="ethernet-1/49", oper_up=True, **values), totals)
        record_session(meter, device, BgpSessionSample(peer="10.10.1.1", state="active", prefixes_received=0))
        record_platform(meter, device, PlatformSample(cpu_percent=5, memory_percent=94))

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as response:
            exposition = response.read().decode()

        series = self._series(exposition)
        types = self._types(exposition)
        runtime_labels = {"service_name"}  # the only label the runtime adds to its own meter
        expected_labels = {
            "snapl_interface_up": {"device", "use_case", "interface"},
            "snapl_interface_octets_total": {"device", "use_case", "interface", "direction"},
            "snapl_interface_errors_total": {"device", "use_case", "interface", "direction"},
            "snapl_interface_discards_total": {"device", "use_case", "interface", "direction"},
            "snapl_bgp_session_established": {"device", "use_case", "peer"},
            "snapl_bgp_prefixes_received": {"device", "use_case", "peer"},
            "snapl_device_cpu_percent": {"device", "use_case"},
            "snapl_device_memory_percent": {"device", "use_case"},
        }
        assert set(expected_labels) == set(TELEMETRY_FAMILIES)
        for name, labels in expected_labels.items():
            assert name in series, f"{name} missing from the endpoint"
            assert types[name] == TELEMETRY_FAMILIES[name]
            for one in series[name]:
                assert set(one) == labels | runtime_labels, name
        # The counter keeps its name: no second _total (spec 015 R6), and the first total added 0.
        assert "snapl_interface_octets_total_total" not in exposition
        octets_in = [s for s in series["snapl_interface_octets_total"] if s["direction"] == "in"]
        assert len(octets_in) == 1
        assert 'direction="in",interface="ethernet-1/49"' in exposition
        assert any(
            line.startswith("snapl_interface_octets_total{") and 'direction="in"' in line and line.endswith(" 30")
            for line in exposition.splitlines()
        )
