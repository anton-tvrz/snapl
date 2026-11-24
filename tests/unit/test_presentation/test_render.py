"""Rendering contracts (spec 006 FR-010, SC-004).

The human rendering is checked for the facts an operator needs to see, not for
exact layout — asserting on box-drawing characters would make every cosmetic
change a test failure. The JSON rendering is checked strictly: it is an
interface, and FR-010 says stdout carries only valid JSON.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from rich.console import Console

from snapl_orchestrator.models import WorkflowReason
from snapl_presentation.exit_codes import ExitCode
from snapl_presentation.render import HumanRenderer, JsonRenderer, build_renderer

pytestmark = pytest.mark.unit


def _capture() -> tuple[Console, object]:
    console = Console(record=True, width=200, force_terminal=False, no_color=True)
    return console, console


def _text(console: Console) -> str:
    return console.export_text()


class TestHumanScan:
    def test_reports_the_per_status_counts(self, scan_result) -> None:
        console, _ = _capture()
        HumanRenderer(console).scan(scan_result(drifted=1, errored=1))
        text = _text(console)
        assert "3 devices" in text
        assert "1 clean" in text
        assert "1 drifted" in text
        assert "1 errored" in text

    def test_names_the_drifted_path_with_both_values(self, scan_result) -> None:
        """The platform's headline output — the exact path, desired and actual."""
        console, _ = _capture()
        HumanRenderer(console).scan(scan_result(drifted=1))
        text = _text(console)
        assert "/interface[name=ethernet-1/1]/admin-state" in text
        assert "enable" in text
        assert "disable" in text

    def test_undesired_config_reads_as_undesired_not_as_a_missing_value(self) -> None:
        """An operator must see which way the difference runs (#54, spec 007
        FR-007). Both directions render `desired` as empty otherwise: intent
        wanting nothing looks exactly like the device having nothing."""
        from snapl_observability.models import DriftItem, DriftReport, DriftStatus
        from snapl_orchestrator.models import DriftScanResult

        started = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
        device_id = uuid4()
        result = DriftScanResult(
            workflow_id="scan-drift-dcfabric",
            use_case_id="dcfabric",
            reports={
                device_id: DriftReport(
                    device_id=device_id,
                    device_name="spine-01",
                    status=DriftStatus.DRIFTED,
                    items=[
                        DriftItem(
                            path="/interface[name=ethernet-1/7]/ip_address",
                            desired=None,
                            actual="192.0.2.1",
                            entity_kind="interface",
                            undesired=True,
                        )
                    ],
                    timestamp=started,
                )
            },
            total=1,
            clean=0,
            drifted=1,
            errored=0,
            started_at=started,
            ended_at=started,
        )

        console, _ = _capture()
        HumanRenderer(console).scan(result)
        text = _text(console)

        assert "ethernet-1/7" in text
        assert "192.0.2.1" in text
        assert "not in intent" in text, "undesired config is not marked as such"

    def test_clean_devices_are_not_listed_individually(self, scan_result) -> None:
        """Six clean devices should not push the one drifted device off screen."""
        console, _ = _capture()
        HumanRenderer(console).scan(scan_result(drifted=1))
        text = _text(console)
        assert "spine-01" in text, "the drifted device is named"
        assert "leaf-01" not in text, "clean devices stay in the summary counts"

    def test_errored_device_shows_its_failure(self, scan_result) -> None:
        console, _ = _capture()
        HumanRenderer(console).scan(scan_result(errored=1))
        assert "connection refused" in _text(console)

    def test_bracketed_data_is_not_eaten_by_the_markup_parser(self, scan_result) -> None:
        """gNMI paths are full of square brackets, and Rich reads `[name=...]`
        as a style tag and silently deletes it — mangling the single most
        important line the CLI prints. Every interpolated value is escaped."""
        console, _ = _capture()
        HumanRenderer(console).scan(scan_result(drifted=1))
        assert "[name=ethernet-1/1]" in _text(console)


class TestHumanDeploy:
    def test_success_shows_verdict_and_duration(self, workflow_result) -> None:
        console, _ = _capture()
        HumanRenderer(console).deploy(workflow_result())
        text = _text(console)
        assert "succeeded" in text
        assert "4.0s" in text

    def test_failure_shows_reason_and_detail(self, workflow_result) -> None:
        """The operator must not have to read the worker log to learn why."""
        console, _ = _capture()
        HumanRenderer(console).deploy(workflow_result(success=False, reason=WorkflowReason.APPLY_FAILED))
        text = _text(console)
        assert "apply_failed" in text
        assert "apply rejected by device" in text


