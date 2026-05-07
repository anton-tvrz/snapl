"""T029 — Integration test for desired-state query against live Infrahub.

Validates that ``get_desired_state`` returns the expected devices when filtered
by id, role, and use_case, and that unmatched filters yield an empty list.

Devices come from the ``_query_devices_seeded`` fixture in
``conftest.py`` — see the note there about why the T029 fixture is inline
instead of reusing the ingester. Tests skip if Infrahub is unreachable.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from snapl_intent.models import DesiredState

pytestmark = [pytest.mark.integration, pytest.mark.live]


async def test_get_desired_state_by_name_returns_single_device(query_store) -> None:
    results = await query_store.get_desired_state(name="query-spine-01")

    assert len(results) == 1
    state = results[0]
    assert isinstance(state, DesiredState)
    assert state.device.name == "query-spine-01"
    assert state.device.role == "spine"
    assert state.device.use_case == "dcfabric"


async def test_get_desired_state_by_id_returns_single_device(query_store) -> None:
    by_name = await query_store.get_desired_state(name="query-leaf-01")
    assert by_name, "Expected query-leaf-01 to be seeded"
    target_id: UUID = by_name[0].device.id

    results = await query_store.get_desired_state(device_id=target_id)

    assert len(results) == 1
    assert results[0].device.id == target_id


async def test_get_desired_state_filters_by_role(query_store) -> None:
    results = await query_store.get_desired_state(role="spine")

    assert results, "Expected at least one spine device"
    assert all(state.device.role == "spine" for state in results)
    assert any(state.device.name == "query-spine-01" for state in results)


async def test_get_desired_state_filters_by_use_case(query_store) -> None:
    results = await query_store.get_desired_state(use_case="test_edge")

    assert results, "Expected at least one test_edge device"
    assert all(state.device.use_case == "test_edge" for state in results)
    assert any(state.device.name in {"edge-01", "edge-02"} for state in results)


async def test_get_desired_state_returns_empty_on_unmatched_filter(query_store) -> None:
    results = await query_store.get_desired_state(use_case="nonexistent-use-case")

    assert results == []


# ---------------------------------------------------------------------------
# US4 — use-case isolation integration tests
# ---------------------------------------------------------------------------


class TestUseCaseIsolation:
    """Verify that use_case filtering provides absolute isolation at the DB level.

    Requires both dcfabric and test_edge seeds to be present so we can confirm
    that each use case's query never returns the other's devices.
    """

    async def test_dcfabric_query_excludes_test_edge_devices(self, query_store) -> None:
        results = await query_store.get_desired_state(use_case="dcfabric")

        names = {state.device.name for state in results}
        assert names, "Expected dcfabric devices to be present"
        assert "edge-01" not in names
        assert "edge-02" not in names

    async def test_test_edge_query_excludes_dcfabric_devices(self, query_store) -> None:
        results = await query_store.get_desired_state(use_case="test_edge")

        names = {state.device.name for state in results}
        assert names, "Expected test_edge devices to be present"
        dcfabric_names = {"spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04"}
        assert not names.intersection(dcfabric_names), (
            f"dcfabric devices leaked into test_edge query: {names.intersection(dcfabric_names)}"
        )

    async def test_all_test_edge_devices_have_correct_use_case(self, query_store) -> None:
        results = await query_store.get_desired_state(use_case="test_edge")

        assert results, "Expected test_edge devices to be present"
        for state in results:
            assert state.device.use_case == "test_edge", (
                f"Device {state.device.name!r} has use_case={state.device.use_case!r}"
            )

    async def test_no_use_case_filter_returns_devices_from_all_use_cases(self, query_store) -> None:
        all_results = await query_store.get_desired_state()
        dcfabric_results = await query_store.get_desired_state(use_case="dcfabric")
        test_edge_results = await query_store.get_desired_state(use_case="test_edge")

        all_names = {state.device.name for state in all_results}
        assert all_names.issuperset({state.device.name for state in dcfabric_results})
        assert all_names.issuperset({state.device.name for state in test_edge_results})
