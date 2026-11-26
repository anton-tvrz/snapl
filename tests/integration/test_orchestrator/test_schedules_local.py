"""One drift-schedule round trip against a real Temporal dev server (spec 009, research R11).

The time-skipping test server that the unit tests use has no Schedules, so the
schedule functions are unit-tested against a fake client. This test proves the
real server accepts the Schedule we build, and that the response mode survives
the trip back through the pydantic converter. ``start_local`` downloads the
dev-server binary on first use; the test skips if that is not possible.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

from snapl_orchestrator.audit.memory import InMemoryAuditLog
from snapl_orchestrator.models import DriftResponseMode
from snapl_orchestrator.schedules import (
    delete_drift_schedule,
    describe_drift_schedule,
    ensure_drift_schedule,
    find_drift_schedule,
    pause_drift_schedule,
    resume_drift_schedule,
    set_drift_response,
    trigger_drift_schedule,
)

pytestmark = pytest.mark.integration

USE_CASE = "dcfabric-it"


@pytest.fixture
async def local_env():
    try:
        env = await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter)
    except Exception as exc:
        pytest.skip(f"Temporal dev server unavailable: {exc}")
    async with env:
        yield env


async def test_drift_schedule_round_trip(local_env) -> None:
    client = local_env.client
    audit = InMemoryAuditLog()
    common = {"use_case_id": USE_CASE, "audit_log": audit, "actor": "integration"}

    created = await ensure_drift_schedule(client, task_queue="snapl-it", interval=timedelta(minutes=5), **common)
    assert created.mode is DriftResponseMode.REPORT
    assert created.interval == timedelta(minutes=5)
    assert created.paused is False
    assert created.next_runs, "the server should report upcoming firings"

    await pause_drift_schedule(client, reason="integration: hold", **common)
    paused = await describe_drift_schedule(client, use_case_id=USE_CASE)
    assert (paused.paused, paused.note) == (True, "integration: hold")

    # Re-running setup changes only the interval: pause and mode survive (FR-004).
    updated = await ensure_drift_schedule(client, task_queue="snapl-it", interval=timedelta(minutes=2), **common)
    assert updated.interval == timedelta(minutes=2)
    assert (updated.paused, updated.note, updated.mode) == (True, "integration: hold", DriftResponseMode.REPORT)

    await resume_drift_schedule(client, **common)
    assert (await describe_drift_schedule(client, use_case_id=USE_CASE)).paused is False

    assert await trigger_drift_schedule(client, **common) is DriftResponseMode.REPORT

    switched = await set_drift_response(client, mode=DriftResponseMode.REMEDIATE, **common)
    assert switched.mode is DriftResponseMode.REMEDIATE
    assert switched.interval == timedelta(minutes=2)

    await delete_drift_schedule(client, **common)
    assert await find_drift_schedule(client, use_case_id=USE_CASE) is None

    changes = [event.payload["change"] for event in await audit.query_by_workflow(f"drift-response-{USE_CASE}")]
    assert changes == ["created", "paused", "interval_changed", "resumed", "triggered", "response_changed", "deleted"]
