"""Unit tests for snapl_orchestrator.schedules.

Temporal's time-skipping test server does not implement Schedules (research
R11), so the builder is tested as a pure function and the control functions
against a fake client. One real round trip lives in the integration suite.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from temporalio.api.common.v1 import Payload
from temporalio.client import (
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    ScheduleState,
    WorkflowExecutionStatus,
)
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.service import RPCError, RPCStatusCode

from snapl_observability.models import DriftItem, DriftReport, DriftStatus
from snapl_orchestrator.audit.memory import InMemoryAuditLog
from snapl_orchestrator.exceptions import ScheduleNotFoundError
from snapl_orchestrator.models import (
    AuditEvent,
    AuditEventType,
    DriftResponseMode,
    DriftResponseRequest,
    DriftResponseResult,
    DriftScanResult,
)
from snapl_orchestrator.schedules import (
    DEFAULT_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    build_drift_schedule,
    delete_drift_schedule,
    describe_drift_schedule,
    ensure_drift_schedule,
    find_drift_schedule,
    pause_drift_schedule,
    resume_drift_schedule,
    schedule_id_for,
    trigger_drift_schedule,
)

pytestmark = pytest.mark.unit

_QUEUE = "snapl-orchestrator"


# ---------------------------------------------------------------------------
# build_drift_schedule (pure)
# ---------------------------------------------------------------------------


def test_interval_defaults_and_minimum() -> None:
    assert timedelta(minutes=5) == DEFAULT_SCAN_INTERVAL
    assert timedelta(minutes=1) == MIN_SCAN_INTERVAL
    assert schedule_id_for("dcfabric") == "drift-response-dcfabric"


def test_build_drift_schedule_runs_drift_response_on_the_interval() -> None:
    schedule = build_drift_schedule(use_case_id="dcfabric", interval=timedelta(minutes=5), task_queue=_QUEUE)

    assert [spec.every for spec in schedule.spec.intervals] == [timedelta(minutes=5)]
    action = schedule.action
    assert isinstance(action, ScheduleActionStartWorkflow)
    assert action.workflow == "DriftResponse"
    assert action.id == "drift-response-dcfabric"
    assert action.task_queue == _QUEUE
    assert action.args == [DriftResponseRequest(use_case_id="dcfabric", schedule_id="drift-response-dcfabric")]
    assert action.static_summary == "drift response: report"


def test_build_drift_schedule_skips_overlaps_and_never_replays_old_firings() -> None:
    schedule = build_drift_schedule(use_case_id="dcfabric", interval=timedelta(minutes=2), task_queue=_QUEUE)

    assert schedule.policy.overlap is ScheduleOverlapPolicy.SKIP
    # The SDK default is 365 days, which would replay missed repairs (R4).
    assert schedule.policy.catchup_window == timedelta(minutes=2)
    assert schedule.policy.pause_on_failure is False
    assert schedule.state.paused is False


def test_build_drift_schedule_carries_the_mode_it_is_given() -> None:
    schedule = build_drift_schedule(
        use_case_id="dcfabric",
        interval=DEFAULT_SCAN_INTERVAL,
        task_queue=_QUEUE,
        mode=DriftResponseMode.REMEDIATE,
    )
    assert schedule.action.args[0].mode is DriftResponseMode.REMEDIATE
    assert schedule.action.static_summary == "drift response: remediate"


def test_build_drift_schedule_refuses_an_interval_below_the_minimum() -> None:
    with pytest.raises(ValueError, match="1m"):
        build_drift_schedule(use_case_id="dcfabric", interval=timedelta(seconds=59), task_queue=_QUEUE)


# ---------------------------------------------------------------------------
# Fake Temporal client
#
# Mirrors the parts of temporalio.client the module uses. describe() returns
# the action's arguments as raw Payloads, as the real server does, so the
# decode path is exercised.
# ---------------------------------------------------------------------------


class FakeScheduleHandle:
    def __init__(self, client: FakeClient, schedule_id: str) -> None:
        self._client = client
        self.id = schedule_id
        self.calls: list[tuple] = []

    def _entry(self) -> dict:
        if self.id not in self._client.schedules:
            raise RPCError("schedule not found", RPCStatusCode.NOT_FOUND, b"")
        return self._client.schedules[self.id]

    async def describe(self):
        entry = self._entry()
        schedule = entry["schedule"]
        args = list(schedule.action.args)
        # An update hands back the described (raw) action unchanged; only
        # freshly built actions hold models that need encoding.
        raw_args = args if args and isinstance(args[0], Payload) else await pydantic_data_converter.encode(args)
        described = dataclasses.replace(schedule, action=dataclasses.replace(schedule.action, args=raw_args))
        return SimpleNamespace(id=self.id, schedule=described, info=entry["info"])

    async def update(self, updater) -> None:
        if self._client.fail_next:
            raise self._client.fail_next
        description = await self.describe()
        update = updater(SimpleNamespace(description=description))
        if update is None:
            return
        self.calls.append(("update",))
        self._entry()["schedule"] = update.schedule

    async def pause(self, note: str | None = None) -> None:
        if self._client.fail_next:
            raise self._client.fail_next
        entry = self._entry()
        entry["schedule"] = dataclasses.replace(
            entry["schedule"], state=dataclasses.replace(entry["schedule"].state, paused=True, note=note)
        )
        self.calls.append(("pause", note))

    async def unpause(self, note: str | None = None) -> None:
        entry = self._entry()
        entry["schedule"] = dataclasses.replace(
            entry["schedule"], state=dataclasses.replace(entry["schedule"].state, paused=False, note=note)
        )
        self.calls.append(("unpause", note))

    async def trigger(self, overlap=None) -> None:
        self._entry()
        self.calls.append(("trigger", overlap))

    async def delete(self) -> None:
        self._entry()
        del self._client.schedules[self.id]
        self.calls.append(("delete",))


class FakeWorkflowHandle:
    def __init__(self, status: WorkflowExecutionStatus | None, result) -> None:
        self._status = status
        self._result = result

    async def describe(self):
        if isinstance(self._status, Exception):
            raise self._status
        return SimpleNamespace(status=self._status)

    async def result(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeClient:
    def __init__(self) -> None:
        self.schedules: dict[str, dict] = {}
        self.runs: dict[str, FakeWorkflowHandle] = {}
        self.handles: dict[str, FakeScheduleHandle] = {}
        self.data_converter = pydantic_data_converter
        self.fail_next: Exception | None = None
        self.created: list[str] = []

    async def create_schedule(self, schedule_id: str, schedule, **_kwargs):
        if self.fail_next:
            raise self.fail_next
        if schedule_id in self.schedules:
            raise ScheduleAlreadyRunningError
        self.schedules[schedule_id] = {"schedule": schedule, "info": _info()}
        self.created.append(schedule_id)
        return self.get_schedule_handle(schedule_id)

    def get_schedule_handle(self, schedule_id: str) -> FakeScheduleHandle:
        return self.handles.setdefault(schedule_id, FakeScheduleHandle(self, schedule_id))

    def get_workflow_handle(self, workflow_id: str, **_kwargs) -> FakeWorkflowHandle:
        return self.runs[workflow_id]


_T0 = datetime(2026, 10, 28, 3, 0, tzinfo=UTC)


def _info(*, recent=(), running=(), next_times=(), skipped: int = 0):
    return SimpleNamespace(
        recent_actions=list(recent),
        running_actions=[SimpleNamespace(workflow_id=w, first_execution_run_id="r") for w in running],
        next_action_times=list(next_times),
        num_actions_skipped_overlap=skipped,
    )


def _action_result(workflow_id: str, at: datetime):
    return SimpleNamespace(
        scheduled_at=at,
        started_at=at,
        action=SimpleNamespace(workflow_id=workflow_id, first_execution_run_id="run-1"),
    )


def _scan_result(*, clean: int, drifted: int) -> DriftScanResult:
    reports = {}
    for i in range(clean + drifted):
        device_id = uuid4()
        status = DriftStatus.DRIFTED if i < drifted else DriftStatus.CLEAN
        items = [DriftItem(path="/x", desired=1, actual=2, entity_kind="T")] if status is DriftStatus.DRIFTED else []
        reports[device_id] = DriftReport(
            device_id=device_id, device_name=f"d{i}", status=status, items=items, timestamp=_T0
        )
    return DriftScanResult(
        workflow_id="scan",
        use_case_id="dcfabric",
        reports=reports,
        total=clean + drifted,
        clean=clean,
        drifted=drifted,
        errored=0,
        started_at=_T0,
        ended_at=_T0,
    )


def _response_result(workflow_id: str, *, clean: int, drifted: int) -> DriftResponseResult:
    return DriftResponseResult(
        workflow_id=workflow_id,
        schedule_id="drift-response-dcfabric",
        use_case_id="dcfabric",
        mode=DriftResponseMode.REPORT,
        scan=_scan_result(clean=clean, drifted=drifted),
        started_at=_T0,
        ended_at=_T0,
    )


async def _changes(audit: InMemoryAuditLog) -> list[AuditEvent]:
    return await audit.query_by_workflow("drift-response-dcfabric")


async def _ensure(client: FakeClient, audit: InMemoryAuditLog, **kwargs):
    kwargs.setdefault("interval", DEFAULT_SCAN_INTERVAL)
    return await ensure_drift_schedule(
        client, use_case_id="dcfabric", task_queue=_QUEUE, audit_log=audit, actor="anton", **kwargs
    )


# ---------------------------------------------------------------------------
# ensure_drift_schedule (US1)
# ---------------------------------------------------------------------------


async def test_ensure_creates_a_report_schedule_and_records_it() -> None:
    client, audit = FakeClient(), InMemoryAuditLog()

    view = await _ensure(client, audit)

    assert client.created == ["drift-response-dcfabric"]
    assert view.mode is DriftResponseMode.REPORT
    assert view.interval == DEFAULT_SCAN_INTERVAL
    assert view.paused is False
    [event] = await _changes(audit)
    assert event.event_type is AuditEventType.SCHEDULE_CHANGED
    assert event.workflow_type == "DriftSchedule"
    assert event.target_id == "dcfabric"
    assert event.actor == "anton"
    assert event.payload == {"change": "created", "interval_seconds": 300, "mode": "report"}


async def test_ensure_on_an_existing_schedule_changes_only_the_interval() -> None:
    client, audit = FakeClient(), InMemoryAuditLog()
    client.schedules["drift-response-dcfabric"] = {
        "schedule": dataclasses.replace(
            build_drift_schedule(
                use_case_id="dcfabric",
                interval=DEFAULT_SCAN_INTERVAL,
                task_queue=_QUEUE,
                mode=DriftResponseMode.REMEDIATE,
            ),
            state=ScheduleState(paused=True, note="maintenance: spine swap"),
        ),
        "info": _info(),
    }

    view = await _ensure(client, audit, interval=timedelta(minutes=2))

    assert client.created == []
    stored = client.schedules["drift-response-dcfabric"]["schedule"]
    assert [s.every for s in stored.spec.intervals] == [timedelta(minutes=2)]
    assert stored.policy.catchup_window == timedelta(minutes=2)
    # Operator state survives a re-run of setup (FR-004).
    assert view.mode is DriftResponseMode.REMEDIATE
    assert (view.paused, view.note) == (True, "maintenance: spine swap")
    [event] = await _changes(audit)
    assert event.payload == {"change": "interval_changed", "from_seconds": 300, "to_seconds": 120}


async def test_ensure_with_an_unchanged_interval_records_nothing() -> None:
    client, audit = FakeClient(), InMemoryAuditLog()
    await _ensure(client, audit)

    await _ensure(client, audit)

    assert len(await _changes(audit)) == 1
    assert client.get_schedule_handle("drift-response-dcfabric").calls == []


async def test_ensure_records_nothing_when_temporal_refuses() -> None:
    client, audit = FakeClient(), InMemoryAuditLog()
    client.fail_next = RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")

    with pytest.raises(RPCError):
        await _ensure(client, audit)

    assert await _changes(audit) == []


# ---------------------------------------------------------------------------
# describe_drift_schedule / find_drift_schedule (US1)
# ---------------------------------------------------------------------------


async def test_describe_projects_the_schedule_and_its_recent_runs() -> None:
    client, audit = FakeClient(), InMemoryAuditLog()
    await _ensure(client, audit)
    runs = [f"drift-response-dcfabric-2026-10-28T03:{m:02d}:00Z" for m in range(0, 35, 5)]
    client.schedules["drift-response-dcfabric"]["info"] = _info(
        recent=[_action_result(w, _T0 + timedelta(minutes=5 * i)) for i, w in enumerate(runs)],
        running=[runs[-1]],
        next_times=[_T0 + timedelta(minutes=m) for m in (40, 45, 50, 55)],
        skipped=2,
    )
    for w in runs[:-2]:
        client.runs[w] = FakeWorkflowHandle(WorkflowExecutionStatus.COMPLETED, _response_result(w, clean=5, drifted=1))
    client.runs[runs[-2]] = FakeWorkflowHandle(WorkflowExecutionStatus.FAILED, None)
    client.runs[runs[-1]] = FakeWorkflowHandle(WorkflowExecutionStatus.RUNNING, None)

    view = await describe_drift_schedule(client, use_case_id="dcfabric")

    assert view.schedule_id == "drift-response-dcfabric"
    assert view.use_case_id == "dcfabric"
    assert view.next_runs == [_T0 + timedelta(minutes=m) for m in (40, 45, 50)]
    assert view.running == [runs[-1]]
    assert view.skipped_overlap == 2
    assert [r.workflow_id for r in view.recent_runs] == runs[-5:]
    assert [r.status for r in view.recent_runs] == ["completed", "completed", "completed", "failed", "running"]
    completed = view.recent_runs[0].summary
    assert (completed.total, completed.clean, completed.drifted, completed.wrote) == (6, 5, 1, False)
    assert view.recent_runs[-2].summary is None
    assert view.recent_runs[-1].summary is None


async def test_describe_keeps_a_run_whose_result_cannot_be_fetched() -> None:
    client, audit = FakeClient(), InMemoryAuditLog()
    await _ensure(client, audit)
    client.schedules["drift-response-dcfabric"]["info"] = _info(
        recent=[_action_result("w1", _T0), _action_result("w2", _T0)]
    )
    client.runs["w1"] = FakeWorkflowHandle(WorkflowExecutionStatus.COMPLETED, RuntimeError("history gone"))
    client.runs["w2"] = FakeWorkflowHandle(RuntimeError("describe failed"), None)

    view = await describe_drift_schedule(client, use_case_id="dcfabric")

    assert [(r.status, r.summary) for r in view.recent_runs] == [("completed", None), ("unknown", None)]


async def test_describe_a_missing_schedule_raises_not_found() -> None:
    with pytest.raises(ScheduleNotFoundError, match="dcfabric"):
        await describe_drift_schedule(FakeClient(), use_case_id="dcfabric")


async def test_find_returns_none_for_a_missing_schedule() -> None:
    assert await find_drift_schedule(FakeClient(), use_case_id="dcfabric") is None


async def test_find_returns_the_view_when_present() -> None:
    client = FakeClient()
    await _ensure(client, InMemoryAuditLog())
    view = await find_drift_schedule(client, use_case_id="dcfabric")
    assert view is not None
    assert view.mode is DriftResponseMode.REPORT


# ---------------------------------------------------------------------------
# pause / resume / trigger / delete (US2)
# ---------------------------------------------------------------------------


async def _created(audit: InMemoryAuditLog | None = None) -> tuple[FakeClient, InMemoryAuditLog]:
    client, audit = FakeClient(), audit or InMemoryAuditLog()
    await _ensure(client, audit)
    return client, audit


async def test_pause_requires_a_reason() -> None:
    client, audit = await _created()
    for reason in ("", "   "):
        with pytest.raises(ValueError, match="reason"):
            await pause_drift_schedule(client, use_case_id="dcfabric", reason=reason, audit_log=audit, actor="anton")
    assert client.get_schedule_handle("drift-response-dcfabric").calls == []


async def test_pause_suppresses_with_the_reason_as_the_note() -> None:
    client, audit = await _created()

    await pause_drift_schedule(
        client, use_case_id="dcfabric", reason="maintenance: spine swap", audit_log=audit, actor="anton"
    )

    assert client.get_schedule_handle("drift-response-dcfabric").calls == [("pause", "maintenance: spine swap")]
    event = (await _changes(audit))[-1]
    assert event.payload == {"change": "paused", "reason": "maintenance: spine swap"}
    assert event.actor == "anton"


async def test_resume_records_the_optional_note() -> None:
    client, audit = await _created()

    await resume_drift_schedule(client, use_case_id="dcfabric", audit_log=audit, actor="anton", note="swap done")

    assert client.get_schedule_handle("drift-response-dcfabric").calls == [("unpause", "swap done")]
    assert (await _changes(audit))[-1].payload == {"change": "resumed", "note": "swap done"}


async def test_trigger_returns_the_mode_it_will_run_with() -> None:
    client, audit = await _created()

    mode = await trigger_drift_schedule(client, use_case_id="dcfabric", audit_log=audit, actor="anton")

    assert mode is DriftResponseMode.REPORT
    assert client.get_schedule_handle("drift-response-dcfabric").calls == [("trigger", None)]
    assert (await _changes(audit))[-1].payload == {"change": "triggered", "mode": "report"}


async def test_delete_removes_the_schedule_and_records_it() -> None:
    client, audit = await _created()

    await delete_drift_schedule(client, use_case_id="dcfabric", audit_log=audit, actor="anton")

    assert client.schedules == {}
    assert (await _changes(audit))[-1].payload == {"change": "deleted"}


@pytest.mark.parametrize("operation", ["pause", "resume", "trigger", "delete"])
async def test_every_control_raises_not_found_for_a_missing_schedule(operation: str) -> None:
    client, audit = FakeClient(), InMemoryAuditLog()
    call = {
        "pause": lambda: pause_drift_schedule(client, use_case_id="dcfabric", reason="r", audit_log=audit, actor="a"),
        "resume": lambda: resume_drift_schedule(client, use_case_id="dcfabric", audit_log=audit, actor="a"),
        "trigger": lambda: trigger_drift_schedule(client, use_case_id="dcfabric", audit_log=audit, actor="a"),
        "delete": lambda: delete_drift_schedule(client, use_case_id="dcfabric", audit_log=audit, actor="a"),
    }[operation]

    with pytest.raises(ScheduleNotFoundError):
        await call()

    assert await _changes(audit) == []


async def test_no_record_is_written_when_temporal_refuses_a_pause() -> None:
    client, audit = await _created()
    client.fail_next = RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")

    with pytest.raises(RPCError):
        await pause_drift_schedule(client, use_case_id="dcfabric", reason="r", audit_log=audit, actor="anton")

    assert [e.payload["change"] for e in await _changes(audit)] == ["created"]
