"""Unit tests for GnmiCollector (T012, T017, T019, T025)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
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
    async def test_multipath_data_keyed_by_stamped_path_regardless_of_order(self, make_device):
        """gnmi_get stamps each subtree with its requested path, so collect keys
        the data correctly even when notifications arrive in a different order
        than the requested paths (#53) — no positional guessing."""
        device = make_device("spine-01")
        collector = _make_collector()
        bgp_path = "/network-instance[name=default]/protocols/bgp/neighbor"
        # Notifications returned in the REVERSE of the requested-path order,
        # each stamped (by gnmi_get) with the path it belongs to.
        notification = {
            "notification": [
                {"timestamp": 1, "update": [{"path": bgp_path, "val": {"neighbor": []}}]},
                {"timestamp": 1, "update": [{"path": "/interface", "val": {"srl_nokia-interfaces:interface": []}}]},
            ]
        }
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value=notification,
        ):
            result = await collector.collect(device, paths=["/interface", bgp_path])
        assert result.success is True
        assert result.data["/interface"] == {"srl_nokia-interfaces:interface": []}
        assert result.data[bgp_path] == {"neighbor": []}


# ---------------------------------------------------------------------------
# #30 — per-device dial-host resolution
# ---------------------------------------------------------------------------


class TestDialHostResolution:
    """The dial target comes from the Device: lab_node_name first, then
    management_address, then the constructor host as a last-resort fallback
    (regression for #30 — a worker-wide instance must reach every device)."""

    @pytest.mark.asyncio
    async def test_collect_dials_lab_node_name_first(self, make_device):
        device = make_device("spine-01", address="10.0.0.1", lab_node_name="clab-dcfabric-spine-01")
        collector = _make_collector(host=None)
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value=_NOTIFICATION_ROOT,
        ) as mock_get:
            result = await collector.collect(device, paths=["/"])
        assert result.success is True
        assert mock_get.call_args.kwargs["host"] == "clab-dcfabric-spine-01"

    @pytest.mark.asyncio
    async def test_collect_dials_management_address_without_lab_node_name(self, make_device):
        device = make_device("spine-01", address="10.0.0.1")
        collector = _make_collector(host=None)
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value=_NOTIFICATION_ROOT,
        ) as mock_get:
            result = await collector.collect(device, paths=["/"])
        assert result.success is True
        assert mock_get.call_args.kwargs["host"] == "10.0.0.1"

    @pytest.mark.asyncio
    async def test_collect_falls_back_to_constructor_host(self, make_device):
        device = make_device("spine-01", address="")
        collector = _make_collector(host="192.0.2.7")
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value=_NOTIFICATION_ROOT,
        ) as mock_get:
            result = await collector.collect(device, paths=["/"])
        assert result.success is True
        assert mock_get.call_args.kwargs["host"] == "192.0.2.7"

    @pytest.mark.asyncio
    async def test_collect_without_any_dial_target_errors_not_raises(self, make_device):
        device = make_device("spine-01", address="")
        collector = _make_collector(host=None)
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
        ) as mock_get:
            result = await collector.collect(device, paths=["/"])
            mock_get.assert_not_called()
        assert result.success is False
        assert "dial target" in (result.error or "")

    @pytest.mark.asyncio
    async def test_constructor_host_is_optional(self):
        from snapl_collector.gnmi.collector import GnmiCollector

        collector = GnmiCollector(password="test")  # pragma: allowlist secret
        assert collector is not None

    @pytest.mark.asyncio
    async def test_collect_batch_dials_each_device(self, make_device):
        devices = [
            make_device("spine-01", lab_node_name="clab-dcfabric-spine-01"),
            make_device("leaf-01", lab_node_name="clab-dcfabric-leaf-01"),
        ]
        collector = _make_collector(host=None)
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value=_NOTIFICATION_ROOT,
        ) as mock_get:
            batch = await collector.collect_batch(devices, paths=["/"])
        assert batch.succeeded == 2
        hosts = {call.kwargs["host"] for call in mock_get.call_args_list}
        assert hosts == {"clab-dcfabric-spine-01", "clab-dcfabric-leaf-01"}


# ---------------------------------------------------------------------------
# #69 — repeated same-path updates are merged, not overwritten
# ---------------------------------------------------------------------------


