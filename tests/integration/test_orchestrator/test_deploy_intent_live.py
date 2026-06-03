"""Integration test scaffolding for the Orchestrator block.

These tests exercise the full activity graph against a live Temporal cluster
and (optionally) a live SR Linux node + Infrahub. They are skipped when the
required services are unreachable. Run with:

    TEMPORAL_HOST=localhost:7233 \
    SRLINUX_HOST=clab-dcfabric-spine-01 \
    SRLINUX_PASSWORD=... \
    INFRAHUB_API_TOKEN=... \
    uv run pytest tests/integration/test_orchestrator/ -m integration -v

The skip fixtures auto-skip individual tests when their dependencies are not
available, so this test file is safe to run in CI without the full stack.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_deploy_intent_live_against_real_temporal(skip_if_temporal_unreachable) -> None:
    """SC-001: deploy_intent against a real Temporal cluster completes within 60s.

    Pending: wire concrete activities + start a worker in-process; invoke
    DeployIntentWorkflow via client.execute_workflow; assert WorkflowResult.success
    and elapsed time < 60s.
    """
    pytest.skip("Integration scaffold — implementation pending live-stack validation")


def test_deploy_intent_worker_restart_resumes(skip_if_temporal_unreachable) -> None:
    """SC-003: kill worker mid-workflow, restart, verify no work loss."""
    pytest.skip("Integration scaffold — implementation pending live-stack validation")


def test_scan_drift_then_reconcile_loop(
    skip_if_temporal_unreachable,
    skip_if_srlinux_unreachable,
) -> None:
    """SC-007: drift detected → operator-initiated reconcile → state clean."""
    pytest.skip("Integration scaffold — implementation pending live-stack validation")


def test_audit_log_persists_across_worker_restart(skip_if_temporal_unreachable) -> None:
    """SC-005: audit events survive worker process restart."""
    pytest.skip("Integration scaffold — implementation pending live-stack validation")
