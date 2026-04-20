"""Async Infrahub client wrapper.

Wraps :class:`infrahub_sdk.InfrahubClient` with project-specific defaults:
- Connection details sourced from ``INFRAHUB_ADDRESS`` / ``INFRAHUB_API_TOKEN``.
- Configurable 10-second default timeout (per SC-007).
- SDK exceptions are mapped to domain exceptions at the call site by the
  store methods that use this client.

This file deliberately stays thin — the store layer is responsible for all
semantics (upserts, dependency ordering, schema batching). Keeping the client
as a dumb factory makes it trivial to substitute a mock in tests.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from snapl_intent.exceptions import IntentConnectionError

if TYPE_CHECKING:
    from infrahub_sdk import InfrahubClient


DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_ADDRESS = "http://localhost:8000"


def _get_env(name: str, *, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def build_client(
    *,
    address: str | None = None,
    api_token: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> InfrahubClient:
    """Construct an :class:`InfrahubClient` from explicit args or env vars.

    Args:
        address: Infrahub HTTP address (default: ``$INFRAHUB_ADDRESS`` or ``http://localhost:8000``).
        api_token: API token (default: ``$INFRAHUB_API_TOKEN``). Required for
            authenticated deployments; local Docker development may leave this
            unset.
        timeout: Request timeout in seconds (default: 10).

    Returns:
        Configured async Infrahub client.

    Raises:
        IntentConnectionError: The ``infrahub_sdk`` package is not importable.
    """
    try:
        from infrahub_sdk import Config, InfrahubClient
    except ImportError as exc:
        raise IntentConnectionError(
            "infrahub-sdk is not installed — add infrahub-sdk[ctl] as a dependency"
        ) from exc

    resolved_address = address or _get_env("INFRAHUB_ADDRESS", default=DEFAULT_ADDRESS)
    resolved_token = api_token or _get_env("INFRAHUB_API_TOKEN")

    config = Config(
        address=resolved_address,
        api_token=resolved_token,
        timeout=timeout,
    )
    return InfrahubClient(config=config)


@asynccontextmanager
async def client_session(
    *,
    address: str | None = None,
    api_token: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> AsyncIterator[InfrahubClient]:
    """Async context manager yielding a configured Infrahub client.

    Usage::

        async with client_session() as client:
            ...

    Connection errors raised by the SDK are re-raised as
    :class:`IntentConnectionError`.
    """
    client = build_client(address=address, api_token=api_token, timeout=timeout)
    try:
        yield client
    except IntentConnectionError:
        raise
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        raise IntentConnectionError(f"Infrahub operation failed: {message}") from exc
