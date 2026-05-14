"""Unit tests for gnmi_get client wrapper (T011)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from snapl_collector.gnmi.client import gnmi_get

pytestmark = pytest.mark.unit

_CONN = {
    "host": "127.0.0.1",
    "port": 57400,
    "username": "admin",
    "password": "test",  # pragma: allowlist secret
    "insecure": True,
    "timeout": 30,
}

_NOTIFICATION = {
    "notification": [
        {
            "timestamp": 1234567890,
            "update": [{"path": "/", "val": {"interface": []}}],
        }
    ]
}


def _mock_gc(response=None, side_effect=None):
    gc = MagicMock()
    gc.__enter__ = MagicMock(return_value=gc)
    gc.__exit__ = MagicMock(return_value=False)
    if side_effect:
        gc.get.side_effect = side_effect
    else:
        gc.get.return_value = response or _NOTIFICATION
    return gc


class TestGnmiGet:
    @pytest.mark.asyncio
    async def test_calls_get_with_correct_params(self):
        gc = _mock_gc()
        with patch("snapl_collector.gnmi.client.gNMIclient", return_value=gc):
            await gnmi_get(**_CONN, paths=["/"])
        gc.get.assert_called_once_with(path=["/"], datatype="all", encoding="json_ietf")

    @pytest.mark.asyncio
    async def test_returns_response_dict(self):
        gc = _mock_gc(response=_NOTIFICATION)
        with patch("snapl_collector.gnmi.client.gNMIclient", return_value=gc):
            result = await gnmi_get(**_CONN, paths=["/"])
        assert isinstance(result, dict)
        assert "notification" in result

    @pytest.mark.asyncio
    async def test_forwards_multiple_paths(self):
        paths = ["/interface", "/network-instance[name=default]/protocols/bgp/neighbor"]
        gc = _mock_gc()
        with patch("snapl_collector.gnmi.client.gNMIclient", return_value=gc):
            await gnmi_get(**_CONN, paths=paths)
        gc.get.assert_called_once_with(path=paths, datatype="all", encoding="json_ietf")

    @pytest.mark.asyncio
    async def test_propagates_os_error(self):
        gc = _mock_gc(side_effect=OSError("connection refused"))
        with (
            patch("snapl_collector.gnmi.client.gNMIclient", return_value=gc),
            pytest.raises(OSError, match="connection refused"),
        ):
            await gnmi_get(**_CONN, paths=["/"])

    @pytest.mark.asyncio
    async def test_propagates_grpc_exception(self):
        gc = _mock_gc(side_effect=Exception("grpc error"))
        with (
            patch("snapl_collector.gnmi.client.gNMIclient", return_value=gc),
            pytest.raises(Exception, match="grpc error"),
        ):
            await gnmi_get(**_CONN, paths=["/"])
