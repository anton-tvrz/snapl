"""Unit tests for the demo bootstrap tasks (Issue #97).

These tasks are what stands between "an expert can drive it from a REPL
following a runbook" and "a clean checkout demos". They must therefore be
correct about two things the runbook got wrong by hand: connection settings
resolved identically to the worker's, and a preflight that fails loudly
rather than leaving a half-seeded SoT to be discovered mid-demo.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from invoke import Context

from tasks import demo, ns

pytestmark = pytest.mark.unit


def _ctx() -> MagicMock:
    return MagicMock(spec=Context)


# --------------------------------------------------------------------------
# Settings resolution — must not drift from the worker's
# --------------------------------------------------------------------------


def test_settings_defaults_match_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """A task that seeds a different Infrahub than the worker reads is the
    single most confusing failure mode available here."""
    for key in ("INFRAHUB_ADDRESS", "INFRAHUB_API_TOKEN", "TEMPORAL_HOST", "SRLINUX_PORT"):
        monkeypatch.delenv(key, raising=False)

    settings = demo.DemoSettings.from_env()

    from snapl_intent.infrahub.client import DEFAULT_ADDRESS
    from snapl_orchestrator.worker.run import DEFAULT_TEMPORAL_HOST

    assert settings.infrahub_address == DEFAULT_ADDRESS
    assert settings.temporal_host == DEFAULT_TEMPORAL_HOST


def test_settings_read_the_same_env_vars_the_worker_does(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_ADDRESS", "http://localhost:8001")
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "token-abc")  # pragma: allowlist secret
    monkeypatch.setenv("TEMPORAL_HOST", "localhost:7234")
    monkeypatch.setenv("SRLINUX_PORT", "57401")

    settings = demo.DemoSettings.from_env()

    assert settings.infrahub_address == "http://localhost:8001"
    assert settings.infrahub_token == "token-abc"  # noqa: S105 — test fixture  # pragma: allowlist secret
    assert settings.temporal_host == "localhost:7234"
    assert settings.srlinux_port == 57401


def test_settings_reject_a_non_integer_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRLINUX_PORT", "not-a-port")
    with pytest.raises(demo.DemoConfigError, match="SRLINUX_PORT"):
        demo.DemoSettings.from_env()


# --------------------------------------------------------------------------
# demo.seed
# --------------------------------------------------------------------------


def test_seed_provisions_the_schema_before_seeding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ordering is not cosmetic: seeding into an unregistered schema is the
    #87 race, where the SDK silently drops attributes it does not know."""
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "t")  # pragma: allowlist secret
    store = MagicMock()
    order: list[str] = []
    store.provision_schema = AsyncMock(side_effect=lambda _: order.append("provision"))
    store.seed = AsyncMock(side_effect=lambda _: order.append("seed"))

    with patch.object(demo, "_build_store", return_value=store):
        demo.seed(_ctx())

    assert order == ["provision", "seed"]
    store.provision_schema.assert_awaited_once_with("dcfabric")
    store.seed.assert_awaited_once_with("dcfabric")


def test_seed_honours_an_explicit_use_case(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "t")  # pragma: allowlist secret
    store = MagicMock()
    store.provision_schema = AsyncMock()
    store.seed = AsyncMock()

    with patch.object(demo, "_build_store", return_value=store):
        demo.seed(_ctx(), use_case="test_edge")

    store.provision_schema.assert_awaited_once_with("test_edge")
    store.seed.assert_awaited_once_with("test_edge")


def test_seed_requires_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INFRAHUB_API_TOKEN", raising=False)
    with pytest.raises(demo.DemoConfigError, match="INFRAHUB_API_TOKEN"):
        demo.seed(_ctx())


