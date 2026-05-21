"""Smoke tests — incremental workflow + activity validation."""

from __future__ import annotations

import datetime as _dt
from uuid import uuid4

import pydantic_core  # noqa: F401 — pre-import so workflow sandbox can find it
import pytest
from temporalio import activity, workflow
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from snapl_orchestrator.activities import Activities, set_activities
from snapl_orchestrator.activities.audit import record_audit_event
from snapl_orchestrator.audit.memory import InMemoryAuditLog
from snapl_orchestrator.models import AuditEvent, AuditEventType

pytestmark = pytest.mark.unit


@workflow.defn(name="Smoke")
class SmokeWorkflow:
    @workflow.run
    async def run(self, name: str) -> str:
        return f"hello {name}"


@pytest.mark.asyncio
async def test_smoke_workflow_round_trip() -> None:
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter,
        ) as env,
        Worker(env.client, task_queue="smoke", workflows=[SmokeWorkflow]),
    ):
        result = await env.client.execute_workflow(
            SmokeWorkflow.run,
            "world",
            id="smoke-test",
            task_queue="smoke",
        )
    assert result == "hello world"


@activity.defn(name="echo_str")
async def echo_str(value: str) -> str:
    return f"echo:{value}"


@workflow.defn(name="EchoWf")
class EchoWorkflow:
    @workflow.run
    async def run(self, value: str) -> str:
        return await workflow.execute_activity(
            echo_str,
            value,
            start_to_close_timeout=_dt.timedelta(seconds=10),
        )


@pytest.mark.asyncio
async def test_workflow_calls_simple_activity() -> None:
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter,
        ) as env,
        Worker(
            env.client,
            task_queue="echo",
            workflows=[EchoWorkflow],
            activities=[echo_str],
        ),
    ):
        out = await env.client.execute_workflow(
            EchoWorkflow.run,
            "hi",
            id="echo-test",
            task_queue="echo",
        )
    assert out == "echo:hi"


@workflow.defn(name="AuditWf")
class AuditWorkflow:
    @workflow.run
    async def run(self) -> str:
        event = AuditEvent(
            event_id=workflow.uuid4(),
            workflow_id=workflow.info().workflow_id,
            workflow_type="AuditWf",
            target_id=workflow.uuid4(),
            event_type=AuditEventType.WORKFLOW_STARTED,
            timestamp=workflow.now(),
        )
        await workflow.execute_activity(
            record_audit_event,
            event,
            start_to_close_timeout=_dt.timedelta(seconds=10),
        )
        return "done"


def _teardown() -> None:
    import snapl_orchestrator.activities as a

    a._activities = None


@workflow.defn(name="FetchWf")
class FetchIntentWorkflow:
    @workflow.run
    async def run(self, device_id) -> str:
        from snapl_orchestrator.activities.intent import fetch_desired_state

        desired = await workflow.execute_activity(
            fetch_desired_state,
            device_id,
            start_to_close_timeout=_dt.timedelta(seconds=10),
        )
        return desired.device.name


@pytest.mark.asyncio
async def test_workflow_calls_fetch_desired_state(dcfabric_desired_state) -> None:
    """Verify the workflow can serialize a UUID arg and deserialize a DesiredState return."""
    from unittest.mock import AsyncMock, MagicMock

    from snapl_orchestrator.activities.intent import fetch_desired_state

    intent_store = MagicMock()
    intent_store.get_desired_state = AsyncMock(return_value=[dcfabric_desired_state])
    set_activities(
        Activities(
            intent_store=intent_store,
            executor=object(),
            collector=object(),
            observer=object(),
            audit_log=InMemoryAuditLog(),
        )
    )
    try:
        async with (
            await WorkflowEnvironment.start_time_skipping(
                data_converter=pydantic_data_converter,
            ) as env,
            Worker(
                env.client,
                task_queue="fetch-test",
                workflows=[FetchIntentWorkflow],
                activities=[fetch_desired_state],
            ),
        ):
            out = await env.client.execute_workflow(
                FetchIntentWorkflow.run,
                dcfabric_desired_state.device.id,
                id=f"fetch-{uuid4()}",
                task_queue="fetch-test",
            )
    finally:
        _teardown()
    assert out == dcfabric_desired_state.device.name


@pytest.mark.asyncio
async def test_workflow_with_pydantic_audit_event() -> None:
    log = InMemoryAuditLog()
    set_activities(
        Activities(
            intent_store=object(),
            executor=object(),
            collector=object(),
            observer=object(),
            audit_log=log,
        )
    )
    try:
        async with (
            await WorkflowEnvironment.start_time_skipping(
                data_converter=pydantic_data_converter,
            ) as env,
            Worker(
                env.client,
                task_queue="audit-test",
                workflows=[AuditWorkflow],
                activities=[record_audit_event],
            ),
        ):
            out = await env.client.execute_workflow(
                AuditWorkflow.run,
                id=f"audit-test-{uuid4()}",
                task_queue="audit-test",
            )
    finally:
        _teardown()
    assert out == "done"
    assert len(log) == 1