class TestHumanAudit:
    def test_empty_result_says_so(self) -> None:
        """An empty log is an answer, not an error (FR / US4 scenario 3)."""
        console, _ = _capture()
        HumanRenderer(console).audit([])
        assert "no audit events" in _text(console)

    def test_events_render_chronologically(self, audit_events) -> None:
        console, _ = _capture()
        HumanRenderer(console).audit(audit_events)
        text = _text(console)
        assert text.index("workflow_started") < text.index("activity_completed")
        assert "apply_config" in text


class TestJson:
    def test_scan_output_is_valid_json(self, scan_result) -> None:
        console, _ = _capture()
        JsonRenderer(console).scan(scan_result(drifted=1, errored=1))
        payload = json.loads(_text(console))
        assert payload["total"] == 3
        assert payload["drifted"] == 1

    def test_scan_json_carries_the_exit_code(self, scan_result) -> None:
        """So a consumer that already parses stdout need not also check $?."""
        console, _ = _capture()
        JsonRenderer(console).scan(scan_result(drifted=1))
        assert json.loads(_text(console))["exit_code"] == 2

    def test_scan_json_survives_errored_devices(self, scan_result) -> None:
        """SC-004: valid JSON in 100% of runs, including when devices error."""
        console, _ = _capture()
        JsonRenderer(console).scan(scan_result(errored=2))
        payload = json.loads(_text(console))
        errors = [r["error"] for r in payload["reports"].values() if r["error"]]
        assert len(errors) == 2

    def test_json_carries_every_field_the_human_view_shows(self, scan_result) -> None:
        console, _ = _capture()
        JsonRenderer(console).scan(scan_result(drifted=1))
        payload = json.loads(_text(console))
        drifted = [r for r in payload["reports"].values() if r["status"] == "drifted"]
        item = drifted[0]["items"][0]
        assert item["path"]
        assert item["desired"] == "enable"
        assert item["actual"] == "disable"

    def test_audit_json_is_a_list(self, audit_events) -> None:
        console, _ = _capture()
        JsonRenderer(console).audit(audit_events)
        payload = json.loads(_text(console))
        assert isinstance(payload, list)
        assert len(payload) == 2

    def test_deploy_json_is_valid(self, workflow_result) -> None:
        console, _ = _capture()
        JsonRenderer(console).deploy(workflow_result())
        assert json.loads(_text(console))["success"] is True


class TestBuildRenderer:
    def test_selects_by_flag(self) -> None:
        assert isinstance(build_renderer(as_json=True), JsonRenderer)
        assert isinstance(build_renderer(as_json=False), HumanRenderer)

    def test_both_renderers_share_the_exit_mapping(self, scan_result) -> None:
        """Exit codes are a property of the result, not of the format."""
        result = scan_result(drifted=1)
        assert build_renderer(as_json=True).scan_code(result) == build_renderer(as_json=False).scan_code(result)


# ---------------------------------------------------------------------------
# Drift schedule view (spec 009)
# ---------------------------------------------------------------------------


def _schedule_view(*, mode: str = "report", paused: bool = False, note: str | None = None, runs=None):
    from datetime import timedelta

    from snapl_orchestrator.models import DriftResponseMode, DriftScheduleView

    t0 = datetime(2026, 10, 28, 3, 0, tzinfo=UTC)
    return DriftScheduleView(
        schedule_id="drift-response-dcfabric",
        use_case_id="dcfabric",
        interval=timedelta(minutes=5),
        mode=DriftResponseMode(mode),
        paused=paused,
        note=note,
        next_runs=[t0 + timedelta(minutes=m) for m in (10, 15, 20)],
        recent_runs=runs or [],
        running=[],
        skipped_overlap=0,
    )


def _run(workflow_id: str, status: str, summary=None):
    from snapl_orchestrator.models import ScheduledRunInfo

    at = datetime(2026, 10, 28, 3, 0, tzinfo=UTC)
    return ScheduledRunInfo(workflow_id=workflow_id, scheduled_at=at, started_at=at, status=status, summary=summary)


