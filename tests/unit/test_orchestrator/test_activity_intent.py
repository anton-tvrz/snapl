"""Unit tests for the fetch_desired_state and fetch_devices_for_use_case activities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from snapl_intent.exceptions import IntentConnectionError, IntentNotFoundError
from snapl_orchestrator.activities import Activities, get_activities, set_activities
from snapl_orchestrator.activities.intent import (
    fetch_desired_state,
    fetch_devices_for_use_case,
)
from snapl_orchestrator.exceptions import OrchestratorConfigError

pytestmark = pytest.mark.unit


def _install_activities(*, intent_store=None) -> Activities:
    activities = Activities(
        intent_store=intent_store or MagicMock(name="IntentStore"),
        executor=MagicMock(name="Executor"),
        collector=MagicMock(name="Collector"),
        observer=MagicMock(name="Observer"),
        audit_log=MagicMock(name="AuditLog"),
    )
    set_activities(activities)
    return activities


def teardown_function() -> None:
    # Reset module-level state between tests.
    import snapl_orchestrator.activities as a

    a._activities = None


def test_get_activities_raises_when_uninstalled() -> None:
    with pytest.raises(OrchestratorConfigError):
        get_activities()


@pytest.mark.asyncio
async def test_fetch_desired_state_returns_first_state(dcfabric_desired_state) -> None:
    intent_store = MagicMock(name="IntentStore")
    intent_store.get_desired_state = AsyncMock(return_value=[dcfabric_desired_state])
    _install_activities(intent_store=intent_store)

    device_id = dcfabric_desired_state.device.id
    result = await fetch_desired_state(device_id)

    assert result == dcfabric_desired_state
    intent_store.get_desired_state.assert_awaited_once_with(device_id=device_id)


@pytest.mark.asyncio
async def test_fetch_desired_state_raises_not_found_when_empty() -> None:
    intent_store = MagicMock(name="IntentStore")
    intent_store.get_desired_state = AsyncMock(return_value=[])
    _install_activities(intent_store=intent_store)

    with pytest.raises(IntentNotFoundError):
        await fetch_desired_state(uuid4())


@pytest.mark.asyncio
async def test_fetch_desired_state_propagates_connection_error() -> None:
    intent_store = MagicMock(name="IntentStore")
    intent_store.get_desired_state = AsyncMock(side_effect=IntentConnectionError("infrahub down"))
    _install_activities(intent_store=intent_store)

    with pytest.raises(IntentConnectionError):
        await fetch_desired_state(uuid4())


@pytest.mark.asyncio
async def test_fetch_devices_for_use_case_returns_device_list(make_desired) -> None:
    states = [make_desired(name) for name in ("spine-01", "spine-02", "leaf-01")]
    intent_store = MagicMock(name="IntentStore")
    intent_store.get_desired_state = AsyncMock(return_value=states)
    _install_activities(intent_store=intent_store)

    devices = await fetch_devices_for_use_case("dcfabric")

    assert {d.id for d in devices} == {s.device.id for s in states}
    intent_store.get_desired_state.assert_awaited_once_with(use_case="dcfabric")


@pytest.mark.asyncio
async def test_fetch_devices_for_use_case_empty_returns_empty_list() -> None:
    intent_store = MagicMock(name="IntentStore")
    intent_store.get_desired_state = AsyncMock(return_value=[])
    _install_activities(intent_store=intent_store)

    devices = await fetch_devices_for_use_case("unknown-use-case")
    assert devices == []