def _fragments(path, *vals):
    """A GET response with one notification per fragment, all for one path."""
    return {"notification": [{"timestamp": 1, "update": [{"path": path, "val": val}]} for val in vals]}


class TestRepeatedPathUpdates:
    """gNMI lets a target split one requested path across several
    notifications/updates. Every fragment must reach the caller; fragments
    that can't be reconciled fail the collect loudly as a parse error (#69)."""

    async def _collect(self, make_device, response, paths):
        collector = _make_collector()
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            return_value=response,
        ):
            return await collector.collect(make_device("spine-01"), paths=paths)

    @pytest.mark.asyncio
    async def test_single_update_unchanged(self, make_device):
        val = {"admin-state": "enable", "subinterface": [{"index": 0}]}
        result = await self._collect(make_device, _fragments("/interface[name=ethernet-1/1]", val), ["/interface"])
        assert result.success is True
        assert result.data == {"/interface[name=ethernet-1/1]": val}

    @pytest.mark.asyncio
    async def test_split_container_deep_merged(self, make_device):
        bgp_path = "/network-instance[name=default]/protocols/bgp"
        response = _fragments(
            bgp_path,
            {"autonomous-system": 65001, "ipv4-unicast": {"admin-state": "enable"}},
            {"router-id": "10.0.0.1", "ipv4-unicast": {"multipath": {"max-paths-level-1": 2}}},
        )
        result = await self._collect(make_device, response, [bgp_path])
        assert result.success is True
        assert result.data[bgp_path] == {
            "autonomous-system": 65001,
            "router-id": "10.0.0.1",
            "ipv4-unicast": {"admin-state": "enable", "multipath": {"max-paths-level-1": 2}},
        }

    @pytest.mark.asyncio
    async def test_split_list_combined(self, make_device):
        response = _fragments("/interface", [{"name": "ethernet-1/1"}], [{"name": "ethernet-1/2"}])
        result = await self._collect(make_device, response, ["/interface"])
        assert result.success is True
        assert result.data["/interface"] == [{"name": "ethernet-1/1"}, {"name": "ethernet-1/2"}]

    @pytest.mark.asyncio
    async def test_split_root_response_merged(self, make_device):
        """A root GET reports an empty path on every fragment; all of them key
        to "/" and are merged, lists inside the subtree combined."""
        response = _fragments(
            None,
            {"interface": [{"name": "ethernet-1/1"}], "system": {"name": {"host-name": "spine-01"}}},
            {"interface": [{"name": "ethernet-1/2"}], "system": {"ntp": {"admin-state": "enable"}}},
        )
        result = await self._collect(make_device, response, ["/"])
        assert result.success is True
        assert result.data["/"] == {
            "interface": [{"name": "ethernet-1/1"}, {"name": "ethernet-1/2"}],
            "system": {"name": {"host-name": "spine-01"}, "ntp": {"admin-state": "enable"}},
        }

    @pytest.mark.asyncio
    async def test_resent_fragment_is_idempotent(self, make_device):
        val = {"mtu": 9000, "subinterface": [{"index": 0}]}
        response = _fragments("/interface[name=ethernet-1/1]", val, val)
        result = await self._collect(make_device, response, ["/interface[name=ethernet-1/1]"])
        assert result.success is True
        assert result.data["/interface[name=ethernet-1/1]"] == val

    @pytest.mark.asyncio
    async def test_conflicting_scalars_raise_parse_error(self, make_device):
        path = "/interface[name=ethernet-1/1]"
        response = _fragments(path, {"mtu": 1500}, {"mtu": 9000})
        result = await self._collect(make_device, response, [path])
        assert result.success is False
        assert result.data == {}
        assert "parse error" in result.error
        assert f"{path}/mtu" in result.error

    @pytest.mark.asyncio
    async def test_mismatched_shapes_raise_parse_error(self, make_device):
        response = _fragments("/interface", [{"name": "ethernet-1/1"}], {"name": "ethernet-1/2"})
        result = await self._collect(make_device, response, ["/interface"])
        assert result.success is False
        assert "parse error" in result.error


# ---------------------------------------------------------------------------
# #69 — missing credentials fail at construction
# ---------------------------------------------------------------------------