def _summary(*, clean=5, drifted=1, errored=0, succeeded=0, failed=0):
    from snapl_orchestrator.models import RunSummary

    return RunSummary(
        total=clean + drifted + errored,
        clean=clean,
        drifted=drifted,
        errored=errored,
        remediated=succeeded + failed,
        succeeded=succeeded,
        failed=failed,
        wrote=succeeded + failed > 0,
    )


class TestHumanSchedule:
    def test_shows_the_schedule_settings(self) -> None:
        console, _ = _capture()
        HumanRenderer(console).schedule(_schedule_view())
        text = _text(console)
        assert "drift-response-dcfabric" in text
        assert "every" in text
        assert "5m" in text
        assert "report" in text
        assert "active" in text
        assert "03:10:00Z" in text

    def test_a_paused_schedule_shows_its_reason(self) -> None:
        console, _ = _capture()
        HumanRenderer(console).schedule(_schedule_view(paused=True, note="maintenance: spine swap"))
        assert 'paused — "maintenance: spine swap"' in _text(console)

    def test_an_active_schedule_shows_its_note(self) -> None:
        # A stand-down leaves the schedule active, with why on its note (#389).
        note = "stood down: drift-response-dcfabric-1: 4 of 6 devices drifted, more than half; repaired nothing"
        console, _ = _capture()
        HumanRenderer(console).schedule(_schedule_view(note=note))
        assert f'active — "{note}"' in _text(console)

    def test_a_completed_report_run_shows_its_summary(self) -> None:
        console, _ = _capture()
        view = _schedule_view(runs=[_run("drift-response-dcfabric-1", "completed", _summary())])
        HumanRenderer(console).schedule(view)
        text = _text(console)
        assert "completed" in text
        assert "6 devices: 5 clean, 1 drifted, 0 errored" in text
        assert "wrote: no" in text

    def test_a_run_that_wrote_says_how_many_devices(self) -> None:
        console, _ = _capture()
        view = _schedule_view(
            mode="remediate", runs=[_run("drift-response-dcfabric-1", "completed", _summary(succeeded=1))]
        )
        HumanRenderer(console).schedule(view)
        assert "wrote: 1 device (1 ok, 0 failed)" in _text(console)

    def test_a_run_without_a_summary_shows_its_workflow_id(self) -> None:
        console, _ = _capture()
        view = _schedule_view(runs=[_run("drift-response-dcfabric-2026-10-28T03:05:00Z", "running")])
        HumanRenderer(console).schedule(view)
        text = _text(console)
        assert "running" in text
        assert "drift-response-dcfabric-2026-10-28T03:05:00Z" in text

    def test_remediate_renders_in_the_warning_colour_on_a_tty(self) -> None:
        console = Console(record=True, width=200, force_terminal=True, color_system="standard")
        HumanRenderer(console).schedule(_schedule_view(mode="remediate"))
        styled = console.export_text(styles=True)
        assert "\x1b[33mremediate" in styled or "\x1b[1;33mremediate" in styled


class TestJsonSchedule:
    def test_emits_the_view_as_json(self) -> None:
        console = Console(record=True, width=200, force_terminal=False)
        view = _schedule_view(runs=[_run("drift-response-dcfabric-1", "completed", _summary())])
        JsonRenderer(console).schedule(view)
        payload = json.loads(console.export_text())
        assert payload["schedule_id"] == "drift-response-dcfabric"
        assert payload["interval"] == 300
        assert payload["mode"] == "report"
        assert payload["next_runs"][0] == "2026-10-28T03:10:00Z"
        assert payload["recent_runs"][0]["summary"]["wrote"] is False


