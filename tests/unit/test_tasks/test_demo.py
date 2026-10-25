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


def _identified_as_ours():
    """Patch the SoT identity check to pass.

    `demo.seed` refuses to write unless the instance identifies positively as
    snapl's (#115), so every test that expects a seed to happen has to say
    which Source of Truth it is talking to.
    """
    from snapl_intent.infrahub.identity import IdentityCheck, SotIdentity

    return patch.object(
        demo,
        "identify_sot",
        AsyncMock(return_value=IdentityCheck(SotIdentity.OURS, "carries snapl's schema")),
    )


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

    with patch.object(demo, "_build_store", return_value=store), _identified_as_ours():
        demo.seed(_ctx())

    assert order == ["provision", "seed"]
    store.provision_schema.assert_awaited_once_with("dcfabric")
    store.seed.assert_awaited_once_with("dcfabric")


def test_seed_honours_an_explicit_use_case(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "t")  # pragma: allowlist secret
    store = MagicMock()
    store.provision_schema = AsyncMock()
    store.seed = AsyncMock()

    with patch.object(demo, "_build_store", return_value=store), _identified_as_ours():
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


# --------------------------------------------------------------------------
# Refusing to write to someone else's Source of Truth (#115)
#
# #107 added the identity check and wired it into `demo.check`, `snapl status`
# and the e2e refusal — every path that *reports*. It was never wired into the
# one task that *writes*, and `demo.up` ran seed before check, so the
# verification happened after the damage. Environment variables are the one
# namespace Docker cannot isolate, and since #111 a shell export deliberately
# outranks development/.env, so `INFRAHUB_ADDRESS=... invoke demo.seed` was
# enough to provision snapl's schema into a neighbouring project's Infrahub —
# authenticated, because both projects shipped the same admin token.
# --------------------------------------------------------------------------


def test_seed_refuses_to_write_to_a_foreign_source_of_truth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "t")  # pragma: allowlist secret
    from snapl_intent.infrahub.identity import IdentityCheck, SotIdentity

    store = MagicMock()
    store.provision_schema = AsyncMock()
    store.seed = AsyncMock()
    foreign = IdentityCheck(SotIdentity.FOREIGN, "localhost:8000 has 12 devices but no snapl marker")

    with (
        patch.object(demo, "_build_store", return_value=store),
        patch.object(demo, "identify_sot", AsyncMock(return_value=foreign)),
        pytest.raises(demo.DemoConfigError, match="refusing to seed"),
    ):
        demo.seed(_ctx())

    store.provision_schema.assert_not_awaited()
    store.seed.assert_not_awaited()


def test_seed_verifies_identity_before_it_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ordering is the whole point — a check that runs after the write is a
    report, not a guard."""
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "t")  # pragma: allowlist secret
    from snapl_intent.infrahub.identity import IdentityCheck, SotIdentity

    order: list[str] = []
    store = MagicMock()
    store.provision_schema = AsyncMock(side_effect=lambda _: order.append("provision"))
    store.seed = AsyncMock(side_effect=lambda _: order.append("seed"))

    async def _identify(*_args, **_kwargs):
        order.append("identify")
        return IdentityCheck(SotIdentity.OURS, "ours")

    with (
        patch.object(demo, "_build_store", return_value=store),
        patch.object(demo, "identify_sot", _identify),
    ):
        demo.seed(_ctx())

    assert order == ["identify", "provision", "seed"]


def test_seed_proceeds_against_an_empty_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh snapl instance has no marker and no devices — that is the
    normal bootstrap case and must not be mistaken for a neighbour's."""
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "t")  # pragma: allowlist secret
    from snapl_intent.infrahub.identity import IdentityCheck, SotIdentity

    store = MagicMock()
    store.provision_schema = AsyncMock()
    store.seed = AsyncMock()
    empty = IdentityCheck(SotIdentity.OURS, "empty and unprovisioned")

    with (
        patch.object(demo, "_build_store", return_value=store),
        patch.object(demo, "identify_sot", AsyncMock(return_value=empty)),
    ):
        demo.seed(_ctx())

    store.seed.assert_awaited_once()


def test_seed_refuses_when_identity_cannot_be_determined(monkeypatch: pytest.MonkeyPatch) -> None:
    """UNKNOWN must not fail open on the write path.

    Measured against the real neighbour: pointed at another project's Infrahub
    with snapl's credentials, `identify` returns UNKNOWN rather than FOREIGN,
    because the instance will not serve its schema to a stranger. A guard that
    only refuses on FOREIGN therefore lets the seed through — the exact case it
    exists to stop. Reporting paths (`demo.check`, `snapl status`) still treat
    UNKNOWN as inconclusive; only writing demands a positive identification.
    """
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "t")  # pragma: allowlist secret
    from snapl_intent.infrahub.identity import IdentityCheck, SotIdentity

    store = MagicMock()
    store.provision_schema = AsyncMock()
    store.seed = AsyncMock()
    unknown = IdentityCheck(SotIdentity.UNKNOWN, "schema could not be read")

    with (
        patch.object(demo, "_build_store", return_value=store),
        patch.object(demo, "identify_sot", AsyncMock(return_value=unknown)),
        pytest.raises(demo.DemoConfigError, match="refusing to seed"),
    ):
        demo.seed(_ctx())

    store.provision_schema.assert_not_awaited()
    store.seed.assert_not_awaited()
