"""The stand-down activity (#136, spec 009 FR-018, research R8).

It is the one place a run changes its own schedule. The activity holds no
client of its own: it uses the worker's, through ``activity.client()``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from temporalio.testing import ActivityEnvironment

from snapl_orchestrator.activities import Activities, set_activities
from snapl_orchestrator.activities.schedule import stand_down_schedule
from snapl_orchestrator.audit.memory import InMemoryAuditLog
from snapl_orchestrator.models import DriftResponseMode, StandDownRequest
from snapl_orchestrator.schedules import describe_drift_schedule, ensure_drift_schedule, set_drift_response
from tests.unit.test_orchestrator.test_schedules import FakeClient

pytestmark = pytest.mark.unit

_REQUEST = StandDownRequest(use_case_id="dcfabric", reason="run-1: the repair failed on leaf-01")


def teardown_function() -> None:
    import snapl_orchestrator.activities as a

    a._activities = None


def _install(audit: InMemoryAuditLog) -> None:
    set_activities(
        Activities(
            intent_store=MagicMock(), executor=MagicMock(), collector=MagicMock(), observer=MagicMock(), audit_log=audit
        )
    )


async def _remediating(audit: InMemoryAuditLog) -> FakeClient:
    client = FakeClient()
    common = {"use_case_id": "dcfabric", "audit_log": audit, "actor": "anton"}
    await ensure_drift_schedule(client, task_queue="snapl-orchestrator", **common)
    await set_drift_response(client, mode=DriftResponseMode.REMEDIATE, **common)
    return client


async def test_it_stands_the_schedule_down_with_the_workers_client_and_audit_log() -> None:
    audit = InMemoryAuditLog()
    client = await _remediating(audit)
    _install(audit)

    changed = await ActivityEnvironment(client=client).run(stand_down_schedule, _REQUEST)

    assert changed is True
    view = await describe_drift_schedule(client, use_case_id="dcfabric")
    assert (view.mode, view.note) == (DriftResponseMode.REPORT, f"stood down: {_REQUEST.reason}")
    last = (await audit.query_by_workflow("drift-response-dcfabric"))[-1]
    assert last.payload["change"] == "stood_down"


async def test_a_schedule_deleted_during_the_run_is_not_an_error() -> None:
    """There is nothing left to stand down, and failing would only retry."""
    audit = InMemoryAuditLog()
    _install(audit)

    changed = await ActivityEnvironment(client=FakeClient()).run(stand_down_schedule, _REQUEST)

    assert changed is False