class TestHumanAuditScheduleEvents:
    def _event(self, event_type: str, payload: dict, actor: str | None):
        from snapl_orchestrator.models import AuditEvent, AuditEventType

        return AuditEvent(
            event_id=uuid4(),
            workflow_id="drift-response-dcfabric",
            workflow_type="DriftSchedule",
            target_id="dcfabric",
            event_type=AuditEventType(event_type),
            payload=payload,
            timestamp=datetime(2026, 10, 28, 3, 0, tzinfo=UTC),
            actor=actor,
        )

    def test_a_schedule_change_shows_what_changed_and_who(self) -> None:
        console, _ = _capture()
        HumanRenderer(console).audit(
            [
                self._event(
                    "schedule_changed", {"change": "response_changed", "from": "report", "to": "remediate"}, "anton"
                )
            ]
        )
        text = _text(console)
        assert "response_changed report→remediate" in text
        assert "anton" in text

    def test_a_device_level_drift_response_shows_its_decision(self) -> None:
        console, _ = _capture()
        HumanRenderer(console).audit(
            [self._event("drift_response", {"decision": "undesired_only"}, "schedule:drift-response-dcfabric")]
        )
        text = _text(console)
        assert "undesired_only" in text
        assert "schedule:drift-response-dcfabric" in text

    def test_a_use_case_level_drift_response_shows_its_mode(self) -> None:
        console, _ = _capture()
        HumanRenderer(console).audit([self._event("drift_response", {"mode": "report"}, "schedule:x")])
        assert "report" in _text(console)


def test_a_run_line_is_never_hard_wrapped_on_a_narrow_console() -> None:
    """Found live: at the default 80 columns Rich broke `wrote: no` onto its own
    line, splitting one run's facts across two rows of the view."""
    console = Console(record=True, width=80, force_terminal=False, no_color=True)
    view = _schedule_view(runs=[_run("drift-response-dcfabric-1", "completed", _summary())])
    HumanRenderer(console).schedule(view)
    lines = console.export_text().splitlines()
    assert any("6 devices: 5 clean, 1 drifted, 0 errored" in line and "wrote: no" in line for line in lines)


@pytest.mark.parametrize("as_json", [True, False])
def test_a_cancelled_scan_exits_as_an_error_even_when_what_it_finished_was_clean(scan_result, as_json) -> None:
    """The scan did not answer for the whole fabric, so it is not a clean scan (#36)."""
    cancelled = scan_result().model_copy(update={"cancelled": True})

    assert build_renderer(as_json=as_json).scan_code(cancelled) == ExitCode.ERROR


class TestHealthRendering:
    """Spec 016 (contracts/telemetry.md)."""

    def _event(self, reasons: list[dict], before: str = "healthy", after: str = "unhealthy"):
        from snapl_orchestrator.models import AuditEvent, AuditEventType

        return AuditEvent(
            event_id=uuid4(),
            workflow_id="telemetry:leaf-01",
            workflow_type="DeviceTelemetry",
            target_id=uuid4(),
            event_type=AuditEventType.HEALTH_CHANGED,
            payload={"before": before, "after": after, "reasons": reasons, "last_sample_at": None},
            timestamp=datetime(2026, 11, 19, 22, 0, tzinfo=UTC),
            actor="worker",
        )

    def test_a_health_change_shows_the_change_and_each_reason_on_its_own_line(self) -> None:
        console, _ = _capture()
        reasons = [{"kind": "interface", "text": "ethernet-1/49 down (expected up)"}, {"kind": "x", "text": "second"}]
        HumanRenderer(console).audit([self._event(reasons)])
        lines = _text(console).splitlines()
        assert any("healthy→unhealthy" in line and "worker" in line for line in lines)
        assert [line.strip() for line in lines if line.strip() in {"ethernet-1/49 down (expected up)", "second"}] == [
            "ethernet-1/49 down (expected up)",
            "second",
        ]

    def test_a_recovery_has_no_reason_lines(self) -> None:
        console, _ = _capture()
        HumanRenderer(console).audit([self._event([], before="unhealthy", after="healthy")])
        assert "unhealthy→healthy" in _text(console)

    def test_the_status_health_line_names_the_unhealthy(self) -> None:
        from snapl_presentation.render import FabricHealthSummary

        console, _ = _capture()
        summary = FabricHealthSummary(healthy=["a", "b"], unhealthy=["spine-01"], unknown=["c"])
        HumanRenderer(console).status([("temporal", True, "x")], [], health=summary)
        text = _text(console)
        assert "fabric health   2 healthy, 1 unhealthy (spine-01), 1 unknown" in text
        assert text.index("temporal") < text.index("fabric health")

    def test_json_status_health_is_null_when_nothing_was_recorded(self) -> None:
        from snapl_presentation.render import FabricHealthSummary

        console = Console(record=True, width=200, force_terminal=False, no_color=True)
        JsonRenderer(console).status(
            [], [], health=FabricHealthSummary(healthy=[], unhealthy=[], unknown=["a"], recorded=False)
        )
        assert json.loads(console.export_text())["health"] is None