class TestConstructorValidation:
    """A collector without a password is a bootstrap error, raised when it is
    built — not a per-device "auth error" at collect time (#69)."""

    @pytest.mark.parametrize("password", ["", None])
    def test_missing_password_raises_config_error(self, password):
        from snapl_collector.exceptions import CollectorConfigError
        from snapl_collector.gnmi.collector import GnmiCollector

        with pytest.raises(CollectorConfigError, match="password is required"):
            GnmiCollector(host="127.0.0.1", password=password)


class _SilentError(Exception):
    """An exception whose ``str()`` is empty, like a dial that never connects (#194)."""

    def __str__(self) -> str:
        return ""


class TestErrorReasonIsNeverBlank:
    """An operator must be able to act on the reason (#194)."""

    @pytest.mark.asyncio
    async def test_an_exception_with_no_message_is_named_by_its_class(self, make_device):
        device = make_device("spine-01")
        collector = _make_collector()
        with patch("snapl_collector.gnmi.collector.gnmi_get", new_callable=AsyncMock, side_effect=_SilentError()):
            result = await collector.get_running_config(device)

        assert result.success is False
        assert "_SilentError" in result.error

    @pytest.mark.asyncio
    async def test_a_connectivity_error_names_the_dial_target(self, make_device):
        device = make_device("spine-01")
        collector = _make_collector()
        target = f"{collector._dial_host(device)}:57400"
        with patch("snapl_collector.gnmi.collector.gnmi_get", new_callable=AsyncMock, side_effect=_SilentError()):
            result = await collector.get_running_config(device)

        assert result.error == f"connectivity error: _SilentError, dialing {target}"

    @pytest.mark.asyncio
    async def test_a_message_is_kept_and_the_dial_target_added(self, make_device):
        device = make_device("spine-01")
        collector = _make_collector()
        target = f"{collector._dial_host(device)}:57400"
        with patch(
            "snapl_collector.gnmi.collector.gnmi_get",
            new_callable=AsyncMock,
            side_effect=OSError("connection refused"),
        ):
            result = await collector.get_running_config(device)

        assert result.error == f"connectivity error: connection refused, dialing {target}"

    @pytest.mark.asyncio
    async def test_a_batch_failure_is_not_blank_either(self, make_device):
        device = make_device("spine-01")
        collector = _make_collector()
        with patch.object(collector, "collect", new_callable=AsyncMock, side_effect=_SilentError()):
            batch = await collector.collect_batch([device], ["/interface"])

        assert "_SilentError" in batch.results[device.id].error


class TestCollectBatchConcurrencyCap:
    """A cap on how many devices a batch dials at once (#55)."""

    @pytest.mark.asyncio
    async def test_no_more_than_the_cap_are_dialed_at_once(self, make_device):
        import asyncio

        from snapl_collector.gnmi.collector import GnmiCollector

        collector = GnmiCollector(password="test", max_concurrency=2)  # pragma: allowlist secret
        devices = [make_device(f"d-{i}", address=f"10.0.0.{i}") for i in range(1, 7)]
        in_flight = 0
        max_in_flight = 0

        async def slow_get(*_args, **_kwargs):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return _NOTIFICATION_ROOT

        with patch("snapl_collector.gnmi.collector.gnmi_get", side_effect=slow_get):
            batch = await collector.collect_batch(devices, paths=["/"])

        assert batch.succeeded == 6
        assert max_in_flight == 2

    def test_the_default_cap_is_documented(self):
        from snapl_collector.gnmi.collector import DEFAULT_MAX_CONCURRENCY

        assert DEFAULT_MAX_CONCURRENCY == 16

    @pytest.mark.parametrize("bad", [0, -1])
    def test_a_cap_below_one_is_a_config_error(self, bad):
        from snapl_collector.exceptions import CollectorConfigError
        from snapl_collector.gnmi.collector import GnmiCollector

        with pytest.raises(CollectorConfigError, match="max_concurrency"):
            GnmiCollector(password="test", max_concurrency=bad)  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# Spec 016 T007 — stream_telemetry, replaying the captured subscriptions
# ---------------------------------------------------------------------------

