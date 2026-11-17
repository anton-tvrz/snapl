"""The connection helpers shared by the CLI and the dashboard (spec 010 T005).

They moved out of cli.py so both surfaces use one implementation. These tests
pin the behaviour they had there.
"""

from __future__ import annotations

import asyncio
import socket
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio.api.taskqueue.v1 import PollerInfo
from temporalio.api.workflowservice.v1 import DescribeTaskQueueResponse

from snapl_presentation import probes
from snapl_presentation.exceptions import ConnectionCliError
from snapl_presentation.settings import CliSettings

pytestmark = pytest.mark.unit


def _closed_port() -> int:
    """A port nothing listens on: bind, read the number, release it."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.asyncio
async def test_probe_returns_none_for_a_listening_port() -> None:
    server = await asyncio.start_server(lambda _r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        assert await probes.probe(f"127.0.0.1:{port}", timeout=2.0) is None


@pytest.mark.asyncio
async def test_probe_returns_a_reason_for_a_refused_port() -> None:
    reason = await probes.probe(f"127.0.0.1:{_closed_port()}", timeout=2.0)

    assert reason


@pytest.mark.asyncio
async def test_probe_returns_a_reason_when_the_connect_times_out() -> None:
    with patch.object(probes.asyncio, "open_connection", new=AsyncMock(side_effect=TimeoutError)):
        reason = await probes.probe("127.0.0.1:18033", timeout=0.5)

    assert reason == "no response within 0.5s"


@pytest.mark.asyncio
async def test_connect_names_temporal_host_without_dialing_when_the_probe_fails() -> None:
    settings = CliSettings(temporal_host=f"127.0.0.1:{_closed_port()}", connect_timeout=2.0)
    build_client = AsyncMock()

    with (
        patch("snapl_orchestrator.worker.client.build_client", new=build_client),
        pytest.raises(ConnectionCliError) as excinfo,
    ):
        await probes.connect(settings)

    assert excinfo.value.env_var == "TEMPORAL_HOST"
    assert excinfo.value.address == settings.temporal_host
    build_client.assert_not_called()


@pytest.mark.asyncio
async def test_connect_returns_the_client_when_the_probe_passes() -> None:
    settings = CliSettings(temporal_host="127.0.0.1:18033")
    client = MagicMock()

    with (
        patch.object(probes, "probe", new=AsyncMock(return_value=None)),
        patch("snapl_orchestrator.worker.client.build_client", new=AsyncMock(return_value=client)),
    ):
        assert await probes.connect(settings) is client


@pytest.mark.asyncio
async def test_count_pollers_counts_the_task_queue_pollers() -> None:
    client = _describing([_poller(seconds_ago=1), _poller(seconds_ago=30)])

    assert await probes.count_pollers(client, CliSettings()) == 2


@pytest.mark.asyncio
async def test_count_pollers_ignores_a_poller_temporal_last_heard_from_minutes_ago() -> None:
    """Temporal lists a stopped worker's pollers for minutes after it stops (#196)."""
    client = _describing([_poller(seconds_ago=5), _poller(seconds_ago=180), _poller(seconds_ago=240)])

    assert await probes.count_pollers(client, CliSettings()) == 1


@pytest.mark.asyncio
async def test_count_pollers_counts_an_idle_worker_between_long_polls() -> None:
    """An idle worker's last poll is up to about 60 s old (measured on the lab, #196)."""
    client = _describing([_poller(seconds_ago=65)])

    assert await probes.count_pollers(client, CliSettings()) == 1


@pytest.mark.asyncio
async def test_count_pollers_is_zero_once_every_poller_is_stale() -> None:
    client = _describing([_poller(seconds_ago=probes.POLLER_STALE_AFTER.total_seconds() + 5)])

    assert await probes.count_pollers(client, CliSettings()) == 0


def _poller(*, seconds_ago: float) -> PollerInfo:
    poller = PollerInfo(identity=f"worker-{seconds_ago:g}s")
    poller.last_access_time.FromDatetime(datetime.now(UTC) - timedelta(seconds=seconds_ago))
    return poller


def _describing(pollers: list[PollerInfo]) -> MagicMock:
    client = MagicMock()
    client.service_client.workflow_service.describe_task_queue = AsyncMock(
        return_value=DescribeTaskQueueResponse(pollers=pollers)
    )
    return client


@pytest.mark.asyncio
async def test_identify_sot_delegates_to_the_intent_identity_check() -> None:
    store = MagicMock()
    identity = MagicMock()
    identify = AsyncMock(return_value=identity)

    with patch("snapl_intent.infrahub.identity.identify", new=identify):
        result = await probes.identify_sot(store, address="http://localhost:18000")

    assert result is identity
    identify.assert_awaited_once_with(store.client, address="http://localhost:18000")
