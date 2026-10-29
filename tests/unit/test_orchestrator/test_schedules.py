"""Unit tests for snapl_orchestrator.schedules.

Temporal's time-skipping test server does not implement Schedules (research
R11), so the builder is tested as a pure function and the control functions
against a fake client. One real round trip lives in the integration suite.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from temporalio.client import ScheduleActionStartWorkflow, ScheduleOverlapPolicy

from snapl_orchestrator.models import DriftResponseMode, DriftResponseRequest
from snapl_orchestrator.schedules import (
    DEFAULT_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    build_drift_schedule,
    schedule_id_for,
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