# ---------------------------------------------------------------------------
# The removal plan (spec 013 T019)
# ---------------------------------------------------------------------------

_PORT = "/interface[name=ethernet-1/7]"
_NEIGHBOR = "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=192.0.2.0]"
_ADDRESS = "/interface[name=ethernet-1/49]/subinterface[index=0]/ipv4/address[ip-prefix=192.0.2.1/31]"
_POLICY = "/routing-policy/policy[name=c-probe]"


def _removal_plan(*, deletes=True, refused=True, forward=2):
    from snapl_observability.models import PlannedDelete, RefusedEntity, RemovalPlan

    return RemovalPlan(
        device_id=uuid4(),
        device_name="leaf-04",
        scanned_at=datetime(2026, 11, 24, 14, 5, 12, tzinfo=UTC),
        deletes=[
            PlannedDelete(path=_ADDRESS, entity_kind="interface", answers=[_ADDRESS]),
            PlannedDelete(path=_PORT, entity_kind="interface", answers=[f"{_PORT}/description", f"{_PORT}/enabled"]),
            PlannedDelete(
                path=_NEIGHBOR, entity_kind="bgp_session", answers=[f"{_NEIGHBOR}/peer_asn", f"{_NEIGHBOR}/peer_group"]
            ),
        ]
        if deletes
        else [],
        refused=[
            RefusedEntity(
                entity_path="/interface[name=mgmt0]",
                entity_kind="interface",
                paths=["/interface[name=mgmt0]/description"],
                reason="protected",
            ),
            RefusedEntity(
                entity_path=_POLICY,
                entity_kind="routing_policy",
                paths=[f"{_POLICY}/default_action"],
                reason="not_removable",
            ),
        ]
        if refused
        else [],
        forward_drift=[f"/interface[name=ethernet-1/{n}]/description" for n in range(1, forward + 1)],
    )


class TestHumanRemovalPlan:
    def _text(self, plan) -> str:
        console = Console(record=True, width=80, force_terminal=False, no_color=True)
        HumanRenderer(console).removal_plan(plan)
        return console.export_text()

    def test_heads_with_the_count_and_the_scan_time(self) -> None:
        assert "leaf-04: 3 deletes planned from a scan at 14:05:12Z" in self._text(_removal_plan())

    def test_lists_each_delete_whole_even_on_a_narrow_terminal(self) -> None:
        text = self._text(_removal_plan())
        for path in (_ADDRESS, _PORT, _NEIGHBOR):
            assert f"delete {path}" in text

    def test_names_what_each_delete_answers(self) -> None:
        text = self._text(_removal_plan())
        assert "answers …/description, …/enabled" in text
        assert "answers …/peer_asn, …/peer_group" in text

    def test_an_address_delete_answers_itself_without_saying_so(self) -> None:
        assert text_count(self._text(_removal_plan()), "answers") == 2

    def test_warns_that_a_neighbour_delete_tears_the_session_down(self) -> None:
        text = self._text(_removal_plan())
        assert "removing a neighbour tears its session down; its peer group stays configured" in text

    def test_lists_the_refusals_with_their_reasons(self) -> None:
        text = self._text(_removal_plan())
        assert "refused /interface[name=mgmt0] — protected" in text
        assert f"refused {_POLICY} — not removable in this version" in text

    def test_points_forward_drift_at_reconcile(self) -> None:
        text = self._text(_removal_plan())
        assert "2 paths are missing or differ from intent: snapl reconcile leaf-04 heals those, not this." in text

    def test_says_nothing_was_written_and_how_to_remove(self) -> None:
        text = self._text(_removal_plan())
        assert "Nothing was written. To remove: snapl remove leaf-04 --apply" in text

    def test_nothing_to_remove(self) -> None:
        text = self._text(_removal_plan(deletes=False, refused=False, forward=0))
        assert "leaf-04: nothing to remove" in text
        assert "--apply" not in text
        assert "reconcile" not in text

    def test_one_delete_is_singular(self) -> None:
        from snapl_observability.models import PlannedDelete

        plan = _removal_plan(refused=False, forward=1).model_copy(
            update={"deletes": [PlannedDelete(path=_PORT, entity_kind="interface", answers=[f"{_PORT}/enabled"])]}
        )
        text = self._text(plan)
        assert "1 delete planned" in text
        assert "1 path is missing or differs from intent" in text


