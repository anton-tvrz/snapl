"""Unit tests for GnmiExecutor (T011, T019, T023, T026)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from snapl_executor.gnmi.executor import GnmiExecutor
from snapl_executor.models import ApplyResult, BatchResult, DryRunResult


def _make_executor(**kwargs) -> GnmiExecutor:
    defaults: dict = {
        "host": "127.0.0.1",
        "port": 57400,
        "username": "admin",
        "password": "test",  # pragma: allowlist secret
    }
    defaults.update(kwargs)
    return GnmiExecutor(**defaults)


# ---------------------------------------------------------------------------
# T011 — apply()
# ---------------------------------------------------------------------------


class TestApply:
    @pytest.mark.asyncio
    async def test_apply_success(self, dcfabric_desired_state, mock_gnmi_client):
        executor = _make_executor()
        with patch("snapl_executor.gnmi.executor.gNMIclient", return_value=mock_gnmi_client):
            result = await executor.apply(dcfabric_desired_state)
        assert isinstance(result, ApplyResult)
        assert result.success is True
        assert result.error is None
        assert result.is_rollback is False
        assert result.duration_ms >= 0
        assert result.payload

    @pytest.mark.asyncio
    async def test_apply_connection_error(self, dcfabric_desired_state):
        executor = _make_executor()
        with patch("snapl_executor.gnmi.executor.gNMIclient") as mock_cls:
            mock_cls.return_value.__enter__.side_effect = OSError("connection refused")
            result = await executor.apply(dcfabric_desired_state)
        assert result.success is False
        assert result.error is not None
        assert result.is_rollback is False

    @pytest.mark.asyncio
    async def test_apply_device_rejects_payload(self, dcfabric_desired_state, mock_gnmi_client):
        mock_gnmi_client.set.side_effect = Exception("gRPC status INVALID_ARGUMENT")
        executor = _make_executor()
        with patch("snapl_executor.gnmi.executor.gNMIclient", return_value=mock_gnmi_client):
            result = await executor.apply(dcfabric_desired_state)
        assert result.success is False
        assert "INVALID_ARGUMENT" in (result.error or "")

    @pytest.mark.asyncio
    async def test_apply_result_contains_payload(self, dcfabric_desired_state, mock_gnmi_client):
        executor = _make_executor()
        with patch("snapl_executor.gnmi.executor.gNMIclient", return_value=mock_gnmi_client):
            result = await executor.apply(dcfabric_desired_state)
        assert isinstance(result.payload, dict)
        assert result.payload  # non-empty


# ---------------------------------------------------------------------------
# T019 — dry_run()
# ---------------------------------------------------------------------------


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_success_no_gnmi_call(self, dcfabric_desired_state):
        executor = _make_executor()
        with patch("snapl_executor.gnmi.executor.gNMIclient") as mock_cls:
            result = await executor.dry_run(dcfabric_desired_state)
            mock_cls.assert_not_called()
        assert isinstance(result, DryRunResult)
        assert result.success is True
        assert result.payload is not None
        assert result.render_error is None

    @pytest.mark.asyncio
    async def test_dry_run_result_not_committed(self, dcfabric_desired_state):
        executor = _make_executor()
        with patch("snapl_executor.gnmi.executor.gNMIclient") as mock_cls:
            result = await executor.dry_run(dcfabric_desired_state)
            mock_cls.assert_not_called()
        assert result.success is True

    @pytest.mark.asyncio
    async def test_dry_run_render_error(self, dcfabric_desired_state):
        executor = _make_executor()
        with patch("snapl_executor.gnmi.executor.ConfigRenderer") as mock_renderer_cls:
            mock_renderer = MagicMock()
            mock_renderer.render_safe.return_value = {"_render_error": "undefined variable: foo"}
            mock_renderer_cls.return_value = mock_renderer
            result = await executor.dry_run(dcfabric_desired_state)
        assert result.success is False
        assert result.render_error is not None
        assert result.payload is None


# ---------------------------------------------------------------------------
# T023 — rollback()
# ---------------------------------------------------------------------------


class TestRollback:
    @pytest.mark.asyncio
    async def test_rollback_sets_is_rollback_flag(self, dcfabric_desired_state, mock_gnmi_client):
        executor = _make_executor()
        with patch("snapl_executor.gnmi.executor.gNMIclient", return_value=mock_gnmi_client):
            result = await executor.rollback(dcfabric_desired_state)
        assert result.is_rollback is True

    @pytest.mark.asyncio
    async def test_rollback_success(self, dcfabric_desired_state, mock_gnmi_client):
        executor = _make_executor()
        with patch("snapl_executor.gnmi.executor.gNMIclient", return_value=mock_gnmi_client):
            result = await executor.rollback(dcfabric_desired_state)
        assert result.success is True
        assert result.is_rollback is True

    @pytest.mark.asyncio
    async def test_rollback_failure_preserves_is_rollback(self, dcfabric_desired_state):
        executor = _make_executor()
        with patch("snapl_executor.gnmi.executor.gNMIclient") as mock_cls:
            mock_cls.return_value.__enter__.side_effect = OSError("refused")
            result = await executor.rollback(dcfabric_desired_state)
        assert result.is_rollback is True
        assert result.success is False


# ---------------------------------------------------------------------------
# T026 — apply_batch()
# ---------------------------------------------------------------------------


def _make_desired(device_name: str, device_id: UUID | None = None):
    from snapl_intent.models import BGPSession, DesiredState, Device, Interface

    dev_id = device_id or uuid4()
    device = Device(
        id=dev_id,
        name=device_name,
        management_address="127.0.0.1",
        role="spine",
        use_case="dcfabric",
        platform="nokia-srlinux",
    )
    ifaces = [
        Interface(
            id=uuid4(),
            device_id=dev_id,
            name="ethernet-1/1",
            ip_address="10.0.0.0",
            prefix_length=31,
            enabled=True,
            mtu=9232,
        )
    ]
    sessions = [
        BGPSession(
            id=uuid4(),
            device_id=dev_id,
            local_asn=65000,
            peer_address="10.0.0.1",
            peer_asn=65001,
            enabled=True,
            address_family="ipv4_unicast",
        )
    ]
    return DesiredState(device=device, interfaces=ifaces, bgp_sessions=sessions)


class TestApplyBatch:
    @pytest.mark.asyncio
    async def test_batch_all_succeed(self, mock_gnmi_client):
        states = [_make_desired(f"dev-{i}") for i in range(3)]
        executor = _make_executor()
        with patch("snapl_executor.gnmi.executor.gNMIclient", return_value=mock_gnmi_client):
            result = await executor.apply_batch(states)
        assert isinstance(result, BatchResult)
        assert result.total == 3
        assert result.succeeded == 3
        assert result.failed == 0

    @pytest.mark.asyncio
    async def test_batch_partial_failure(self, mock_gnmi_client):
        states = [_make_desired(f"dev-{i}") for i in range(3)]
        executor = _make_executor()
        call_count = 0

        def failing_set(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise Exception("device rejected")
            return {"response": [{"timestamp": 0}]}

        mock_gnmi_client.set.side_effect = failing_set
        with patch("snapl_executor.gnmi.executor.gNMIclient", return_value=mock_gnmi_client):
            result = await executor.apply_batch(states)
        assert result.total == 3
        assert result.succeeded == 2
        assert result.failed == 1

    @pytest.mark.asyncio
    async def test_batch_empty_raises(self):
        executor = _make_executor()
        with pytest.raises(ValueError, match="empty"):
            await executor.apply_batch([])

    @pytest.mark.asyncio
    async def test_batch_duplicate_device_ids_raises(self):
        shared_id = UUID("00000000-0000-0000-0000-000000000001")
        states = [_make_desired("d1", shared_id), _make_desired("d2", shared_id)]
        executor = _make_executor()
        with pytest.raises(ValueError, match="duplicate"):
            await executor.apply_batch(states)

    @pytest.mark.asyncio
    async def test_batch_results_keyed_by_device_id(self, mock_gnmi_client):
        states = [_make_desired(f"dev-{i}") for i in range(2)]
        executor = _make_executor()
        with patch("snapl_executor.gnmi.executor.gNMIclient", return_value=mock_gnmi_client):
            result = await executor.apply_batch(states)
        for ds in states:
            assert ds.device.id in result.results
