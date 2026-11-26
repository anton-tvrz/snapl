"""Unit tests for Source of Truth failures in ``load_states``."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from snapl_intent.exceptions import IntentAttributionError, IntentConnectionError
from snapl_presentation.exceptions import CliError, ConnectionCliError
from snapl_presentation.resolve import load_states

pytestmark = pytest.mark.unit

_SETTINGS = SimpleNamespace(infrahub_address="http://infrahub:8000")


async def test_unreachable_source_of_truth_is_a_connection_error():
    store = SimpleNamespace(get_desired_state=AsyncMock(side_effect=IntentConnectionError("down")))

    with pytest.raises(ConnectionCliError):
        await load_states(store, _SETTINGS)


async def test_unattributable_intent_is_not_reported_as_unreachable():
    """The stack is up and answering: telling the operator to start it is wrong."""
    store = SimpleNamespace(
        get_desired_state=AsyncMock(side_effect=IntentAttributionError("1 InterfacePhysical node(s) name no device"))
    )

    with pytest.raises(CliError) as excinfo:
        await load_states(store, _SETTINGS)

    assert not isinstance(excinfo.value, ConnectionCliError)
    assert "1 InterfacePhysical" in excinfo.value.message
    assert "unreachable" not in excinfo.value.render()
