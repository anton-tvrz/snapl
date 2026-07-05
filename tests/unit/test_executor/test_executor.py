"""Unit tests for GnmiExecutor (T011, T019, T023, T026)."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from snapl_executor.gnmi.executor import GnmiExecutor
from snapl_executor.models import ApplyResult, BatchResult, DryRunResult

pytestmark = pytest.mark.unit


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


class TestApplyBatch:
    @pytest.mark.asyncio
    async def test_batch_all_succeed(self, mock_gnmi_client, make_desired):
        states = [make_desired(f"dev-{i}") for i in range(3)]
        executor = _make_executor()
        with patch("snapl_executor.gnmi.executor.gNMIclient", return_value=mock_gnmi_client):
            result = await executor.apply_batch(states)
        assert isinstance(result, BatchResult)
        assert result.total == 3
        assert result.succeeded == 3
        assert result.failed == 0

    @pytest.mark.asyncio
    async def test_batch_partial_failure(self, mock_gnmi_client, make_desired):
        states = [make_desired(f"dev-{i}") for i in range(3)]
        executor = _make_executor()
        _lock = threading.Lock()
        _calls: list[int] = []

        def failing_set(*args, **kwargs):
            with _lock:
                n = len(_calls)
                _calls.append(n)
            if n == 0:
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
    async def test_batch_duplicate_device_ids_raises(self, make_desired):
        shared_id = UUID("00000000-0000-0000-0000-000000000001")
        states = [make_desired("d1", shared_id), make_desired("d2", shared_id)]
        executor = _make_executor()
        with pytest.raises(ValueError, match="duplicate"):
            await executor.apply_batch(states)

    @pytest.mark.asyncio
    async def test_batch_results_keyed_by_device_id(self, mock_gnmi_client, make_desired):
        states = [make_desired(f"dev-{i}") for i in range(2)]
        executor = _make_executor()
        with patch("snapl_executor.gnmi.executor.gNMIclient", return_value=mock_gnmi_client):
            result = await executor.apply_batch(states)
        for ds in states:
            assert ds.device.id in result.results


class TestApplyBatchSerialization:
    @pytest.mark.asyncio
    async def test_apply_batch_serializes_sets_to_same_target(self, make_desired):
        """SR Linux rejects concurrent exclusive config sessions, so batch
        applies that resolve to the same dial host must run the gNMI sets
        one at a time."""
        import time as _time

        executor = _make_executor()
        in_flight = 0
        max_in_flight = 0
        lock = threading.Lock()

        def fake_set(host, payload):
            nonlocal in_flight, max_in_flight
            with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            _time.sleep(0.05)
            with lock:
                in_flight -= 1
            return "ok"

        # make_desired gives every device the same management_address, so
        # both applies resolve to one host and must share its lock.
        states = [make_desired("spine-01"), make_desired("spine-02")]
        with patch.object(executor, "_blocking_set", side_effect=fake_set):
            result = await executor.apply_batch(states)

        assert result.succeeded == 2
        assert max_in_flight == 1, "gNMI sets to the same target overlapped"


# ---------------------------------------------------------------------------
# #30 — per-device dial-host resolution
# ---------------------------------------------------------------------------


class TestDialHostResolution:
    """The dial target comes from the Device: lab_node_name first, then
    management_address, then the constructor host as a last-resort fallback
    (regression for #30 — a worker-wide instance must reach every device)."""

    @pytest.mark.asyncio
    async def test_apply_dials_lab_node_name_first(self, dcfabric_desired_state, mock_gnmi_client):
        dcfabric_desired_state.device.lab_node_name = "clab-dcfabric-spine-01"
        executor = _make_executor(host=None)
        with patch("snapl_executor.gnmi.executor.gNMIclient", return_value=mock_gnmi_client) as mock_cls:
            result = await executor.apply(dcfabric_desired_state)
        assert result.success is True
        assert mock_cls.call_args.kwargs["target"] == ("clab-dcfabric-spine-01", 57400)

    @pytest.mark.asyncio
    async def test_apply_dials_management_address_without_lab_node_name(self, dcfabric_desired_state, mock_gnmi_client):
        executor = _make_executor(host=None)
        with patch("snapl_executor.gnmi.executor.gNMIclient", return_value=mock_gnmi_client) as mock_cls:
            result = await executor.apply(dcfabric_desired_state)
        assert result.success is True
        assert mock_cls.call_args.kwargs["target"] == ("10.0.0.1", 57400)

    @pytest.mark.asyncio
    async def test_apply_falls_back_to_constructor_host(self, dcfabric_desired_state, mock_gnmi_client):
        dcfabric_desired_state.device.management_address = ""
        executor = _make_executor(host="192.0.2.7")
        with patch("snapl_executor.gnmi.executor.gNMIclient", return_value=mock_gnmi_client) as mock_cls:
            result = await executor.apply(dcfabric_desired_state)
        assert result.success is True
        assert mock_cls.call_args.kwargs["target"] == ("192.0.2.7", 57400)

    @pytest.mark.asyncio
    async def test_apply_without_any_dial_target_fails(self, dcfabric_desired_state):
        dcfabric_desired_state.device.management_address = ""
        executor = _make_executor(host=None)
        with patch("snapl_executor.gnmi.executor.gNMIclient") as mock_cls:
            result = await executor.apply(dcfabric_desired_state)
            mock_cls.assert_not_called()
        assert result.success is False
        assert "dial target" in (result.error or "")

    @pytest.mark.asyncio
    async def test_constructor_host_is_optional(self):
        executor = GnmiExecutor(password="test")  # pragma: allowlist secret
        assert executor is not None

    @pytest.mark.asyncio
    async def test_apply_batch_dials_each_device(self, make_desired, mock_gnmi_client):
        states = [make_desired("spine-01"), make_desired("leaf-01")]
        states[0].device.lab_node_name = "clab-dcfabric-spine-01"
        states[1].device.lab_node_name = "clab-dcfabric-leaf-01"
        executor = _make_executor(host=None)
        with patch("snapl_executor.gnmi.executor.gNMIclient", return_value=mock_gnmi_client) as mock_cls:
            result = await executor.apply_batch(states)
        assert result.succeeded == 2
        targets = {call.kwargs["target"] for call in mock_cls.call_args_list}
        assert targets == {("clab-dcfabric-spine-01", 57400), ("clab-dcfabric-leaf-01", 57400)}