def text_count(text: str, word: str) -> int:
    return sum(line.strip().startswith(word) for line in text.splitlines())


class TestJsonRemovalPlan:
    def test_is_the_plan_under_one_key(self) -> None:
        plan = _removal_plan()
        console = Console(record=True, width=200, force_terminal=False)
        JsonRenderer(console).removal_plan(plan)
        assert json.loads(console.export_text()) == {"plan": json.loads(plan.model_dump_json())}


class TestRemovalPlanCode:
    def test_something_to_remove_is_drift(self) -> None:
        assert HumanRenderer.removal_plan_code(_removal_plan()) == ExitCode.DRIFT

    def test_nothing_to_remove_is_ok(self) -> None:
        assert JsonRenderer.removal_plan_code(_removal_plan(deletes=False)) == ExitCode.OK


# ---------------------------------------------------------------------------
# The removal's result (spec 013 T033, contracts/cli-remove.md "Apply")
# ---------------------------------------------------------------------------

_C1_REJECTION = "[FailedPrecondition] subinterface ethernet-1/7.0 not found"


def _removal_result(reason, *, deleted=(), verification=None, detail=None, plan=None, device_name="leaf-04"):
    from snapl_orchestrator.models import RemovalResult, RemovalVerification

    started = datetime(2026, 11, 24, 14, 6, 0, tzinfo=UTC)
    return RemovalResult(
        workflow_id="remove-config-x",
        device_id=uuid4(),
        device_name=device_name,
        success=reason == WorkflowReason.SUCCEEDED,
        reason=reason,
        detail=detail,
        plan=plan if plan is not None else _removal_plan(),
        deleted=list(deleted),
        device_response="{}" if deleted else None,
        verification=RemovalVerification(**verification) if verification else None,
        started_at=started,
        ended_at=started,
    )


_ALL = sorted([_ADDRESS, _PORT, _NEIGHBOR])
_VERIFIED = {"gone": _ALL, "remaining": [], "new_forward": [], "still_refused": [f"{_POLICY}/default_action"]}


