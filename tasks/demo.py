"""Demo bootstrap tasks — take a clean checkout to a demo-ready environment (#97).

``dev.*`` owns the infrastructure lifecycle (compose stack, containerlab). These
tasks own the two steps between "containers are running" and "the closed loop
demos": populating the Source of Truth, and proving the environment is actually
ready before an audience is watching.

    uv run invoke demo.up       # deps + lab + seed + drift schedule + check
    uv run invoke demo.check    # preflight only — safe to re-run anytime
    uv run invoke demo.reset    # back to nothing, for a clean rehearsal

Connection settings come from the same env vars the Temporal worker reads, with
the same defaults, so a task can never seed a different Infrahub than the worker
queries. See ``docs/demo-scenarios.md`` for the scenarios these enable.
"""

from __future__ import annotations

import asyncio
import contextlib
import getpass
import os
import socket
from dataclasses import dataclass

from invoke import Exit, task

DEFAULT_USE_CASE = "dcfabric"
_PROBE_TIMEOUT_SECONDS = 3.0


class _DemoError(Exit):
    """Base for demo-task failures.

    Subclasses ``invoke.Exit`` so an operator gets one clear line and exit
    code 1 instead of a Python traceback — these are diagnostics, and a
    traceback buries the diagnosis. ``args`` is set explicitly because
    ``Exit`` does not chain to ``Exception.__init__``, which would otherwise
    leave ``str(exc)`` empty.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message=message, code=1)
        self.args = (message,)


class DemoConfigError(_DemoError):
    """Environment configuration is missing or malformed."""


class DemoCheckError(_DemoError):
    """One or more preflight checks failed.

    Carries the full result list so the caller can show everything that was
    probed, not just the first thing that broke.
    """

    def __init__(self, message: str, results: list[CheckResult]) -> None:
        super().__init__(message)
        self.results = results


@dataclass(frozen=True)
class CheckResult:
    """One preflight probe."""

    name: str
    ok: bool
    detail: str = ""

    def render(self) -> str:
        return f"  [{'ok' if self.ok else 'FAIL'}] {self.name}{f' — {self.detail}' if self.detail else ''}"


@dataclass(frozen=True)
class DemoSettings:
    """Connection settings, resolved exactly as the worker resolves them."""

    infrahub_address: str
    infrahub_token: str | None
    temporal_host: str
    srlinux_port: int
    temporal_namespace: str = "default"
    task_queue: str = "snapl-orchestrator"
    audit_db: str = "./snapl-audit.sqlite"

    @classmethod
    def from_env(cls) -> DemoSettings:
        # Imported here so `invoke --list` works without the full stack installed.
        from snapl_intent.infrahub.client import DEFAULT_ADDRESS  # noqa: PLC0415
        from snapl_orchestrator.worker.run import (  # noqa: PLC0415
            DEFAULT_AUDIT_DB,
            DEFAULT_NAMESPACE,
            DEFAULT_TASK_QUEUE,
            DEFAULT_TEMPORAL_HOST,
        )

        raw_port = os.environ.get("SRLINUX_PORT", "57400")
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise DemoConfigError(f"SRLINUX_PORT must be an integer, got {raw_port!r}") from exc

        return cls(
            infrahub_address=os.environ.get("INFRAHUB_ADDRESS") or DEFAULT_ADDRESS,
            infrahub_token=os.environ.get("INFRAHUB_API_TOKEN"),
            temporal_host=os.environ.get("TEMPORAL_HOST") or DEFAULT_TEMPORAL_HOST,
            srlinux_port=port,
            temporal_namespace=os.environ.get("TEMPORAL_NAMESPACE") or DEFAULT_NAMESPACE,
            task_queue=os.environ.get("TEMPORAL_TASK_QUEUE") or DEFAULT_TASK_QUEUE,
            audit_db=os.environ.get("SNAPL_AUDIT_DB") or DEFAULT_AUDIT_DB,
        )

    def require_token(self) -> str:
        if not self.infrahub_token:
            raise DemoConfigError(
                "INFRAHUB_API_TOKEN is required. The dev stack's token is the "
                "INFRAHUB_INITIAL_ADMIN_TOKEN in development/docker-compose.yml."
            )
        return self.infrahub_token


def _build_store(settings: DemoSettings):
    """Construct an InfrahubIntentStore from resolved settings."""
    from snapl_intent.infrahub.client import build_client  # noqa: PLC0415
    from snapl_intent.infrahub.store import InfrahubIntentStore  # noqa: PLC0415

    client = build_client(address=settings.infrahub_address, api_token=settings.require_token())
    return InfrahubIntentStore(client=client)


def identify_sot(client, *, address: str):
    """Classify the Infrahub behind ``client`` — see snapl_intent...identity (#107)."""
    from snapl_intent.infrahub.identity import identify  # noqa: PLC0415

    return identify(client, address=address)


def _probe_tcp(host: str, port: int, *, timeout: float = _PROBE_TIMEOUT_SECONDS) -> bool:
    """True when a TCP connection to host:port completes."""
    with contextlib.suppress(OSError), socket.create_connection((host, port), timeout=timeout):
        return True
    return False


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def _temporal_address(settings: DemoSettings) -> tuple[str, int]:
    host, _, port = settings.temporal_host.rpartition(":")
    return host or "localhost", int(port or 18033)


async def ensure_schedule(settings: DemoSettings, *, use_case: str):
    """Create the use case's report-only drift schedule, or leave it as the operator set it.

    The same ``ensure_drift_schedule`` that ``snapl schedule create`` calls, so
    there is one registration path (spec 009 FR-009). It never creates in
    remediate and never un-pauses. The audit DB is the worker's path, resolved
    the same way, so on a clean checkout this may create it (research R7).
    """
    from snapl_orchestrator import schedules  # noqa: PLC0415
    from snapl_orchestrator.audit.sqlite import SqliteAuditLog  # noqa: PLC0415
    from snapl_orchestrator.worker import client as temporal_client  # noqa: PLC0415

    # The Temporal client retries its handshake forever; probe first so a stack
    # that is not up fails in one line instead of hanging demo.up.
    if not _probe_tcp(*_temporal_address(settings)):
        raise DemoConfigError(
            f"Temporal unreachable at {settings.temporal_host} — start the stack: uv run invoke dev.deps"
        )
    log = SqliteAuditLog(database_url=settings.audit_db)
    await log.initialize()
    try:
        client = await temporal_client.build_client(
            target=settings.temporal_host, namespace=settings.temporal_namespace
        )
        return await schedules.ensure_drift_schedule(
            client,
            use_case_id=use_case,
            task_queue=settings.task_queue,
            audit_log=log,
            actor=f"invoke:demo.up ({getpass.getuser()})",
            interval=schedules.DEFAULT_SCAN_INTERVAL,
        )
    finally:
        await log.close()


async def find_schedule(settings: DemoSettings, *, use_case: str):
    """The use case's drift schedule, or None. Read-only."""
    from snapl_orchestrator import schedules  # noqa: PLC0415
    from snapl_orchestrator.worker import client as temporal_client  # noqa: PLC0415

    client = await temporal_client.build_client(target=settings.temporal_host, namespace=settings.temporal_namespace)
    return await schedules.find_drift_schedule(client, use_case_id=use_case)


def _schedule_check(settings: DemoSettings, use_case: str) -> CheckResult:
    """Unlike `snapl status`, this row fails: a demo with no watching schedule is not ready."""
    try:
        view = asyncio.run(find_schedule(settings, use_case=use_case))
    except Exception as exc:
        first = next((line.strip() for line in str(exc).splitlines() if line.strip()), type(exc).__name__)
        return CheckResult("drift schedule", False, f"could not inspect: {first}")
    if view is None:
        return CheckResult("drift schedule", False, "none — run: uv run invoke demo.up")
    if view.paused:
        reason = f' — "{view.note}"' if view.note else ""
        return CheckResult("drift schedule", False, f"paused{reason} (snapl schedule resume)")
    seconds = int(view.interval.total_seconds())
    every = (
        f"{seconds // 3600}h" if seconds % 3600 == 0 else f"{seconds // 60}m" if seconds % 60 == 0 else f"{seconds}s"
    )
    return CheckResult("drift schedule", True, f"{use_case}: every {every}, {view.mode.value}, active")


@task(help={"use-case": f"Use case to provision and seed (default: {DEFAULT_USE_CASE})"})
def seed(ctx, use_case: str = DEFAULT_USE_CASE):
    """Provision the Infrahub schema and seed the Source of Truth. Idempotent.

    Replaces the copy-paste REPL snippet the demo doc used to carry. Schema
    provisioning must complete before seeding — on a fresh instance the SDK
    caches a schema that does not yet know the use case's attributes and
    silently drops them (#87), so `provision_schema` awaits registration
    before this returns.
    """
    settings = DemoSettings.from_env()
    settings.require_token()
    store = _build_store(settings)

    async def _run():
        # Before the first write, never after (#115). `demo.check` verifies
        # identity too, but it runs at the end of `demo.up` — by then the
        # schema and six devices are already in whichever Infrahub answered.
        # INFRAHUB_ADDRESS is an ordinary environment variable, and a sibling
        # project's instance answering on its port is a successful connection
        # to the wrong Source of Truth.
        from snapl_intent.infrahub.identity import SotIdentity  # noqa: PLC0415

        identity = await identify_sot(store.client, address=settings.infrahub_address)
        # Positively ours, not merely "not provably foreign". `is_foreign`
        # alone fails open on UNKNOWN, and UNKNOWN is what a neighbour's
        # instance actually returns when it refuses to serve its schema to
        # snapl's credentials — measured, not assumed. A healthy snapl stack
        # identifies as OURS whether it is freshly booted or fully seeded, so
        # requiring that costs a legitimate bootstrap nothing.
        if identity.identity is not SotIdentity.OURS:
            raise DemoConfigError(
                f"refusing to seed: {identity.detail}.\n"
                "Point INFRAHUB_ADDRESS at snapl's own instance, or start it: "
                "uv run invoke dev.deps"
            )

        print(f"Provisioning '{use_case}' schema against {settings.infrahub_address} ...")
        provision = await store.provision_schema(use_case)
        print(f"  schema: {provision}")
        print(f"Seeding '{use_case}' ...")
        result = await store.seed(use_case)
        print(f"  seed: {result}")

    from snapl_intent.exceptions import IntentError  # noqa: PLC0415

    try:
        asyncio.run(_run())
    except IntentError as exc:
        raise DemoConfigError(
            f"seeding '{use_case}' failed against {settings.infrahub_address}: {exc}\n"
            "Is the stack up? Try: uv run invoke dev.deps && uv run invoke demo.check"
        ) from exc

    print("Source of Truth is populated. Verify with: uv run invoke demo.check")


@task(help={"use-case": f"Use case to verify (default: {DEFAULT_USE_CASE})"})
def check(ctx, use_case: str = DEFAULT_USE_CASE):
    """Preflight the demo environment — every probe runs, then it reports.

    Answers "is this thing ready?" in one command instead of leaving it to be
    discovered as a workflow failure mid-demo.
    """
    settings = DemoSettings.from_env()
    results: list[CheckResult] = []

    temporal_ok = _probe_tcp(*_temporal_address(settings))
    results.append(CheckResult(name="temporal reachable", ok=temporal_ok, detail=settings.temporal_host))
    if temporal_ok:
        results.append(_schedule_check(settings, use_case))

    states: list = []
    if not settings.infrahub_token:
        results.append(CheckResult("infrahub token set", False, "INFRAHUB_API_TOKEN is unset"))
    else:
        store = _build_store(settings)
        try:
            states = asyncio.run(store.get_desired_state(use_case=use_case))
        except Exception as exc:
            results.append(CheckResult("source of truth reachable", False, f"{type(exc).__name__}: {exc}"))
        else:
            results.append(CheckResult("source of truth reachable", True, settings.infrahub_address))
            # Reachable is not the same as ours (#107): several sibling
            # projects serve Infrahub, and pointing at one of theirs is a
            # successful connection to the wrong Source of Truth.
            identity = asyncio.run(identify_sot(store.client, address=settings.infrahub_address))
            results.append(CheckResult("source of truth is snapl's", not identity.is_foreign, identity.detail))
            results.append(
                CheckResult(
                    name=f"'{use_case}' seeded",
                    ok=bool(states),
                    detail=f"{len(states)} devices" if states else "no devices — run: uv run invoke demo.seed",
                )
            )

    for state in states:
        target = state.device.lab_node_name or state.device.management_address
        if not target:
            results.append(CheckResult(f"gnmi {state.device.name}", False, "no dial target in the SoT (#96)"))
            continue
        results.append(
            CheckResult(
                name=f"gnmi {state.device.name}",
                ok=_probe_tcp(target, settings.srlinux_port),
                detail=f"{target}:{settings.srlinux_port}",
            )
        )

    print("Demo preflight:")
    for result in results:
        print(result.render())

    failed = [result for result in results if not result.ok]
    if failed:
        raise DemoCheckError(
            f"{len(failed)} of {len(results)} checks failed: {', '.join(r.name for r in failed)}",
            results,
        )

    print(f"All {len(results)} checks passed — ready to demo.")
    return results


@task(help={"use-case": f"Use case to seed (default: {DEFAULT_USE_CASE})"})
def up(ctx, use_case: str = DEFAULT_USE_CASE):
    """Clean checkout to demo-ready: deps + lab + seed + report-only drift schedule + preflight."""
    from . import dev  # noqa: PLC0415 — avoids a circular import at module load

    dev.deps(ctx)
    dev.lab_deploy(ctx)
    # seed refuses a foreign or unidentifiable SoT, so a schedule is never
    # created against intent that is not snapl's (spec 009 FR-031).
    seed(ctx, use_case=use_case)
    view = asyncio.run(ensure_schedule(DemoSettings.from_env(), use_case=use_case))
    print(f"Drift schedule {view.schedule_id}: {view.mode.value}, {'paused' if view.paused else 'active'}")
    check(ctx, use_case=use_case)
    print("\nStart the worker in its own terminal: uv run invoke orchestrator.start")


@task
def reset(ctx):
    """Tear everything down — stack, volumes, and lab — for a clean rehearsal."""
    from . import dev  # noqa: PLC0415

    dev.lab_destroy(ctx)
    dev.down(ctx, volumes=True)
    print("Environment reset. Rebuild with: uv run invoke demo.up")
