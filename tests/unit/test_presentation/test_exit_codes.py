"""The exit-code contract (spec 006 FR-011, SC-005).

This is the CLI's machine-facing API. A caller reading only the exit code must
be able to tell "clean", "drifted" and "broken" apart — so these tests pin the
mapping rather than the wording of any output.
"""

from __future__ import annotations

import pytest

from snapl_orchestrator.models import WorkflowReason
from snapl_presentation.exit_codes import ExitCode
from snapl_presentation.render import HumanRenderer

pytestmark = pytest.mark.unit


def test_codes_are_the_documented_integers() -> None:
    assert (int(ExitCode.OK), int(ExitCode.ERROR), int(ExitCode.DRIFT)) == (0, 1, 2)


class TestScanCode:
    def test_clean_fabric_is_ok(self, scan_result) -> None:
        assert HumanRenderer.scan_code(scan_result()) is ExitCode.OK

    def test_drift_is_two_not_one(self, scan_result) -> None:
        """Drift is a finding, not a failure of the command."""
        assert HumanRenderer.scan_code(scan_result(drifted=1)) is ExitCode.DRIFT

    def test_an_errored_device_outranks_drift(self, scan_result) -> None:
        """A caller must not be told "just drift" when part of the fabric was
        never actually evaluated — that would under-report the problem."""
        assert HumanRenderer.scan_code(scan_result(drifted=1, errored=1)) is ExitCode.ERROR

    def test_errors_alone_are_operational_failures(self, scan_result) -> None:
        assert HumanRenderer.scan_code(scan_result(errored=1)) is ExitCode.ERROR


class TestDeployCode:
    def test_success_is_ok(self, workflow_result) -> None:
        assert HumanRenderer.deploy_code(workflow_result()) is ExitCode.OK

    def test_failure_is_error(self, workflow_result) -> None:
        result = workflow_result(success=False, reason=WorkflowReason.APPLY_FAILED)
        assert HumanRenderer.deploy_code(result) is ExitCode.ERROR

    def test_verification_failure_is_error_not_drift(self, workflow_result) -> None:
        """A deploy that did not take effect failed at its job — unlike a scan,
        which is *supposed* to find drift."""
        result = workflow_result(success=False, reason=WorkflowReason.VERIFICATION_FAILED)
        assert HumanRenderer.deploy_code(result) is ExitCode.ERROR


class TestReconcileCode:
    def test_all_succeeded_is_ok(self, reconcile_result) -> None:
        assert HumanRenderer.reconcile_code(reconcile_result(succeeded=2)) is ExitCode.OK

    def test_any_failure_is_error(self, reconcile_result) -> None:
        assert HumanRenderer.reconcile_code(reconcile_result(succeeded=1, failed=1)) is ExitCode.ERROR

    def test_skips_are_not_failures(self, reconcile_result) -> None:
        """A device already being deployed, or absent from the SoT, is a
        reported outcome — not a broken run (#66/#35)."""
        assert HumanRenderer.reconcile_code(reconcile_result(succeeded=1, skipped=1)) is ExitCode.OK
