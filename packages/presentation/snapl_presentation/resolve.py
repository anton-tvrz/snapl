"""Device-name resolution against the Source of Truth (spec 006 FR-007).

Operators name devices; workflows take UUIDs. This is the only place that gap
is closed, so no command has to know how — and so "unknown name" and
"ambiguous name" are reported the same way everywhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from snapl_intent.exceptions import IntentError
from snapl_presentation.exceptions import AmbiguousDeviceError, ConnectionCliError, UnknownDeviceError

if TYPE_CHECKING:
    from uuid import UUID

    from snapl_intent.abc import IntentStore
    from snapl_intent.models import DesiredState
    from snapl_presentation.settings import CliSettings


def build_store(settings: CliSettings) -> IntentStore:
    """An InfrahubIntentStore from resolved settings."""
    from snapl_intent.infrahub.client import build_client  # noqa: PLC0415
    from snapl_intent.infrahub.store import InfrahubIntentStore  # noqa: PLC0415

    client = build_client(address=settings.infrahub_address, api_token=settings.infrahub_token)
    return InfrahubIntentStore(client=client)


async def load_states(store: IntentStore, settings: CliSettings, *, use_case: str | None = None) -> list[DesiredState]:
    """Fetch desired state, converting SoT failures into an operator message."""
    try:
        return await store.get_desired_state(use_case=use_case)
    except IntentError as exc:
        raise ConnectionCliError(
            subsystem="Source of Truth",
            address=settings.infrahub_address,
            env_var="INFRAHUB_ADDRESS",
            cause=str(exc),
        ) from exc


def resolve_device(states: list[DesiredState], name: str) -> UUID:
    """Map one device name to its id.

    Refuses on ambiguity rather than picking a winner: silently configuring the
    wrong device is worse than making the operator type a --use-case.
    """
    matches = [state for state in states if state.device.name == name]
    if not matches:
        raise UnknownDeviceError(name, known=[s.device.name for s in states])
    if len(matches) > 1:
        raise AmbiguousDeviceError(name, use_cases=[s.device.use_case for s in matches])
    return matches[0].device.id


def resolve_devices(states: list[DesiredState], names: list[str]) -> list[UUID]:
    return [resolve_device(states, name) for name in names]
