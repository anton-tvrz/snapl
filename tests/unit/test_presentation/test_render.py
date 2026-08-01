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
