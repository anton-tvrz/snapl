"""Unit tests for gnmi_set (T014)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from snapl_executor.gnmi.client import gnmi_set

pytestmark = pytest.mark.unit

_CONN = {
    "host": "127.0.0.1",
    "port": 57400,
    "username": "admin",
    "password": "test",  # pragma: allowlist secret
    "insecure": True,
    "timeout": 30,
}


def _mock_gc(response=None, side_effect=None):
    gc = MagicMock()
    gc.__enter__ = MagicMock(return_value=gc)
    gc.__exit__ = MagicMock(return_value=False)
    if side_effect:
        gc.set.side_effect = side_effect
    else:
        gc.set.return_value = response or {"response": [{"timestamp": 0}]}
    return gc


class TestGnmiSet:
    @pytest.mark.asyncio
    async def test_calls_set_with_correct_payload(self):
        payload = {"interface": []}
        gc = _mock_gc()
        with patch("snapl_executor.gnmi.client.gNMIclient", return_value=gc):
            result = await gnmi_set(**_CONN, payload=payload)
        gc.set.assert_called_once_with(update=[("/", payload)])
        assert result == {"response": [{"timestamp": 0}]}

    @pytest.mark.asyncio
    async def test_returns_dict(self):
        gc = _mock_gc(response={"response": [{"op": "UPDATE"}]})
        with patch("snapl_executor.gnmi.client.gNMIclient", return_value=gc):
            result = await gnmi_set(**_CONN, payload={})
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_propagates_grpc_exception(self):
        gc = _mock_gc(side_effect=OSError("connection refused"))
        with (
            patch("snapl_executor.gnmi.client.gNMIclient", return_value=gc),
            pytest.raises(OSError, match="connection refused"),
        ):
            await gnmi_set(**_CONN, payload={})
