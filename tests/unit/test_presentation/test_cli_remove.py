"""`snapl remove`, the preview (spec 013 T018, contracts/cli-remove.md).

The Temporal client is mocked, as in the other CLI tests: what is asserted is
which workflow starts with which id, what is printed and the exit code.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from typer.testing import CliRunner

from snapl_observability.models import PlannedDelete, RefusedEntity, RemovalPlan
from snapl_presentation import cli
from snapl_presentation.exit_codes import ExitCode

pytestmark = pytest.mark.unit

runner = CliRunner()

PORT = "/interface[name=ethernet-1/7]"
NEIGHBOR = "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=192.0.2.0]"
POLICY = "/routing-policy/policy[name=c-probe]"


@pytest.fixture(autouse=True)
def _worker_present():
    with patch.object(cli, "count_pollers", new=AsyncMock(return_value=1)):
        yield


@pytest.fixture
def sot(states):
    with (
        patch.object(cli, "build_store", return_value=MagicMock()),
        patch.object(cli, "load_states", new=AsyncMock(return_value=states)),
    ):
        yield states


def _plan(device_ids, *, deletes=(), refused=(), forward=()) -> RemovalPlan:
    return RemovalPlan(
        device_id=device_ids["leaf-01"],
        device_name="leaf-01",
        scanned_at=datetime(2026, 11, 24, 14, 5, 12, tzinfo=UTC),
        deletes=[
            PlannedDelete(path=path, entity_kind=kind, answers=answers) for path, kind, answers in sorted(deletes)
        ],
        refused=[
            RefusedEntity(entity_path=path, entity_kind="routing_policy", paths=[f"{path}/default_action"], reason=r)
            for path, r in refused
        ],
        forward_drift=list(forward),
    )


def _some_deletes():
    return [
        (PORT, "interface", [f"{PORT}/description", f"{PORT}/enabled"]),
        (NEIGHBOR, "bgp_session", [f"{NEIGHBOR}/peer_asn", f"{NEIGHBOR}/peer_group"]),
    ]


def _client(*results):
    client = MagicMock()
    client.execute_workflow = AsyncMock(side_effect=list(results))
    return client


def _remove(client, *args: str):
    with patch.object(cli, "_connect", new=AsyncMock(return_value=client)):
        return runner.invoke(cli.app, ["remove", "leaf-01", *args])


class TestPreview:
    def test_a_plan_with_deletes_exits_two(self, sot, device_ids) -> None:
        client = _client(_plan(device_ids, deletes=_some_deletes()))

        result = _remove(client)

        assert result.exit_code == ExitCode.DRIFT, result.output
        assert f"delete {PORT}" in result.output
        assert f"delete {NEIGHBOR}" in result.output
        assert "Nothing was written" in result.output
        assert "snapl remove leaf-01 --apply" in result.output

    def test_starts_plan_removal_for_the_device(self, sot, device_ids) -> None:
        from snapl_orchestrator.workflows.removal import PlanRemovalWorkflow

        client = _client(_plan(device_ids))

        _remove(client)

        call = client.execute_workflow.call_args
        assert call.args == (PlanRemovalWorkflow.run, device_ids["leaf-01"])
        assert call.kwargs["id"].startswith(f"plan-removal-{device_ids['leaf-01']}-")
        assert call.kwargs["id"] != f"plan-removal-{device_ids['leaf-01']}-"

    def test_two_previews_never_collide(self, sot, device_ids) -> None:
        client = _client(_plan(device_ids), _plan(device_ids))

        _remove(client)
        _remove(client)

        first, second = (call.kwargs["id"] for call in client.execute_workflow.call_args_list)
        assert first != second

    def test_nothing_to_remove_exits_zero(self, sot, device_ids) -> None:
        result = _remove(_client(_plan(device_ids)))

        assert result.exit_code == ExitCode.OK, result.output
        assert "leaf-01: nothing to remove" in result.output

    def test_refusals_alone_exit_zero_and_are_listed(self, sot, device_ids) -> None:
        result = _remove(_client(_plan(device_ids, refused=[(POLICY, "not_removable")])))

        assert result.exit_code == ExitCode.OK, result.output
        assert "nothing to remove" in result.output
        assert f"refused {POLICY}" in result.output

    def test_json_is_the_plan(self, sot, device_ids) -> None:
        plan = _plan(device_ids, deletes=_some_deletes())

        result = _remove(_client(plan), "--json")

        assert result.exit_code == ExitCode.DRIFT
        assert json.loads(result.stdout) == {"plan": json.loads(plan.model_dump_json())}

    def test_a_device_not_in_intent_starts_nothing(self, sot) -> None:
        client = _client()
        with patch.object(cli, "_connect", new=AsyncMock(return_value=client)):
            result = runner.invoke(cli.app, ["remove", "ghost"])

        assert result.exit_code == ExitCode.ERROR
        assert "ghost" in result.output
        client.execute_workflow.assert_not_called()

    def test_no_worker_refuses_with_the_hint(self, sot, device_ids) -> None:
        client = _client(_plan(device_ids))
        with patch.object(cli, "count_pollers", new=AsyncMock(return_value=0)):
            result = _remove(client)

        assert result.exit_code == ExitCode.ERROR
        assert "no worker" in result.output
        assert "orchestrator.start" in result.output
        client.execute_workflow.assert_not_called()

    @pytest.mark.parametrize(
        ("reason", "detail"),
        [
            ("collect_failed", "gNMI dial failed: connection refused"),
            ("intent_unavailable", "infrahub unreachable"),
            ("device_not_found", "No desired state found"),
            ("remove_failed", "refused 1 path: /interface[name=mgmt0] (protected)"),
        ],
    )
    def test_a_failed_preview_is_one_line_and_exit_one(self, sot, reason, detail) -> None:
        failure = WorkflowFailureError(cause=ApplicationError(detail, type=reason, non_retryable=True))
        client = MagicMock()
        client.execute_workflow = AsyncMock(side_effect=failure)

        result = _remove(client)

        assert result.exit_code == ExitCode.ERROR
        assert detail in result.output
        assert "Traceback" not in result.output
        assert "nothing was written" in result.output.lower()
