"""Integration tests for GnmiCollector against a live SR Linux node (T016+)."""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.integration

_IFACE_PATH = "/interface"
_BGP_PATH = "/network-instance[name=default]/protocols/bgp/neighbor"


# ---------------------------------------------------------------------------
# T016 — US1: get_running_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_running_config_success(srlinux_collector, srlinux_device):
    result = await srlinux_collector.get_running_config(srlinux_device)
    assert result.success is True, f"expected success, got error: {result.error}"
    assert result.data != {}
    assert "/" in result.data
    assert result.error is None
    assert result.duration_ms > 0
    assert result.timestamp is not None


# ---------------------------------------------------------------------------
# T019 (US2) — targeted collect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_targeted_paths(srlinux_collector, srlinux_device):
    result = await srlinux_collector.collect(srlinux_device, paths=[_IFACE_PATH, _BGP_PATH])
    assert result.success is True, f"expected success, got error: {result.error}"
    assert _IFACE_PATH in result.data or _BGP_PATH in result.data
    assert result.paths == [_IFACE_PATH, _BGP_PATH]


# ---------------------------------------------------------------------------
# T022 (US3) — collect_batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_batch_single_device(srlinux_collector, srlinux_device):
    batch = await srlinux_collector.collect_batch([srlinux_device], paths=[_IFACE_PATH])
    assert batch.total == 1
    assert batch.succeeded == 1
    assert batch.failed == 0
    assert srlinux_device.id in batch.results


# ---------------------------------------------------------------------------
# T023 — SC-001: get_running_config completes in <30s
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_running_config_completes_within_30s(srlinux_collector, srlinux_device):
    start = time.monotonic()
    result = await srlinux_collector.get_running_config(srlinux_device)
    elapsed = time.monotonic() - start
    assert result.success is True, f"collect failed: {result.error}"
    assert elapsed < 30, f"get_running_config took {elapsed:.1f}s, expected <30s"


# ---------------------------------------------------------------------------
# T024 — SC-002: targeted collect completes in <5s
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_targeted_collect_completes_within_5s(srlinux_collector, srlinux_device):
    start = time.monotonic()
    result = await srlinux_collector.collect(srlinux_device, paths=[_IFACE_PATH])
    elapsed = time.monotonic() - start
    assert result.success is True, f"collect failed: {result.error}"
    assert elapsed < 5, f"targeted collect took {elapsed:.1f}s, expected <5s"
