"""The removal planner: undesired drift items to deletes (spec 013, T002).

One test per row of contracts/blocks.md's table, plus the plan-wide rules:
every undesired path lands in exactly one delete or refusal, forward drift is
never acted on, and the plan is deterministic.
"""

from __future__ import annotations

import pytest

from tests.unit.test_observability.removal_reports import (
    BINDING,
    EXTRA_ADDRESS,
    NEIGHBOR,
    POLICY,
    PORT,
    SCANNED_AT,
    reports,
)

pytestmark = pytest.mark.unit


def _plan(name: str):
    from snapl_observability.structural.removal import plan_removal

    return plan_removal(reports()[name])


def _refusals(plan) -> dict[str, str]:
    return {r.entity_path: r.reason.value for r in plan.refused}


class TestTheTable:
    def test_a_clean_report_gives_an_empty_plan(self):
        plan = _plan("clean")
        assert plan.is_empty
        assert plan.refused == []
        assert plan.forward_drift == []
        assert plan.device_name == "leaf-04"
        assert plan.scanned_at == SCANNED_AT

    def test_a_port_outside_intent_is_one_delete_of_its_entry(self):
        plan = _plan("port_fields")
        assert [(d.path, d.entity_kind, d.answers) for d in plan.deletes] == [
            (PORT, "interface", [f"{PORT}/description", f"{PORT}/enabled"]),
        ]

    def test_a_port_carrying_only_enabled_is_still_its_entry(self):
        """Deleting the leaf would leave the entry, which defaults to enabled (R2)."""
        assert _plan("port_enabled_only").delete_paths == [PORT]

    def test_an_extra_address_is_deleted_at_its_own_path(self):
        """C3: the '/' inside the prefix key needs no escaping, so the path is used verbatim."""
        plan = _plan("extra_address")
        assert [(d.path, d.answers) for d in plan.deletes] == [(EXTRA_ADDRESS, [EXTRA_ADDRESS])]

    def test_a_neighbor_outside_intent_is_one_delete_of_its_entry(self):
        plan = _plan("neighbor")
        assert [(d.path, d.entity_kind, d.answers) for d in plan.deletes] == [
            (NEIGHBOR, "bgp_session", [f"{NEIGHBOR}/peer_asn", f"{NEIGHBOR}/peer_group"]),
        ]

    def test_a_port_and_its_binding_are_two_deletes(self):
        """C1: the port cannot go without its binding, so both are planned (R3)."""
        plan = _plan("port_with_binding")
        assert plan.delete_paths == [PORT, BINDING]
        assert [d.entity_kind for d in plan.deletes] == ["interface", "routed_interface"]

    def test_a_system0_binding_is_refused_as_protected(self):
        plan = _plan("system0_binding")
        assert plan.is_empty
        assert _refusals(plan) == {"/network-instance[name=default]/interface[name=system0.0]": "protected"}

    def test_a_routing_policy_is_refused_as_not_removable(self):
        plan = _plan("routing_policy")
        assert plan.is_empty
        [refusal] = plan.refused
        assert refusal.entity_path == POLICY
        assert refusal.entity_kind == "routing_policy"
        assert refusal.reason == "not_removable"
        assert refusal.paths == [f"{POLICY}/accept_prefixes", f"{POLICY}/default_action"]

    def test_an_unknown_kind_is_refused_as_not_removable(self):
        plan = _plan("unknown_kind")
        assert plan.is_empty
        assert set(_refusals(plan).values()) == {"not_removable"}

    def test_unknown_shapes_of_known_kinds_are_refused_not_guessed_at(self):
        plan = _plan("unknown_shape")
        assert plan.is_empty
        assert len(plan.refused) == 4
        assert set(_refusals(plan).values()) == {"not_removable"}

    def test_forward_drift_is_listed_and_never_planned(self):
        source = reports()["forward_and_undesired"]
        plan = _plan("forward_and_undesired")
        assert plan.delete_paths == [PORT]
        assert plan.forward_drift == [item.path for item in source.items if not item.undesired]


class TestProtected:
    def test_the_protected_set_is_the_field_maps(self):
        from snapl_observability.structural.diff import ENTITY_FIELD_MAP

        plan = _plan("protected_interfaces")
        assert plan.is_empty
        assert set(_refusals(plan).values()) == {"protected"}
        refused_names = {path.split("name=")[-1].split("]")[0].split(".")[0] for path in _refusals(plan)}
        assert refused_names == set(ENTITY_FIELD_MAP["interface"]["protected"])

    def test_the_protected_set_is_read_from_the_field_map(self, monkeypatch):
        """Detection and removal use one list (FR-004), not a copy."""
        from snapl_observability.structural.diff import ENTITY_FIELD_MAP

        monkeypatch.setitem(ENTITY_FIELD_MAP["interface"], "protected", ("ethernet-1/7",))
        plan = _plan("port_with_binding")
        assert plan.is_empty
        assert _refusals(plan) == {PORT: "protected", BINDING: "protected"}


class TestPlanWideRules:
    @pytest.mark.parametrize("name", sorted(reports()))
    def test_every_undesired_path_lands_exactly_once(self, name):
        source = reports()[name]
        plan = _plan(name)
        placed = [path for d in plan.deletes for path in d.answers] + [path for r in plan.refused for path in r.paths]
        assert sorted(placed) == sorted(item.path for item in source.items if item.undesired)

    @pytest.mark.parametrize("name", sorted(reports()))
    def test_forward_paths_land_only_in_forward_drift(self, name):
        source = reports()[name]
        plan = _plan(name)
        assert plan.forward_drift == [item.path for item in source.items if not item.undesired]

    @pytest.mark.parametrize("name", sorted(reports()))
    def test_two_calls_give_equal_plans(self, name):
        assert _plan(name) == _plan(name)

    def test_an_error_report_raises(self):
        from snapl_observability.models import DriftReport, DriftStatus
        from snapl_observability.structural.removal import plan_removal

        errored = DriftReport(
            device_id=reports()["clean"].device_id,
            device_name="leaf-04",
            status=DriftStatus.ERROR,
            items=[],
            error="collect failed",
            timestamp=SCANNED_AT,
        )
        with pytest.raises(ValueError, match="error"):
            plan_removal(errored)

    def test_the_diffed_device_plans_every_removable_kind(self):
        """The real diff's output for leaf-04 after the captures' hand-added config."""
        plan = _plan("diffed")
        assert plan.delete_paths == [EXTRA_ADDRESS, PORT, BINDING, NEIGHBOR]
        assert _refusals(plan) == {POLICY: "not_removable"}

    def test_deletes_answer_in_report_order(self):
        plan = _plan("diffed")
        port = next(d for d in plan.deletes if d.path == PORT)
        assert port.answers == [
            f"{PORT}/description",
            f"{PORT}/ip_address",
            f"{PORT}/prefix_length",
            f"{PORT}/enabled",
        ]
