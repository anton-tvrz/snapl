"""Unit tests for GnmiCollector (T012, T017, T019, T025)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

pytestmark = pytest.mark.unit

_DEVICE_ID = UUID("00000000-0000-0000-0000-000000000001")

_NOTIFICATION_ROOT = {
    "notification": [
        {
            "timestamp": 1234567890,
            "update": [
                {
                    "path": "/",
                    "val": {"interface": [{"name": "ethernet-1/1"}], "system": {"name": "spine-01"}},
                }
            ],
        }
    ]
}

_NOTIFICATION_IFACE = {
    "notification": [
        {
            "timestamp": 1234567890,
            "update": [
                {
                    "path": "/interface",
                    "val": [{"name": "ethernet-1/1"}],
                }
            ],
        }
    ]
}

_NOTIFICATION_EMPTY = {
    "notification": [
        {
            "timestamp": 1234567890,
            "update": [],
        }
    ]
}


def _make_collector(host="127.0.0.1"):
    from snapl_collector.gnmi.collector import GnmiCollector

    return GnmiCollector(
        host=host,
        port=57400,
        username="admin",
        password="test",  # pragma: allowlist secret
        insecure=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# T012 — get_running_config() tests (US1)
# ---------------------------------------------------------------------------


class TestGetRunningConfig:
    @pytest.mark.asyncio
    async def test_success_returns_collect_result(self, make_device):
        device = make_device("spine-01", address="127.0.0.1")
        collector = _make_collector()
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value=_NOTIFICATION_ROOT,
        ):
            result = await collector.get_running_config(device)
        assert result.success is True
        assert result.data != {}
        assert "/" in result.data
        assert result.error is None
        assert result.duration_ms >= 0
        assert result.timestamp is not None

    @pytest.mark.asyncio
    async def test_unreachable_device_returns_connectivity_error(self, make_device):
        device = make_device("spine-01")
        collector = _make_collector()
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            side_effect=OSError("connection refused"),
        ):
            result = await collector.get_running_config(device)
        assert result.success is False
        assert "connectivity" in result.error.lower()
        assert result.data == {}

    @pytest.mark.asyncio
    async def test_no_exception_raised_to_caller(self, make_device):
        device = make_device("spine-01")
        collector = _make_collector()
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            side_effect=OSError("unreachable"),
        ):
            result = await collector.get_running_config(device)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_auth_error_classified_correctly(self, make_device):
        import grpc

        device = make_device("spine-01")
        collector = _make_collector()

        class _AuthError(grpc.RpcError):
            def code(self):
                return grpc.StatusCode.UNAUTHENTICATED

            def details(self):
                return "invalid credentials"

        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            side_effect=_AuthError(),
        ):
            result = await collector.get_running_config(device)
        assert result.success is False
        assert "auth" in result.error.lower()

    @pytest.mark.asyncio
    async def test_timeout_error_classified_correctly(self, make_device):
        import grpc

        device = make_device("spine-01")
        collector = _make_collector()

        class _TimeoutError(grpc.RpcError):
            def code(self):
                return grpc.StatusCode.DEADLINE_EXCEEDED

            def details(self):
                return "deadline exceeded"

        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            side_effect=_TimeoutError(),
        ):
            result = await collector.get_running_config(device)
        assert result.success is False
        assert "timeout" in result.error.lower()

    @pytest.mark.asyncio
    async def test_parse_error_classified_correctly(self, make_device):
        device = make_device("spine-01")
        collector = _make_collector()
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value={"bad_key": "no notification here"},
        ):
            result = await collector.get_running_config(device)
        assert result.success is False
        assert "parse" in result.error.lower()


# ---------------------------------------------------------------------------
# T017 — collect() multi-path and edge-case tests (US2)
# ---------------------------------------------------------------------------


class TestCollect:
    @pytest.mark.asyncio
    async def test_single_path_data_keyed_by_path(self, make_device):
        device = make_device("spine-01")
        collector = _make_collector()
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value=_NOTIFICATION_IFACE,
        ):
            result = await collector.collect(device, paths=["/interface"])
        assert result.success is True
        assert "/interface" in result.data

    @pytest.mark.asyncio
    async def test_multi_path_response_keyed_by_both_paths(self, make_device):
        device = make_device("spine-01")
        collector = _make_collector()
        bgp_path = "/network-instance[name=default]/protocols/bgp/neighbor"
        multi_notification = {
            "notification": [
                {
                    "timestamp": 1234567890,
                    "update": [
                        {"path": "/interface", "val": [{"name": "ethernet-1/1"}]},
                        {"path": bgp_path, "val": [{"peer-address": "10.0.0.1"}]},
                    ],
                }
            ]
        }
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value=multi_notification,
        ):
            result = await collector.collect(device, paths=["/interface", bgp_path])
        assert result.success is True
        assert "/interface" in result.data
        assert bgp_path in result.data

    @pytest.mark.asyncio
    async def test_empty_update_list_returns_success_with_empty_data(self, make_device):
        device = make_device("spine-01")
        collector = _make_collector()
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value=_NOTIFICATION_EMPTY,
        ):
            result = await collector.collect(device, paths=["/interface"])
        assert result.success is True
        assert result.data == {}

    @pytest.mark.asyncio
    async def test_empty_paths_raises_value_error_before_connection(self, make_device):
        device = make_device("spine-01")
        collector = _make_collector()
        with (
            patch("snapl_collector.gnmi.collector.gnmi_get", new_callable=AsyncMock) as mock_gnmi_get,
            pytest.raises(ValueError, match="paths"),
        ):
            await collector.collect(device, paths=[])
        mock_gnmi_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_malformed_response_returns_parse_error(self, make_device):
        device = make_device("spine-01")
        collector = _make_collector()
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value={"unexpected": "structure"},
        ):
            result = await collector.collect(device, paths=["/interface"])
        assert result.success is False
        assert "parse" in result.error.lower()

    @pytest.mark.asyncio
    async def test_result_paths_field_matches_input(self, make_device):
        device = make_device("spine-01")
        collector = _make_collector()
        paths = ["/interface"]
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value=_NOTIFICATION_IFACE,
        ):
            result = await collector.collect(device, paths=paths)
        assert result.paths == paths


# ---------------------------------------------------------------------------
# T019 — collect_batch() tests (US3)
# ---------------------------------------------------------------------------


class TestCollectBatch:
    @pytest.mark.asyncio
    async def test_all_succeed_batch_result(self, make_device):
        devices = [make_device(f"spine-{i:02d}", address=f"10.0.0.{i}") for i in range(1, 4)]
        collector = _make_collector()
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value=_NOTIFICATION_ROOT,
        ):
            batch = await collector.collect_batch(devices, paths=["/"])
        assert batch.total == 3
        assert batch.succeeded == 3
        assert batch.failed == 0
        assert len(batch.results) == 3

    @pytest.mark.asyncio
    async def test_partial_failure_captured_not_raised(self, make_device):
        devices = [make_device(f"d-{i}", address=f"10.0.0.{i}") for i in range(1, 4)]
        collector = _make_collector()
        call_count = 0

        async def selective_fail(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("unreachable")
            return _NOTIFICATION_ROOT

        with patch("snapl_collector.gnmi.collector.gnmi_get", side_effect=selective_fail):
            batch = await collector.collect_batch(devices, paths=["/"])
        assert batch.total == 3
        assert batch.succeeded == 2
        assert batch.failed == 1

    @pytest.mark.asyncio
    async def test_empty_devices_raises_value_error(self, make_device):
        collector = _make_collector()
        with pytest.raises(ValueError, match="devices"):
            await collector.collect_batch([], paths=["/"])

    @pytest.mark.asyncio
    async def test_duplicate_device_ids_raises_value_error(self, make_device):
        from uuid import UUID

        shared_id = UUID("00000000-0000-0000-0000-000000000099")
        devices = [
            make_device("d1", device_id=shared_id),
            make_device("d2", device_id=shared_id),
        ]
        collector = _make_collector()
        with pytest.raises(ValueError, match="duplicate"):
            await collector.collect_batch(devices, paths=["/"])

    @pytest.mark.asyncio
    async def test_empty_paths_raises_value_error(self, make_device):
        devices = [make_device("spine-01")]
        collector = _make_collector()
        with pytest.raises(ValueError, match="paths"):
            await collector.collect_batch(devices, paths=[])


# ---------------------------------------------------------------------------
# T025 — SC-003: all error categories return CollectResult, no exceptions (Polish)
# ---------------------------------------------------------------------------


class TestErrorClassification:
    @pytest.mark.asyncio
    async def test_connectivity_error_no_exception(self, make_device):
        device = make_device("spine-01")
        collector = _make_collector()
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            side_effect=OSError("connection refused"),
        ):
            result = await collector.collect(device, paths=["/"])
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_timeout_error_no_exception(self, make_device):
        import grpc

        device = make_device("spine-01")
        collector = _make_collector()

        class _TimeoutError(grpc.RpcError):
            def code(self):
                return grpc.StatusCode.DEADLINE_EXCEEDED

            def details(self):
                return "timed out"

        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            side_effect=_TimeoutError(),
        ):
            result = await collector.collect(device, paths=["/"])
        assert result.success is False
        assert "timeout" in result.error.lower()

    @pytest.mark.asyncio
    async def test_auth_error_no_exception(self, make_device):
        import grpc

        device = make_device("spine-01")
        collector = _make_collector()

        class _AuthError(grpc.RpcError):
            def code(self):
                return grpc.StatusCode.UNAUTHENTICATED

            def details(self):
                return "bad creds"

        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            side_effect=_AuthError(),
        ):
            result = await collector.collect(device, paths=["/"])
        assert result.success is False
        assert "auth" in result.error.lower()

    @pytest.mark.asyncio
    async def test_parse_error_no_exception(self, make_device):
        device = make_device("spine-01")
        collector = _make_collector()
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value={"garbage": True},
        ):
            result = await collector.collect(device, paths=["/"])
        assert result.success is False
        assert "parse" in result.error.lower()


class TestPathNormalization:
    """Live SR Linux responses key updates differently than the idealized
    forms: the root path comes back as None, and targeted paths come back
    module-prefixed without a leading slash (srl_nokia-interfaces:interface).
    The collector normalizes them so callers can look up the paths they asked
    for."""

    @pytest.mark.asyncio
    async def test_none_path_normalized_to_root(self, make_device):
        device = make_device("spine-01")
        collector = _make_collector()
        notification = {"notification": [{"timestamp": 1, "update": [{"path": None, "val": {"interface": []}}]}]}
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value=notification,
        ):
            result = await collector.collect(device, paths=["/"])
        assert result.success is True
        assert "/" in result.data

    @pytest.mark.asyncio
    async def test_module_prefixed_path_normalized(self, make_device):
        device = make_device("spine-01")
        collector = _make_collector()
        notification = {
            "notification": [
                {
                    "timestamp": 1,
                    "update": [
                        {
                            "path": "srl_nokia-interfaces:interface",
                            "val": [{"name": "ethernet-1/1"}],
                        }
                    ],
                }
            ]
        }
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value=notification,
        ):
            result = await collector.collect(device, paths=["/interface"])
        assert result.success is True
        assert "/interface" in result.data

    @pytest.mark.asyncio
    async def test_module_prefix_stripped_only_outside_brackets(self, make_device):
        device = make_device("spine-01")
        collector = _make_collector()
        path = "srl_nokia-network-instance:network-instance[name=default]/protocols/srl_nokia-bgp:bgp"
        notification = {"notification": [{"timestamp": 1, "update": [{"path": path, "val": {}}]}]}
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value=notification,
        ):
            result = await collector.collect(device, paths=["/network-instance[name=default]/protocols/bgp"])
        assert "/network-instance[name=default]/protocols/bgp" in result.data

    @pytest.mark.asyncio
    async def test_empty_update_paths_recovered_from_requested_paths(self, make_device):
        """SR Linux answers multi-path GETs with one notification per
        requested path, each update carrying an empty path; the collector
        recovers the requested path by position."""
        device = make_device("spine-01")
        collector = _make_collector()
        bgp_path = "/network-instance[name=default]/protocols/bgp/neighbor"
        notification = {
            "notification": [
                {"timestamp": 1, "update": [{"path": None, "val": {"srl_nokia-interfaces:interface": []}}]},
                {"timestamp": 1, "update": [{"path": None, "val": {"neighbor": []}}]},
            ]
        }
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value=notification,
        ):
            result = await collector.collect(device, paths=["/interface", bgp_path])
        assert result.success is True
        assert "/interface" in result.data
        assert bgp_path in result.data