class TestHumanRemovalResult:
    def _text(self, result) -> str:
        console = Console(record=True, width=80, force_terminal=False, no_color=True)
        HumanRenderer(console).removal(result.plan, result)
        return console.export_text()

    def test_succeeded(self) -> None:
        text = self._text(_removal_result(WorkflowReason.SUCCEEDED, deleted=_ALL, verification=_VERIFIED))
        assert "removed 3 entries from leaf-04; the verifying scan finds none of them" in text
        for path in _ALL:
            assert f"deleted {path}" in text
        assert f"still there, refused: {_POLICY}/default_action" in text

    def test_plan_changed_shows_the_new_plan(self) -> None:
        text = self._text(_removal_result(WorkflowReason.PLAN_CHANGED))
        assert "leaf-04 changed since the preview; nothing was removed" in text
        assert "leaf-04: 3 deletes planned from a scan at 14:05:12Z" in text
        assert f"delete {_PORT}" in text
        assert "To remove: snapl remove leaf-04 --apply" in text

    def test_deploy_in_flight(self) -> None:
        text = self._text(_removal_result(WorkflowReason.DEPLOY_IN_FLIGHT))
        assert "a deploy to leaf-04 is running; nothing was removed. Run this again when it ends." in text

    def test_remove_failed_keeps_the_devices_reason(self) -> None:
        text = self._text(_removal_result(WorkflowReason.REMOVE_FAILED, detail=_C1_REJECTION))
        assert "leaf-04 rejected the removal; nothing was removed" in text
        assert _C1_REJECTION in text

    def test_verification_failed_lists_what_was_deleted_and_what_is_wrong(self) -> None:
        lost = "/interface[name=ethernet-1/2]/enabled"
        verification = {"gone": [_ADDRESS, _NEIGHBOR], "remaining": [_PORT], "new_forward": [lost], "still_refused": []}
        text = self._text(_removal_result(WorkflowReason.VERIFICATION_FAILED, deleted=_ALL, verification=verification))
        assert "deleted 3 entries from leaf-04, but the verifying scan disagrees" in text
        for path in _ALL:
            assert f"deleted {path}" in text
        assert f"still there: {_PORT}" in text
        assert f"newly missing or different from intent: {lost}" in text

    def test_collect_failed_after_the_write(self) -> None:
        result = _removal_result(WorkflowReason.COLLECT_FAILED, deleted=_ALL, detail="socket closed")
        text = self._text(result)
        assert "deleted 3 entries; the verifying scan could not read leaf-04" in text
        assert "socket closed" in text
        assert "snapl scan" in text

    def test_collect_failed_before_the_write(self) -> None:
        text = self._text(_removal_result(WorkflowReason.COLLECT_FAILED, detail="socket closed"))
        assert "could not read leaf-04; nothing was removed" in text
        assert "socket closed" in text

    @pytest.mark.parametrize("reason", [WorkflowReason.DEVICE_NOT_FOUND, WorkflowReason.INTENT_UNAVAILABLE])
    def test_intent_failures(self, reason) -> None:
        text = self._text(_removal_result(reason, detail="infrahub unreachable", device_name=None, plan=None))
        assert f"{reason.value.replace('_', ' ')}: nothing was removed" in text
        assert "infrahub unreachable" in text

    def test_cancelled_after_the_set_names_what_was_deleted(self) -> None:
        text = self._text(_removal_result(WorkflowReason.CANCELLED, deleted=[_PORT]))
        assert "the removal of leaf-04 was cancelled after deleting 1 entry" in text
        assert f"deleted {_PORT}" in text

    def test_cancelled_before_the_set(self) -> None:
        text = self._text(_removal_result(WorkflowReason.CANCELLED))
        assert "the removal of leaf-04 was cancelled; nothing was removed" in text

    def test_nothing_to_remove_prints_nothing_more(self) -> None:
        console = Console(record=True, width=80, force_terminal=False, no_color=True)
        HumanRenderer(console).removal(_removal_plan(deletes=False), None)
        assert console.export_text() == ""


class TestHumanRemovalPlanBeforeApply:
    def test_does_not_suggest_apply_while_applying(self) -> None:
        console = Console(record=True, width=80, force_terminal=False, no_color=True)
        HumanRenderer(console).removal_plan(_removal_plan(), applying=True)
        text = console.export_text()
        assert "Nothing was written." in text
        assert "--apply" not in text


class TestJsonRemovalResult:
    def test_is_the_plan_and_the_result(self) -> None:
        plan = _removal_plan()
        result = _removal_result(WorkflowReason.SUCCEEDED, deleted=_ALL, verification=_VERIFIED, plan=plan)
        console = Console(record=True, width=200, force_terminal=False)
        JsonRenderer(console).removal(plan, result)
        assert json.loads(console.export_text()) == {
            "plan": json.loads(plan.model_dump_json()),
            "result": json.loads(result.model_dump_json()),
        }

    def test_nothing_to_remove_has_no_result(self) -> None:
        plan = _removal_plan(deletes=False)
        console = Console(record=True, width=200, force_terminal=False)
        JsonRenderer(console).removal(plan, None)
        assert json.loads(console.export_text()) == {"plan": json.loads(plan.model_dump_json()), "result": None}

    def test_the_json_plan_view_is_unchanged_while_applying(self) -> None:
        """A JSON consumer gets one document: the apply prints plan and result together."""
        console = Console(record=True, width=200, force_terminal=False)
        JsonRenderer(console).removal_plan(_removal_plan(), applying=True)
        assert console.export_text() == ""


class TestRemovalResultCode:
    def test_succeeded_is_ok(self) -> None:
        result = _removal_result(WorkflowReason.SUCCEEDED, deleted=_ALL, verification=_VERIFIED)
        assert HumanRenderer.removal_code(result) == ExitCode.OK

    def test_nothing_to_remove_is_ok(self) -> None:
        assert JsonRenderer.removal_code(None) == ExitCode.OK

    @pytest.mark.parametrize(
        "reason",
        [
            WorkflowReason.PLAN_CHANGED,
            WorkflowReason.DEPLOY_IN_FLIGHT,
            WorkflowReason.REMOVE_FAILED,
            WorkflowReason.COLLECT_FAILED,
            WorkflowReason.CANCELLED,
        ],
    )
    def test_anything_else_is_an_error(self, reason) -> None:
        assert HumanRenderer.removal_code(_removal_result(reason)) == ExitCode.ERROR


