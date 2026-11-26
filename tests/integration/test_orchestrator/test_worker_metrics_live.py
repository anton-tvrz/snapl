"""The worker's metrics endpoint, scraped for real (spec 012, T006, R1).

A test-owned Temporal dev server (``start_local``) runs on a runtime built by
``build_runtime`` on a free port. A worker runs the real ``apply_config``
activity over a fake executor, then the test reads the endpoint over HTTP.
Neither the lab nor the maintainer's port 18091 is touched. ``start_local``
downloads the dev-server binary on first use; the test skips if that is not
possible.
"""

from __future__ import annotations

import asyncio
import socket
from datetime import timedelta
from uuid import uuid4

from temporalio import workflow

# The sandbox re-imports this module to validate the workflow below; nothing
# but the workflow itself may run sandboxed.
with workflow.unsafe.imports_passed_through():
    from unittest.mock import AsyncMock, MagicMock

    import httpx
    import pytest
    from temporalio.contrib.pydantic import pydantic_data_converter
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from snapl_executor.models import ApplyResult
    from snapl_intent.models import DesiredState, Device
    from snapl_orchestrator import activities as activities_module
    from snapl_orchestrator.activities import Activities, set_activities
    from snapl_orchestrator.activities.executor import apply_config
    from snapl_orchestrator.worker.metrics import build_runtime
    from snapl_orchestrator.worker.sandbox import build_workflow_runner

pytestmark = pytest.mark.integration

TASK_QUEUE = "snapl-metrics-it"


@workflow.defn(name="ApplyOnce")
class ApplyOnceWorkflow:
    @workflow.run
    async def run(self, desired: DesiredState) -> ApplyResult:
        return await workflow.execute_activity(apply_config, desired, start_to_close_timeout=timedelta(seconds=30))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def metrics_port() -> int:
    return _free_port()


@pytest.fixture
async def local_env(metrics_port: int):
    runtime = build_runtime(port=metrics_port, bind="127.0.0.1")
    assert runtime is not None, "a free port must give a metrics runtime"
    try:
        env = await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter, runtime=runtime)
    except Exception as exc:
        pytest.skip(f"Temporal dev server unavailable: {exc}")
    async with env:
        yield env


@pytest.fixture
def desired() -> DesiredState:
    device = Device(
        id=uuid4(),
        name="leaf-it-01",
        management_address="192.0.2.10",
        role="leaf",
        use_case="dcfabric-it",
    )
    return DesiredState(device=device)


@pytest.fixture
def fake_executor(desired: DesiredState):
    executor = MagicMock()
    executor.apply = AsyncMock(
        return_value=ApplyResult(
            device_id=desired.device.id,
            device_name=desired.device.name,
            success=True,
            payload={},
            duration_ms=1,
        )
    )
    set_activities(
        Activities(
            intent_store=MagicMock(),
            executor=executor,
            collector=MagicMock(),
            observer=MagicMock(),
            audit_log=MagicMock(),
        )
    )
    yield executor
    activities_module._activities = None


async def _scrape_until(url: str, *needles: str, timeout: float = 10.0) -> str:
    """The exporter may lag the activity a little; poll until every needle shows."""
    deadline = asyncio.get_running_loop().time() + timeout
    body = ""
    async with httpx.AsyncClient(timeout=2.0) as http:
        while asyncio.get_running_loop().time() < deadline:
            body = (await http.get(url)).text
            if all(n in body for n in needles):
                return body
            await asyncio.sleep(0.25)
    return body


async def test_endpoint_carries_gnmi_timing_and_sdk_metrics(
    local_env, metrics_port: int, desired: DesiredState, fake_executor
) -> None:
    async with Worker(
        local_env.client,
        task_queue=TASK_QUEUE,
        workflows=[ApplyOnceWorkflow],
        activities=[apply_config],
        workflow_runner=build_workflow_runner(),
    ):
        result = await local_env.client.execute_workflow(
            ApplyOnceWorkflow.run, desired, id=f"apply-once-{uuid4()}", task_queue=TASK_QUEUE
        )
        assert result.success is True

        body = await _scrape_until(
            f"http://127.0.0.1:{metrics_port}/metrics",
            "snapl_gnmi_call_duration_count{",
            "temporal_workflow_endtoend_latency",
        )

    [count_line] = [line for line in body.splitlines() if line.startswith("snapl_gnmi_call_duration_count{")]
    for label in (
        'device="leaf-it-01"',
        'use_case="dcfabric-it"',
        'operation="configure"',
        'outcome="ok"',
        'activity_type="apply_config"',
    ):
        assert label in count_line, f"{label} missing from {count_line}"
    assert count_line.rsplit(" ", 1)[1] == "1"
    assert "temporal_workflow_endtoend_latency" in body
    fake_executor.apply.assert_awaited_once()
