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
