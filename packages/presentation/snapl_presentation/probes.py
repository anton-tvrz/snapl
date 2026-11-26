"""Connection helpers shared by the CLI and the dashboard (spec 010 R6).

Both surfaces reach Temporal and the Source of Truth the same way, so they share
one implementation: a TCP probe ahead of every Temporal dial, the poller count
behind "is a worker running", and the SoT identity check (#107).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from snapl_presentation.exceptions import ConnectionCliError

if TYPE_CHECKING:
    from snapl_presentation.settings import CliSettings

# Temporal keeps listing a stopped worker's pollers for minutes (#196), so a
# poller counts only if Temporal heard from it this recently. An idle worker
# long-polls, and its last poll was measured at up to about 60 s old on the lab.
POLLER_STALE_AFTER = timedelta(seconds=90)


async def probe(host_port: str, timeout: float) -> str | None:
    """TCP-reachability probe. Returns a failure reason, or None if reachable."""
    host, _, port = host_port.rpartition(":")
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host or "localhost", int(port or 18033)),
            timeout=timeout,
        )
    except TimeoutError:
        return f"no response within {timeout:g}s"
    except (OSError, ValueError) as exc:
        return str(exc)
    writer.close()
    return None


async def connect(settings: CliSettings):
    """Temporal client with the pydantic converter wired in (FR-008).

    Probes TCP first. ``Client.connect`` retries its initial handshake forever
    and takes no connect_timeout, and wrapping it in ``asyncio.wait_for`` does
    not help: the Rust core blocks the event loop, so the timer never fires and
    the CLI hangs with no output — the exact opposite of FR-014. A plain
    asyncio socket probe is cancellable, costs milliseconds, and turns "nothing
    is listening" into an immediate, specific message. The wait_for below still
    guards the case where the port accepts but the handshake stalls.
    """
    from snapl_orchestrator.worker.client import build_client  # noqa: PLC0415

    failure = await probe(settings.temporal_host, settings.connect_timeout)
    if failure is not None:
        raise ConnectionCliError(
            subsystem="Temporal",
            address=settings.temporal_host,
            env_var="TEMPORAL_HOST",
            cause=failure,
        )

    try:
        return await asyncio.wait_for(
            build_client(target=settings.temporal_host, namespace=settings.temporal_namespace),
            timeout=settings.connect_timeout,
        )
    except (TimeoutError, Exception) as exc:
        cause = f"no response within {settings.connect_timeout:g}s" if isinstance(exc, TimeoutError) else str(exc)
        raise ConnectionCliError(
            subsystem="Temporal",
            address=settings.temporal_host,
            env_var="TEMPORAL_HOST",
            cause=cause,
        ) from exc


async def identify_sot(store, *, address: str):
    """Classify the Infrahub a store points at (#107)."""
    from snapl_intent.infrahub.identity import identify  # noqa: PLC0415

    return await identify(store.client, address=address)


async def count_pollers(client: Any, settings: CliSettings) -> int:
    """How many workers are polling our task queue, counting only recent pollers."""
    from temporalio.api.enums.v1 import TaskQueueType  # noqa: PLC0415
    from temporalio.api.taskqueue.v1 import TaskQueue  # noqa: PLC0415
    from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest  # noqa: PLC0415

    request = DescribeTaskQueueRequest(
        namespace=settings.temporal_namespace,
        task_queue=TaskQueue(name=settings.task_queue),
        task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
    )
    response = await asyncio.wait_for(
        client.service_client.workflow_service.describe_task_queue(request),
        timeout=settings.connect_timeout,
    )
    cutoff = datetime.now(UTC) - POLLER_STALE_AFTER
    return sum(1 for poller in response.pollers if poller.last_access_time.ToDatetime(UTC) >= cutoff)
