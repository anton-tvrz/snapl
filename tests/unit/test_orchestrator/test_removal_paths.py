"""The planner and the executor's guard agree on every path (spec 013 SC-003, FR-004; T006).

Detection, planning and execution share one protected list, the drift field
map's. This is the one test that holds all three to it: whatever the planner
plans, a guard built the way the worker builds it accepts, and whatever the
planner refuses, that guard refuses too. It lives with the orchestrator because
the worker bootstrap is the one place both blocks meet.
"""

from __future__ import annotations

import pytest

from snapl_executor.gnmi.executor import check_delete_paths
from snapl_observability.structural.diff import ENTITY_FIELD_MAP
from snapl_observability.structural.removal import plan_removal
from tests.unit.test_observability.removal_reports import reports

pytestmark = pytest.mark.unit

_PROTECTED = ENTITY_FIELD_MAP["interface"]["protected"]
_REPORTS = reports()


@pytest.mark.parametrize("name", sorted(_REPORTS))
def test_every_planned_delete_passes_the_guard(name):
    plan = plan_removal(_REPORTS[name])
    assert check_delete_paths(plan.delete_paths, _PROTECTED) == {}


@pytest.mark.parametrize("name", sorted(_REPORTS))
def test_every_refused_entity_is_refused_by_the_guard_too(name):
    plan = plan_removal(_REPORTS[name])
    entity_paths = [refusal.entity_path for refusal in plan.refused]
    assert set(check_delete_paths(entity_paths, _PROTECTED)) == set(entity_paths)


@pytest.mark.parametrize("name", sorted(_REPORTS))
def test_protected_refusals_are_protected_to_the_guard(name):
    plan = plan_removal(_REPORTS[name])
    protected = [r.entity_path for r in plan.refused if r.reason == "protected"]
    assert set(check_delete_paths(protected, _PROTECTED).values()) <= {"protected"}


def test_the_adversarial_reports_reach_every_protected_name():
    plan = plan_removal(_REPORTS["protected_interfaces"])
    assert plan.is_empty
    named = " ".join(r.entity_path for r in plan.refused)
    for name in _PROTECTED:
        assert f"name={name}]" in named or f"name={name}." in named, name


@pytest.mark.parametrize("name", _PROTECTED)
def test_every_protected_name_is_refused_by_the_guard(name):
    paths = [
        f"/interface[name={name}]",
        f"/interface[name={name}]/subinterface[index=0]/ipv4/address[ip-prefix=192.0.2.1/31]",
        f"/network-instance[name=default]/interface[name={name}.0]",
    ]
    assert check_delete_paths(paths, _PROTECTED) == dict.fromkeys(paths, "protected")