_TELEMETRY_FIXTURES = Path(__file__).parent / "fixtures" / "telemetry"
_LEAF01_IFS = ("ethernet-1/49", "lo0")
_SPINE01_IFS = ("ethernet-1/1",)
_SPINE01_PEERS = ("10.10.1.1",)


def _capture(name: str) -> list[dict]:
    return [entry["msg"] for entry in json.loads((_TELEMETRY_FIXTURES / name).read_text())]


def _replay(messages: list[dict], calls: list[dict] | None = None):
    """A stand-in for subscribe_sample that yields the captured messages, then ends."""

    async def _fake_subscribe_sample(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        for message in messages:
            yield message

    return patch("snapl_collector.gnmi.collector.subscribe_sample", _fake_subscribe_sample)


async def _samples(collector, target, messages, calls=None):
    with _replay(messages, calls):
        return [s async for s in collector.stream_telemetry(target, interval_s=10)]


def _target(make_device, name, interfaces=(), peers=(), **device_kwargs):
    from snapl_collector.models import TelemetryTarget

    return TelemetryTarget(device=make_device(name, **device_kwargs), interfaces=interfaces, peers=peers)


def _upd(path: str, val, *, sync: bool = False, timestamp: int = 1795097375288928413) -> dict:
    message: dict = {"update": {"update": [{"path": path, "val": val}], "timestamp": timestamp}}
    if sync:
        message["sync_response"] = True
    return message


class TestStreamTelemetryPaths:
    async def test_one_path_per_item_plus_cpu_and_memory(self, make_device):
        target = _target(make_device, "spine-01", interfaces=("ethernet-1/1", "lo0"), peers=("10.10.1.1",))
        calls: list[dict] = []
        await _samples(_make_collector(), target, [], calls)
        assert calls[0]["paths"] == [
            "/interface[name=ethernet-1/1]/oper-state",
            "/interface[name=ethernet-1/1]/statistics",
            "/interface[name=lo0]/oper-state",
            "/interface[name=lo0]/statistics",
            "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=10.10.1.1]/session-state",
            "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=10.10.1.1]"
            "/afi-safi[afi-safi-name=ipv4-unicast]/received-routes",
            "/platform/control[slot=A]/cpu[index=all]/total/instant",
            "/platform/control[slot=A]/memory/utilization",
        ]
        assert calls[0]["interval_s"] == 10

    async def test_dials_the_device_as_collect_does(self, make_device):
        target = _target(make_device, "leaf-01", interfaces=("lo0",), address="10.0.0.13", lab_node_name="172.20.21.13")
        calls: list[dict] = []
        await _samples(_make_collector(host="fallback"), target, [], calls)
        assert calls[0]["host"] == "172.20.21.13"
        assert calls[0]["port"] == 57400
        assert calls[0]["username"] == "admin"
        assert calls[0]["insecure"] is True
        assert calls[0]["timeout"] == 30

    async def test_no_dial_target_raises_a_stream_error(self, make_device):
        from snapl_collector import TelemetryStreamError
        from snapl_collector.gnmi.collector import GnmiCollector

        collector = GnmiCollector(password="test")  # pragma: allowlist secret
        target = _target(make_device, "leaf-01", interfaces=("lo0",), address="")
        with pytest.raises(TelemetryStreamError, match="no gNMI dial target"):
            await _samples(collector, target, [])


class TestStreamTelemetryLeafCapture:
    async def test_the_first_sample_is_sync_and_carries_every_item(self, make_device):
        from snapl_collector.models import InterfaceSample, PlatformSample

        target = _target(make_device, "leaf-01", interfaces=_LEAF01_IFS)
        samples = await _samples(_make_collector(), target, _capture("srlinux_v26_3_1_leaf01_sample.json"))
        first = samples[0]
        assert first.sync is True
        assert first.device_name == "leaf-01"
        assert first.interfaces == (
            InterfaceSample(
                name="ethernet-1/49",
                oper_up=True,
                in_octets=2925084,
                out_octets=2953436,
                in_errors=0,
                out_errors=0,
                in_discards=140,
                out_discards=0,
            ),
            InterfaceSample(name="lo0", oper_up=True),
        )
        assert first.sessions == ()
        assert first.platform == PlatformSample(cpu_percent=5, memory_percent=94)

    async def test_the_device_timestamp_becomes_the_sample_time(self, make_device):
        target = _target(make_device, "leaf-01", interfaces=_LEAF01_IFS)
        samples = await _samples(_make_collector(), target, _capture("srlinux_v26_3_1_leaf01_sample.json"))
        expected = datetime.fromtimestamp(1795097375288928413 / 1e9, tz=UTC)
        assert abs((samples[0].timestamp - expected).total_seconds()) < 1e-3
        assert samples[0].timestamp.tzinfo is not None
        assert samples[1].timestamp > samples[0].timestamp

    async def test_later_samples_carry_one_item_each(self, make_device):
        target = _target(make_device, "leaf-01", interfaces=_LEAF01_IFS)
        samples = await _samples(_make_collector(), target, _capture("srlinux_v26_3_1_leaf01_sample.json"))
        assert len(samples) == 20
        for sample in samples[1:]:
            assert sample.sync is False
            items = len(sample.interfaces) + len(sample.sessions) + (sample.platform is not None)
            assert items == 1

    async def test_a_state_message_carries_no_counters_and_a_counter_message_no_state(self, make_device):
        target = _target(make_device, "leaf-01", interfaces=_LEAF01_IFS)
        samples = await _samples(_make_collector(), target, _capture("srlinux_v26_3_1_leaf01_sample.json"))
        state, counters = samples[1].interfaces[0], samples[2].interfaces[0]
        assert (state.name, state.oper_up, state.in_octets) == ("ethernet-1/49", True, None)
        assert (counters.name, counters.oper_up, counters.in_octets) == ("ethernet-1/49", None, 2925312)

    async def test_lo0_has_state_and_no_counters(self, make_device):
        target = _target(make_device, "leaf-01", interfaces=_LEAF01_IFS)
        samples = await _samples(_make_collector(), target, _capture("srlinux_v26_3_1_leaf01_sample.json"))
        lo0 = [i for s in samples for i in s.interfaces if i.name == "lo0"]
        assert lo0
        assert all(i.in_octets is None and i.out_errors is None and i.in_discards is None for i in lo0)
        assert any(i.oper_up is True for i in lo0)


class TestStreamTelemetrySpineCapture:
    async def test_the_session_reads_active_with_no_prefixes(self, make_device):
        from snapl_collector.models import BgpSessionSample

        target = _target(make_device, "spine-01", interfaces=_SPINE01_IFS, peers=_SPINE01_PEERS)
        samples = await _samples(_make_collector(), target, _capture("srlinux_v26_3_1_spine01_sample.json"))
        assert samples[0].sync is True
        assert samples[0].sessions == (BgpSessionSample(peer="10.10.1.1", state="active", prefixes_received=0),)
        later = [s for sample in samples[1:] for s in sample.sessions]
        assert BgpSessionSample(peer="10.10.1.1", state="active") in later
        assert BgpSessionSample(peer="10.10.1.1", prefixes_received=0) in later

    async def test_cpu_and_memory_parse(self, make_device):
        target = _target(make_device, "spine-01", interfaces=_SPINE01_IFS, peers=_SPINE01_PEERS)
        samples = await _samples(_make_collector(), target, _capture("srlinux_v26_3_1_spine01_sample.json"))
        platforms = [s.platform for s in samples if s.platform is not None]
        assert any(p.cpu_percent is not None and 0 <= p.cpu_percent <= 100 for p in platforms)
        assert any(p.memory_percent == 94 for p in platforms)

    async def test_items_outside_the_target_are_dropped(self, make_device):
        # Ask for the interface only: the capture's session updates are not ours.
        target = _target(make_device, "spine-01", interfaces=_SPINE01_IFS)
        samples = await _samples(_make_collector(), target, _capture("srlinux_v26_3_1_spine01_sample.json"))
        assert all(s.sessions == () for s in samples)


class TestStreamTelemetryParsing:
    async def test_an_interface_outside_the_target_is_dropped(self, make_device):
        target = _target(make_device, "leaf-01", interfaces=("lo0",))
        messages = [
            _upd("srl_nokia-interfaces:interface[name=mgmt0]", {"oper-state": "up"}, sync=True),
            _upd("srl_nokia-interfaces:interface[name=lo0]", {"oper-state": "down"}),
        ]
        samples = await _samples(_make_collector(), target, messages)
        assert samples[0].interfaces == ()
        assert [(i.name, i.oper_up) for i in samples[1].interfaces] == [("lo0", False)]

    async def test_a_value_that_does_not_parse_is_none_and_logged_once(self, make_device, caplog):
        target = _target(make_device, "leaf-01", interfaces=("ethernet-1/49",))
        bad = {"in-octets": "lots", "out-octets": "7"}
        messages = [
            _upd("srl_nokia-interfaces:interface[name=ethernet-1/49]/statistics", bad, sync=True),
            _upd("srl_nokia-interfaces:interface[name=ethernet-1/49]/statistics", bad),
        ]
        with caplog.at_level(logging.WARNING, logger="snapl_collector.gnmi.collector"):
            samples = await _samples(_make_collector(), target, messages)
        assert [(i.in_octets, i.out_octets) for s in samples for i in s.interfaces] == [(None, 7), (None, 7)]
        warnings = [r for r in caplog.records if "in-octets" in r.getMessage()]
        assert len(warnings) == 1
        assert "leaf-01" in warnings[0].getMessage()

    async def test_an_unknown_oper_state_is_none(self, make_device, caplog):
        target = _target(make_device, "leaf-01", interfaces=("lo0",))
        messages = [_upd("srl_nokia-interfaces:interface[name=lo0]", {"oper-state": "testing"}, sync=True)]
        with caplog.at_level(logging.WARNING, logger="snapl_collector.gnmi.collector"):
            samples = await _samples(_make_collector(), target, messages)
        assert samples[0].interfaces[0].oper_up is None
        assert any("oper-state" in r.getMessage() for r in caplog.records)

    async def test_the_session_state_is_lower_cased(self, make_device):
        target = _target(make_device, "spine-01", peers=("10.10.1.1",))
        path = (
            "srl_nokia-network-instance:network-instance[name=default]"
            "/protocols/srl_nokia-bgp:bgp/neighbor[peer-address=10.10.1.1]"
        )
        samples = await _samples(_make_collector(), target, [_upd(path, {"session-state": "Established"}, sync=True)])
        assert samples[0].sessions[0].state == "established"

    async def test_a_value_given_at_the_leaf_path_parses(self, make_device):
        """A device may cut the path at the leaf rather than at the list entry."""
        target = _target(make_device, "leaf-01", interfaces=("lo0",))
        samples = await _samples(
            _make_collector(),
            target,
            [
                _upd("interface[name=lo0]/oper-state", "up", sync=True),
                _upd("platform/control[slot=A]/memory/utilization", 51),
            ],
        )
        assert samples[0].interfaces[0].oper_up is True
        assert samples[1].platform is not None
        assert samples[1].platform.memory_percent == 51

    async def test_module_prefixed_keys_in_a_value_parse(self, make_device):
        target = _target(make_device, "leaf-01", interfaces=("lo0",))
        messages = [_upd("interface[name=lo0]", {"srl_nokia-interfaces:oper-state": "up"}, sync=True)]
        samples = await _samples(_make_collector(), target, messages)
        assert samples[0].interfaces[0].oper_up is True

    async def test_a_message_without_updates_yields_an_empty_sample(self, make_device):
        target = _target(make_device, "leaf-01", interfaces=("lo0",))
        samples = await _samples(_make_collector(), target, [{"sync_response": True}])
        assert len(samples) == 1
        assert samples[0].sync is True
        assert samples[0].interfaces == ()
        assert samples[0].timestamp.tzinfo is not None

    async def test_a_stream_error_propagates(self, make_device):
        from snapl_collector import TelemetryStreamError

        async def _broken(**kwargs):
            yield _upd("interface[name=lo0]", {"oper-state": "up"}, sync=True)
            raise TelemetryStreamError("telemetry from 172.20.21.13:57400: UNAVAILABLE")

        target = _target(make_device, "leaf-01", interfaces=("lo0",))
        got = []

        async def _drain():
            async for sample in _make_collector().stream_telemetry(target, interval_s=10):
                got.append(sample)

        with (
            patch("snapl_collector.gnmi.collector.subscribe_sample", _broken),
            pytest.raises(TelemetryStreamError, match="UNAVAILABLE"),
        ):
            await _drain()
        assert len(got) == 1