# ---------------------------------------------------------------------------
# A removal in `snapl audit` (spec 013 T040, contracts/cli-remove.md "Audit rendering")
# ---------------------------------------------------------------------------


def _removal_events(reason="succeeded", *, workflow_type="RemoveConfig", payload=None, actor="anton"):
    from datetime import timedelta

    from snapl_orchestrator.models import AuditEvent, AuditEventType

    device_id = uuid4()
    at = datetime(2026, 11, 24, 14, 6, tzinfo=UTC)
    common = {"workflow_id": f"remove-config-{device_id}", "workflow_type": workflow_type, "target_id": device_id}
    start_payload = {"confirmed_paths": [_PORT, _NEIGHBOR]} if workflow_type == "RemoveConfig" else {}
    terminal_payload = (
        payload
        if payload is not None
        else {
            "deleted_paths": [_PORT, _NEIGHBOR],
            "device_response": "{}",
            "gone": [_PORT, _NEIGHBOR],
            "remaining": [],
            "new_forward": [],
            "still_refused": [],
            "detail": None,
        }
    )
    return [
        AuditEvent(
            event_id=uuid4(),
            event_type=AuditEventType.WORKFLOW_STARTED,
            timestamp=at,
            actor=actor if workflow_type == "RemoveConfig" else None,
            payload=start_payload,
            **common,
        ),
        AuditEvent(
            event_id=uuid4(),
            event_type=AuditEventType.WORKFLOW_TERMINATED,
            timestamp=at + timedelta(seconds=3),
            outcome="success" if reason == "succeeded" else "failure",
            reason=WorkflowReason(reason),
            payload=terminal_payload,
            **common,
        ),
    ]


class TestRemovalAuditLine:
    def _rows(self, events) -> list[str]:
        console = Console(record=True, width=400, force_terminal=False, no_color=True)
        HumanRenderer(console).audit(events)
        return console.export_text().splitlines()

    def test_a_verified_removal_names_its_deletes(self) -> None:
        terminal = next(row for row in self._rows(_removal_events()) if "workflow_terminated" in row)
        assert f"removed 2: {_PORT}, {_NEIGHBOR}; verified" in terminal

    def test_the_terminal_row_carries_the_actor_from_the_start(self) -> None:
        terminal = next(row for row in self._rows(_removal_events()) if "workflow_terminated" in row)
        assert terminal.rstrip().endswith("anton")

    def test_a_failed_verification_says_what_remains(self) -> None:
        payload = {
            "deleted_paths": [_PORT],
            "device_response": "{}",
            "gone": [],
            "remaining": [_PORT],
            "new_forward": [],
            "still_refused": [],
            "detail": None,
        }
        events = _removal_events("verification_failed", payload=payload)
        terminal = next(row for row in self._rows(events) if "workflow_terminated" in row)
        assert f"removed 1: {_PORT}; not verified, still there: {_PORT}" in terminal

    @pytest.mark.parametrize(
        ("reason", "payload", "text"),
        [
            ("plan_changed", {"plan_paths": [_PORT], "device_response": None, "detail": "changed"}, "plan changed"),
            ("deploy_in_flight", {"deleted_paths": [], "device_response": None, "detail": "x"}, "deploy in flight"),
            ("remove_failed", {"deleted_paths": [], "device_response": None, "detail": "rejected"}, "remove failed"),
        ],
    )
    def test_a_removal_that_wrote_nothing_shows_its_reason(self, reason, payload, text) -> None:
        terminal = next(
            row for row in self._rows(_removal_events(reason, payload=payload)) if "workflow_terminated" in row
        )
        assert f"{text}; nothing removed" in terminal

    def test_a_preview_renders_through_the_existing_table(self) -> None:
        payload = {"plan_paths": [_PORT], "refused_paths": []}
        rows = self._rows(_removal_events(workflow_type="PlanRemoval", payload=payload))
        terminal = next(row for row in rows if "workflow_terminated" in row)
        assert "success" in terminal
        assert "removed" not in terminal