def test_seed_turns_a_connection_failure_into_a_pointed_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stack that is not up is the most common way this fails — it should
    say so and name the next command, not print an SDK traceback."""
    from snapl_intent.exceptions import IntentConnectionError

    monkeypatch.setenv("INFRAHUB_API_TOKEN", "t")  # pragma: allowlist secret
    store = MagicMock()
    store.provision_schema = AsyncMock(side_effect=IntentConnectionError("connection refused"))

    with (
        patch.object(demo, "_build_store", return_value=store),
        pytest.raises(demo.DemoConfigError) as excinfo,
    ):
        demo.seed(_ctx())

    assert "dev.deps" in str(excinfo.value)


def test_demo_errors_exit_cleanly_instead_of_tracing_back() -> None:
    """These are operator diagnostics; a traceback buries the diagnosis."""
    from invoke import Exit

    assert issubclass(demo.DemoConfigError, Exit)
    assert issubclass(demo.DemoCheckError, Exit)
    assert demo.DemoConfigError("boom").code == 1
    assert str(demo.DemoConfigError("boom")) == "boom"


# --------------------------------------------------------------------------
# demo.check — the preflight
# --------------------------------------------------------------------------


def _states(count: int, *, dial_target: str | None = "172.20.21.11") -> list[MagicMock]:
    states = []
    for index in range(count):
        state = MagicMock()
        state.device.name = f"device-{index}"
        state.device.lab_node_name = dial_target
        state.device.management_address = None
        states.append(state)
    return states


def test_check_passes_when_everything_is_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "t")  # pragma: allowlist secret
    store = MagicMock()
    store.get_desired_state = AsyncMock(return_value=_states(6))

    with (
        patch.object(demo, "_build_store", return_value=store),
        patch.object(demo, "_probe_tcp", return_value=True),
    ):
        results = demo.check(_ctx())

    assert all(result.ok for result in results), [r for r in results if not r.ok]


def test_check_fails_when_the_sot_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unseeded SoT must be reported here, not as a workflow failure later."""
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "t")  # pragma: allowlist secret
    store = MagicMock()
    store.get_desired_state = AsyncMock(return_value=[])

    with (
        patch.object(demo, "_build_store", return_value=store),
        patch.object(demo, "_probe_tcp", return_value=True),
        pytest.raises(demo.DemoCheckError),
    ):
        demo.check(_ctx())


def test_check_fails_when_a_device_has_no_dial_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """The #96 failure mode, caught before the demo instead of during it."""
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "t")  # pragma: allowlist secret
    store = MagicMock()
    store.get_desired_state = AsyncMock(return_value=_states(1, dial_target=None))

    with (
        patch.object(demo, "_build_store", return_value=store),
        patch.object(demo, "_probe_tcp", return_value=True),
        pytest.raises(demo.DemoCheckError),
    ):
        demo.check(_ctx())


def test_check_fails_when_gnmi_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "t")  # pragma: allowlist secret
    store = MagicMock()
    store.get_desired_state = AsyncMock(return_value=_states(2))

    with (
        patch.object(demo, "_build_store", return_value=store),
        patch.object(demo, "_probe_tcp", return_value=False),
        pytest.raises(demo.DemoCheckError),
    ):
        demo.check(_ctx())


def test_check_reports_every_probe_before_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    """One broken check must not hide the state of the others — the operator
    wants the whole picture in one run, not one fix per invocation."""
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "t")  # pragma: allowlist secret
    store = MagicMock()
    store.get_desired_state = AsyncMock(return_value=_states(2))

    with (
        patch.object(demo, "_build_store", return_value=store),
        patch.object(demo, "_probe_tcp", return_value=False),
        pytest.raises(demo.DemoCheckError) as excinfo,
    ):
        demo.check(_ctx())

    assert "gnmi" in str(excinfo.value).lower()
    assert excinfo.value.results, "the failure must carry the full result list"
    assert any(result.ok for result in excinfo.value.results), "passing checks are still reported"


# --------------------------------------------------------------------------
# Task registration
# --------------------------------------------------------------------------


def test_demo_namespace_is_registered() -> None:
    assert "demo" in ns.collections
    assert set(ns.collections["demo"].task_names) >= {"seed", "check", "up", "reset"}
