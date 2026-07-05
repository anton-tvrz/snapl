"""Integration tests for GnmiExecutor against a live SR Linux node (T018, T022, T025, T029-T031)."""

from __future__ import annotations

import os
import time

import pytest

from snapl_executor.models import ApplyResult, BatchResult, DryRunResult

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lab_host() -> str:
    """The executor dials the device's own addressing (see #30), so test
    devices must carry the reachable lab host, not the seed mgmt IP."""
    return os.environ.get("SRLINUX_HOST", "clab-dcfabric-spine-01")


def _make_spine_desired(management_address: str | None = None):
    """Build a minimal DesiredState for spine-01 from the dcfabric topology."""
    from uuid import UUID

    from snapl_intent.models import BGPSession, DesiredState, Device, Interface

    device_id = UUID("00000000-0000-0000-0000-000000000001")
    device = Device(
        id=device_id,
        name="spine-01",
        management_address=management_address or _lab_host(),
        role="spine",
        use_case="dcfabric",
        platform="nokia-srlinux",
    )
    interfaces = [
        Interface(
            id=UUID("00000000-0000-0000-0000-000000000011"),
            device_id=device_id,
            name="ethernet-1/1",
            ip_address="10.1.1.0",
            prefix_length=31,
            enabled=True,
            mtu=9232,
        )
    ]
    bgp_sessions = [
        BGPSession(
            id=UUID("00000000-0000-0000-0000-000000000021"),
            device_id=device_id,
            local_asn=65000,
            peer_address="10.1.1.1",
            peer_asn=65001,
            peer_group="underlay-ipv4",
            address_family="ipv4_unicast",
            enabled=True,
        )
    ]
    return DesiredState(device=device, interfaces=interfaces, bgp_sessions=bgp_sessions)


# ---------------------------------------------------------------------------
# T018 — apply()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_success(srlinux_executor):
    desired = _make_spine_desired()
    result = await srlinux_executor.apply(desired)
    assert isinstance(result, ApplyResult)
    assert result.success is True, f"apply failed: {result.error}"
    assert result.payload
    assert result.is_rollback is False


# ---------------------------------------------------------------------------
# T022 — dry_run()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_no_device_change(srlinux_executor):
    desired = _make_spine_desired()
    result = await srlinux_executor.dry_run(desired)
    assert isinstance(result, DryRunResult)
    assert result.success is True, f"dry_run render failed: {result.render_error}"
    assert result.payload is not None


# ---------------------------------------------------------------------------
# T025 — rollback()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_is_rollback_flag(srlinux_executor):
    desired = _make_spine_desired()
    result = await srlinux_executor.rollback(desired)
    assert isinstance(result, ApplyResult)
    assert result.is_rollback is True
    assert result.success is True, f"rollback failed: {result.error}"


# ---------------------------------------------------------------------------
# T029 — apply_batch()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_batch(srlinux_executor):
    """Batch apply to two states (same executor = same device, but unique IDs)."""
    from uuid import UUID

    from snapl_intent.models import BGPSession, DesiredState, Device, Interface

    def _desired(device_name: str, dev_id_int: int) -> DesiredState:
        dev_id = UUID(int=dev_id_int)
        device = Device(
            id=dev_id,
            name=device_name,
            management_address=_lab_host(),
            role="spine",
            use_case="dcfabric",
            platform="nokia-srlinux",
        )
        ifaces = [
            Interface(
                id=UUID(int=dev_id_int + 100),
                device_id=dev_id,
                name="ethernet-1/1",
                ip_address="10.1.1.0",
                prefix_length=31,
                enabled=True,
                mtu=9232,
            )
        ]
        sessions = [
            BGPSession(
                id=UUID(int=dev_id_int + 200),
                device_id=dev_id,
                local_asn=65000,
                peer_address="10.1.1.1",
                peer_asn=65001,
                enabled=True,
                address_family="ipv4_unicast",
            )
        ]
        return DesiredState(device=device, interfaces=ifaces, bgp_sessions=sessions)

    states = [_desired("spine-01", 1), _desired("spine-02", 2)]
    result = await srlinux_executor.apply_batch(states)
    assert isinstance(result, BatchResult)
    assert result.total == 2
    assert result.succeeded == 2
    assert result.failed == 0


# ---------------------------------------------------------------------------
# T030 — SC-001 performance: <30s single device apply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_completes_within_30s(srlinux_executor):
    desired = _make_spine_desired()
    start = time.monotonic()
    result = await srlinux_executor.apply(desired)
    elapsed = time.monotonic() - start
    assert elapsed < 30.0, f"apply took {elapsed:.1f}s (SC-001 limit: 30s)"
    assert result.success is True


# ---------------------------------------------------------------------------
# T031 — SC-007 timeout: unreachable device returns failure within 30s
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unreachable_device_returns_failure_within_timeout():
    from snapl_executor.gnmi.executor import GnmiExecutor

    executor = GnmiExecutor(
        host="127.0.0.1",
        port=19999,
        username="admin",
        password="test",  # pragma: allowlist secret
        insecure=True,
        timeout=5,
    )
    desired = _make_spine_desired(management_address="127.0.0.1")
    start = time.monotonic()
    result = await executor.apply(desired)
    elapsed = time.monotonic() - start
    assert result.success is False, "Expected failure for unreachable host"
    assert elapsed < 30.0, f"took {elapsed:.1f}s — should fail fast (SC-007)"
