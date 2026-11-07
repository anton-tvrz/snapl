"""The ``snapl`` operator CLI (spec 006, issue #63).

A thin client over the Orchestrator's workflows. It contains no network logic,
no gNMI and no drift computation — a bug in the loop is never fixed here.

    snapl deploy spine-01
    snapl scan --use-case dcfabric
    snapl reconcile --use-case dcfabric --drifted
    snapl audit --device spine-01
    snapl status
    snapl schedule create --every 5m
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import getpass
import re
from datetime import timedelta
from typing import TYPE_CHECKING, Annotated, Any
from uuid import uuid4

import typer
from rich.console import Console

from snapl_observability.models import DriftStatus
from snapl_presentation.exceptions import CliError, ConnectionCliError, first_line
from snapl_presentation.exit_codes import ExitCode
from snapl_presentation.probes import connect as _connect
from snapl_presentation.probes import count_pollers, identify_sot
from snapl_presentation.render import build_renderer
from snapl_presentation.resolve import build_store, load_states, resolve_device, resolve_devices
from snapl_presentation.settings import CliSettings

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

app = typer.Typer(
    name="snapl",
    help="Operate the NAF closed loop: deploy intent, scan for drift, reconcile, audit.",
    no_args_is_help=True,
    add_completion=False,
)

_stderr = Console(stderr=True)

DEFAULT_USE_CASE = "dcfabric"

UseCaseOpt = Annotated[str, typer.Option("--use-case", help="Use case to operate on.")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Emit JSON on stdout instead of a table.")]
YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")]
EveryOpt = Annotated[str, typer.Option("--every", help="Scan interval: <n>s, <n>m or <n>h (minimum 1m).")]

schedule_app = typer.Typer(
    name="schedule",
    help="Steer the drift schedule that scans a use case on an interval (spec 009).",
    no_args_is_help=True,
)
app.add_typer(schedule_app)


def command(fn: Callable) -> Callable:
    """Run an async command body, mapping its outcome onto the exit contract.

    Every anticipated failure arrives here as a CliError and leaves as one line
    plus a code (FR-014). Anything else is a bug and keeps its traceback —
    hiding those would make the CLI harder to fix, not friendlier.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any):
        try:
            code = asyncio.run(fn(*args, **kwargs))
        except CliError as exc:
            _stderr.print(f"[red]error:[/] {exc.render()}")
            raise typer.Exit(int(exc.code)) from None
        except KeyboardInterrupt:
            # The workflow is server-side; our exit does not stop it (FR /
            # SC-008). Say so, or the operator assumes they cancelled it.
            _stderr.print("\n[yellow]interrupted[/] — the workflow keeps running; re-attach with `snapl status`")
            raise typer.Exit(int(ExitCode.ERROR)) from None
        raise typer.Exit(int(code or ExitCode.OK))

    return wrapper


async def _require_worker(client: Any, settings: CliSettings) -> None:
    """Refuse to start work nothing will pick up (spec 006 US1 scenario 4).

    Temporal happily accepts a workflow with no worker polling: it sits in the
    queue and ``execute_workflow`` blocks forever. To an operator that is
    indistinguishable from a hang, and it is the single most likely way to be
    stuck during a demo — the stack is up, the worker terminal is not.
    """
    try:
        pollers = await count_pollers(client, settings)
    except Exception:
        return
    if pollers == 0:
        raise CliError(
            f"no worker is polling task queue {settings.task_queue!r}",
            hint="Start one in another terminal: uv run invoke orchestrator.start",
        )


async def _open_audit_log(settings: CliSettings):
    """The worker's durable audit log, refusing a path that does not exist.

    For a reader, an absent database is not an empty one: "0 events" for a path
    never written is indistinguishable from a real empty log. For a writer
    (schedule changes) it is worse — a fresh file at a mismatched path would
    hold "switched to remediate by X" where neither the worker nor `snapl
    audit` ever looks.
    """
    from pathlib import Path  # noqa: PLC0415

    from snapl_orchestrator.audit.sqlite import SqliteAuditLog  # noqa: PLC0415

    if settings.audit_db != ":memory:" and not Path(settings.audit_db).exists():
        raise CliError(
            f"no audit log at {settings.audit_db}",
            hint="Set SNAPL_AUDIT_DB to the path the worker writes, or run a workflow first.",
        )
    log = SqliteAuditLog(database_url=settings.audit_db)
    await log.initialize()
    return log


def _confirm(message: str, *, yes: bool, as_json: bool) -> None:
    """Gate a multi-device write behind a prompt (FR-012).

    A JSON consumer cannot answer a prompt, so --json without --yes refuses
    rather than blocking on stdin forever.
    """
    if yes:
        return
    if as_json:
        raise CliError("--json requires --yes for a command that writes to devices")
    if not typer.confirm(message):
        raise CliError("aborted", code=ExitCode.ERROR)


@app.command()
@command
async def deploy(
    device: Annotated[str, typer.Argument(help="Device name, as it appears in the SoT.")],
    use_case: UseCaseOpt = DEFAULT_USE_CASE,
    as_json: JsonOpt = False,
) -> ExitCode:
    """Deploy intended state to one device and verify it took effect."""
    from temporalio.common import WorkflowIDConflictPolicy  # noqa: PLC0415

    from snapl_orchestrator.workflows.deploy_intent import DeployIntentWorkflow  # noqa: PLC0415

    settings = CliSettings.from_env()
    states = await load_states(build_store(settings), settings, use_case=use_case)
    device_id = resolve_device(states, device)

    client = await _connect(settings)
    await _require_worker(client, settings)
    result = await client.execute_workflow(
        DeployIntentWorkflow.run,
        device_id,
        # The documented id family — per-device serialization is the
        # Orchestrator's contract and the CLI must not route around it (FR-015).
        id=f"deploy-intent-{device_id}",
        task_queue=settings.task_queue,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )

    renderer = build_renderer(as_json=as_json)
    renderer.deploy(result)
    return renderer.deploy_code(result)


@app.command()
@command
async def scan(use_case: UseCaseOpt = DEFAULT_USE_CASE, as_json: JsonOpt = False) -> ExitCode:
    """Scan a use case for drift. Exits 2 when drift is found, 1 on error."""
    from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow  # noqa: PLC0415

    settings = CliSettings.from_env()
    client = await _connect(settings)
    await _require_worker(client, settings)
    result = await client.execute_workflow(
        ScanDriftWorkflow.run,
        use_case,
        id=f"scan-drift-{use_case}-{uuid4()}",
        task_queue=settings.task_queue,
    )

    renderer = build_renderer(as_json=as_json)
    renderer.scan(result)
    return renderer.scan_code(result)


@app.command()
@command
async def reconcile(
    devices: Annotated[list[str] | None, typer.Argument(help="Device names to reconcile.")] = None,
    use_case: UseCaseOpt = DEFAULT_USE_CASE,
    drifted: Annotated[bool, typer.Option("--drifted", help="Reconcile every drifted device in the use case.")] = False,
    yes: YesOpt = False,
    as_json: JsonOpt = False,
) -> ExitCode:
    """Re-apply intent to devices, healing drift."""
    from snapl_orchestrator.workflows.reconcile_devices import ReconcileDevicesWorkflow  # noqa: PLC0415
    from snapl_orchestrator.workflows.scan_drift import ScanDriftWorkflow  # noqa: PLC0415

    if bool(devices) == drifted:
        raise CliError("name devices to reconcile, or pass --drifted — not both, not neither")

    settings = CliSettings.from_env()
    client = await _connect(settings)
    await _require_worker(client, settings)

    if drifted:
        scan_result = await client.execute_workflow(
            ScanDriftWorkflow.run,
            use_case,
            id=f"scan-drift-{use_case}-{uuid4()}",
            task_queue=settings.task_queue,
        )
        target_ids: list[UUID] = [
            report.device_id for report in scan_result.reports.values() if report.status is DriftStatus.DRIFTED
        ]
        names = [report.device_name for report in scan_result.reports.values() if report.status is DriftStatus.DRIFTED]
        if not target_ids:
            _stderr.print(f"no drifted devices in {use_case} — nothing to reconcile")
            return ExitCode.OK
    else:
        states = await load_states(build_store(settings), settings, use_case=use_case)
        target_ids = resolve_devices(states, devices or [])
        names = list(devices or [])

    _confirm(f"Reconcile {len(target_ids)} device(s) — {', '.join(names)}?", yes=yes, as_json=as_json)

    result = await client.execute_workflow(
        ReconcileDevicesWorkflow.run,
        target_ids,
        id=f"reconcile-{uuid4()}",
        task_queue=settings.task_queue,
    )

    renderer = build_renderer(as_json=as_json)
    renderer.reconcile(result)
    return renderer.reconcile_code(result)


@app.command()
@command
async def audit(
    workflow: Annotated[str | None, typer.Option("--workflow", help="Workflow id to query.")] = None,
    device: Annotated[str | None, typer.Option("--device", help="Device name to query.")] = None,
    use_case: UseCaseOpt = DEFAULT_USE_CASE,
    as_json: JsonOpt = False,
) -> ExitCode:
    """Read the durable audit trail by workflow or by device."""
    if bool(workflow) == bool(device):
        raise CliError("pass exactly one of --workflow or --device")

    settings = CliSettings.from_env()
    log = await _open_audit_log(settings)
    try:
        if workflow:
            events = await log.query_by_workflow(workflow)
        else:
            states = await load_states(build_store(settings), settings, use_case=use_case)
            events = await log.query_by_device(resolve_device(states, device or ""))
    finally:
        await log.close()

    build_renderer(as_json=as_json).audit(events)
    return ExitCode.OK


@app.command()
@command
async def status(use_case: UseCaseOpt = DEFAULT_USE_CASE, as_json: JsonOpt = False) -> ExitCode:
    """Report whether Temporal, the SoT and the worker are healthy."""
    from snapl_orchestrator.worker.client import list_running_workflows  # noqa: PLC0415

    settings = CliSettings.from_env()
    checks: list[tuple[str, bool, str]] = []
    running: list[Any] = []

    try:
        client = await _connect(settings)
    except ConnectionCliError as exc:
        checks.append(("temporal", False, exc.message))
        client = None
    else:
        checks.append(("temporal", True, settings.temporal_host))
        running = await list_running_workflows(client, task_queue=settings.task_queue)
        # Poller count, not mere queue existence: a queue nothing polls looks
        # identical to a healthy one until a workflow sits in it forever
        # (US5 scenario 3).
        try:
            pollers = await count_pollers(client, settings)
        except Exception as exc:
            checks.append(("worker", False, f"could not inspect {settings.task_queue}: {first_line(str(exc))}"))
        else:
            checks.append(
                (
                    "worker",
                    pollers > 0,
                    f"{pollers} polling {settings.task_queue}"
                    if pollers
                    else f"nothing polling {settings.task_queue} — uv run invoke orchestrator.start",
                )
            )
        from snapl_orchestrator import schedules  # noqa: PLC0415

        try:
            view = await schedules.find_drift_schedule(client, use_case_id=use_case)
        except Exception as exc:
            checks.append(("drift schedule", True, f"could not inspect: {first_line(str(exc))}"))
        else:
            checks.append(_schedule_status_row(view, use_case))

    try:
        states = await load_states(build_store(settings), settings, use_case=use_case)
    except CliError as exc:
        checks.append(("source of truth", False, exc.message))
    else:
        checks.append(("source of truth", True, settings.infrahub_address))
        # Reachable is not the same as ours (#107).
        identity = await identify_sot(build_store(settings), address=settings.infrahub_address)
        checks.append(("source of truth is snapl's", not identity.is_foreign, identity.detail))
        checks.append((f"'{use_case}' seeded", bool(states), f"{len(states)} devices"))

    build_renderer(as_json=as_json).status(checks, running)
    return ExitCode.OK if all(ok for _, ok, _ in checks) else ExitCode.ERROR


# ---------------------------------------------------------------------------
# snapl schedule (spec 009)
#
# None of these requires a worker (FR-022): "stop the unattended writes" is the
# command most likely to be needed when the worker is what is wrong. They warn
# instead, because a schedule with no worker queues runs nobody executes.
# ---------------------------------------------------------------------------

_INTERVAL = re.compile(r"^(\d+)([smh])$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours"}


def _parse_interval(text: str) -> timedelta:
    from snapl_orchestrator.schedules import MIN_SCAN_INTERVAL  # noqa: PLC0415

    match = _INTERVAL.match(text.strip())
    if match is None:
        raise CliError(f"cannot read interval {text!r}", hint="Use <n>s, <n>m or <n>h — for example 90s, 5m, 1h.")
    interval = timedelta(**{_UNITS[match[2]]: int(match[1])})
    if interval < MIN_SCAN_INTERVAL:
        # Refused, not silently raised: an operator who asked for 30s should
        # learn it was not honoured.
        raise CliError(f"interval {text} is below the 1m minimum")
    return interval


def _actor() -> str:
    return getpass.getuser()


def _no_schedule(use_case: str) -> CliError:
    return CliError(
        f"no drift schedule for {use_case}",
        hint=f"Create one: snapl schedule create --use-case {use_case}",
    )


def _unreadable_schedule(use_case: str, exc: Exception) -> CliError:
    return CliError(
        str(exc),
        hint=f"Restore an interval: snapl schedule create --use-case {use_case} --every 5m",
    )


async def _warn_if_no_worker(client: Any, settings: CliSettings) -> None:
    try:
        pollers = await count_pollers(client, settings)
    except Exception:
        return
    if pollers == 0:
        _stderr.print(
            f"[yellow]warning:[/] no worker is polling task queue {settings.task_queue!r} — "
            "scheduled runs will wait (uv run invoke orchestrator.start)"
        )


async def _refuse_foreign_sot(settings: CliSettings) -> None:
    """A schedule scans, and may repair, against whatever SoT the worker reads (FR-031).

    Only a positive "this is another project's" refuses; an Infrahub that could
    not be read is not evidence of anything (#107).
    """
    identity = await identify_sot(build_store(settings), address=settings.infrahub_address)
    if identity.is_foreign:
        raise CliError(
            f"refusing to create a drift schedule: {identity.detail}",
            hint="Point INFRAHUB_ADDRESS at snapl's Infrahub.",
        )


async def _schedule_write(settings: CliSettings, use_case: str, operation: Callable) -> Any:
    """Run one mutating schedule call with the audit log open.

    The log is opened (and a missing one refused) before Temporal is touched,
    so a change is never applied without its audit record (FR-021).
    """
    from snapl_orchestrator.exceptions import (  # noqa: PLC0415
        ScheduleNotFoundError,
        ScheduleRunInProgressError,
        UnreadableScheduleError,
    )

    log = await _open_audit_log(settings)
    try:
        client = await _connect(settings)
        await _warn_if_no_worker(client, settings)
        return await operation(client, log)
    except ScheduleNotFoundError:
        raise _no_schedule(use_case) from None
    except UnreadableScheduleError as exc:
        raise _unreadable_schedule(use_case, exc) from None
    except ScheduleRunInProgressError as exc:
        raise CliError(str(exc), hint="Follow it: snapl schedule show") from None
    finally:
        await log.close()


@schedule_app.command("create")
@command
async def schedule_create(
    use_case: UseCaseOpt = DEFAULT_USE_CASE,
    every: EveryOpt = "5m",
    as_json: JsonOpt = False,
) -> ExitCode:
    """Put a use case on a report-only drift schedule, or change its interval."""
    from snapl_orchestrator import schedules  # noqa: PLC0415

    settings = CliSettings.from_env()
    interval = _parse_interval(every)
    await _refuse_foreign_sot(settings)
    log = await _open_audit_log(settings)
    try:
        client = await _connect(settings)
        await _warn_if_no_worker(client, settings)
        view = await schedules.ensure_drift_schedule(
            client,
            use_case_id=use_case,
            task_queue=settings.task_queue,
            audit_log=log,
            actor=_actor(),
            interval=interval,
        )
    finally:
        await log.close()

    build_renderer(as_json=as_json).schedule(view)
    return ExitCode.OK


@schedule_app.command("show")
@command
async def schedule_show(use_case: UseCaseOpt = DEFAULT_USE_CASE, as_json: JsonOpt = False) -> ExitCode:
    """Show a use case's drift schedule and its recent runs."""
    from snapl_orchestrator import schedules  # noqa: PLC0415
    from snapl_orchestrator.exceptions import ScheduleNotFoundError, UnreadableScheduleError  # noqa: PLC0415

    settings = CliSettings.from_env()
    client = await _connect(settings)
    await _warn_if_no_worker(client, settings)
    try:
        view = await schedules.describe_drift_schedule(client, use_case_id=use_case)
    except ScheduleNotFoundError:
        raise _no_schedule(use_case) from None
    except UnreadableScheduleError as exc:
        raise _unreadable_schedule(use_case, exc) from None

    build_renderer(as_json=as_json).schedule(view)
    return ExitCode.OK


@schedule_app.command("pause")
@command
async def schedule_pause(
    reason: Annotated[str | None, typer.Option("--reason", help="Why scans stop — required.")] = None,
    use_case: UseCaseOpt = DEFAULT_USE_CASE,
) -> ExitCode:
    """Suppress: stop scheduled runs until resumed, with a stated reason."""
    from snapl_orchestrator import schedules  # noqa: PLC0415

    # Checked here rather than as a required Typer option, whose usage error
    # would exit 2 — the code this CLI reserves for drift (spec 006 FR-011).
    if not reason or not reason.strip():
        raise CliError("pause needs --reason", hint='For example: --reason "maintenance: spine-01 optics"')
    settings = CliSettings.from_env()
    await _schedule_write(
        settings,
        use_case,
        lambda client, log: schedules.pause_drift_schedule(
            client, use_case_id=use_case, reason=reason, audit_log=log, actor=_actor()
        ),
    )
    _stderr.print(f'drift schedule for {use_case} paused — "{reason}"')
    return ExitCode.OK


@schedule_app.command("resume")
@command
async def schedule_resume(
    use_case: UseCaseOpt = DEFAULT_USE_CASE,
    note: Annotated[str | None, typer.Option("--note", help="Optional note recorded with the resume.")] = None,
) -> ExitCode:
    """Resume scheduled runs at the next interval."""
    from snapl_orchestrator import schedules  # noqa: PLC0415

    settings = CliSettings.from_env()
    await _schedule_write(
        settings,
        use_case,
        lambda client, log: schedules.resume_drift_schedule(
            client, use_case_id=use_case, audit_log=log, actor=_actor(), note=note
        ),
    )
    _stderr.print(f"drift schedule for {use_case} resumed")
    return ExitCode.OK


async def _remediate_authorization(settings: CliSettings, use_case: str, interval: timedelta) -> str:
    """What switching to (or triggering) remediate authorizes, in words (FR-012).

    The wording is part of the contract: the operator is agreeing to unattended
    writes, including SoT edits reaching the fabric without a `deploy`.
    """
    from snapl_presentation.render import format_interval  # noqa: PLC0415

    try:
        devices = str(len(await load_states(build_store(settings), settings, use_case=use_case)))
    except CliError:
        # Switching mode writes nothing to devices, so an unreachable SoT does
        # not block the decision — it only makes the count unknowable.
        devices = "unknown (SoT unreachable)"
    return (
        f"  Every {format_interval(interval)}, devices whose scan shows drift a deploy can fix will be redeployed\n"
        "  without asking — including devices whose intent was changed in the Source of\n"
        f"  Truth. Undesired config is never removed. Devices: {devices}.\n"
        "  A failed repair is retried every run until you switch back to report.\n"
        "Continue?"
    )


@schedule_app.command("mode")
@command
async def schedule_mode(
    mode: Annotated[str, typer.Argument(help="report (write nothing) or remediate (repair forward drift).")],
    use_case: UseCaseOpt = DEFAULT_USE_CASE,
    yes: YesOpt = False,
    as_json: JsonOpt = False,
) -> ExitCode:
    """Choose what scheduled runs do with the drift they find."""
    from snapl_orchestrator import schedules  # noqa: PLC0415
    from snapl_orchestrator.models import DriftResponseMode  # noqa: PLC0415

    try:
        target = DriftResponseMode(mode)
    except ValueError:
        raise CliError(
            f"unknown mode {mode!r} — use report or remediate",
            hint="To stop scheduled runs entirely, pause instead: snapl schedule pause --reason ...",
        ) from None
    settings = CliSettings.from_env()

    async def _switch(client: Any, log: Any):
        current = await schedules.describe_drift_schedule(client, use_case_id=use_case)
        if target is DriftResponseMode.REMEDIATE:
            body = await _remediate_authorization(settings, use_case, current.interval)
            _confirm(f"Switch {use_case} to remediate?\n{body}", yes=yes, as_json=as_json)
        return await schedules.set_drift_response(
            client, use_case_id=use_case, mode=target, audit_log=log, actor=_actor()
        )

    view = await _schedule_write(settings, use_case, _switch)
    build_renderer(as_json=as_json).schedule(view)
    return ExitCode.OK


@schedule_app.command("trigger")
@command
async def schedule_trigger(use_case: UseCaseOpt = DEFAULT_USE_CASE, yes: YesOpt = False) -> ExitCode:
    """Start one scheduled run now, under the schedule's current response."""
    from snapl_orchestrator import schedules  # noqa: PLC0415
    from snapl_orchestrator.models import DriftResponseMode  # noqa: PLC0415

    settings = CliSettings.from_env()

    async def _trigger(client: Any, log: Any):
        current = await schedules.describe_drift_schedule(client, use_case_id=use_case)
        if current.mode is DriftResponseMode.REMEDIATE:
            # A triggered remediate run may write, so it needs the same consent (FR-025).
            body = await _remediate_authorization(settings, use_case, current.interval)
            _confirm(f"Trigger a remediate run of {use_case} now?\n{body}", yes=yes, as_json=False)
        return await schedules.trigger_drift_schedule(client, use_case_id=use_case, audit_log=log, actor=_actor())

    mode = await _schedule_write(settings, use_case, _trigger)
    schedule_id = schedules.schedule_id_for(use_case)
    _stderr.print(
        f"triggered {schedule_id} ({mode.value}) — follow runs named {schedule_id}-<time>: snapl schedule show"
    )
    return ExitCode.OK


@schedule_app.command("delete")
@command
async def schedule_delete(use_case: UseCaseOpt = DEFAULT_USE_CASE, yes: YesOpt = False) -> ExitCode:
    """Remove a use case's drift schedule. A run in progress finishes."""
    from snapl_orchestrator import schedules  # noqa: PLC0415

    # Not destructive to devices, but it stops detection — worth a prompt.
    _confirm(
        f"Delete the drift schedule for {use_case}? Drift will no longer be found on its own.", yes=yes, as_json=False
    )
    settings = CliSettings.from_env()
    await _schedule_write(
        settings,
        use_case,
        lambda client, log: schedules.delete_drift_schedule(
            client, use_case_id=use_case, audit_log=log, actor=_actor()
        ),
    )
    _stderr.print(f"drift schedule for {use_case} deleted")
    return ExitCode.OK


def _schedule_status_row(view: Any, use_case: str) -> tuple[str, bool, str]:
    """Informational only: `status` reports health, and no schedule is a choice, not a fault (FR-026)."""
    from snapl_presentation.render import format_interval  # noqa: PLC0415

    if view is None:
        return ("drift schedule", True, "none — snapl schedule create")
    state = f'paused — "{view.note}"' if view.paused and view.note else ("paused" if view.paused else "active")
    return ("drift schedule", True, f"{use_case}: every {format_interval(view.interval)}, {view.mode.value}, {state}")


# ---------------------------------------------------------------------------
# snapl dashboard (spec 010, contracts/cli-dashboard.md)
# ---------------------------------------------------------------------------


def _is_loopback(host: str) -> bool:
    import ipaddress  # noqa: PLC0415

    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _port_in_use(host: str, port: int) -> bool:
    """Whether ``host:port`` is taken, checked the way uvicorn will bind it.

    Checked before serving so a taken port is one clear line, never a uvicorn
    traceback, and never a silent move to another port (#107).
    """
    import socket  # noqa: PLC0415

    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return True
    return False


def _serve_dashboard(settings: CliSettings, use_case: str, host: str, port: int) -> None:
    """Build the sources and serve until interrupted.

    No source is required at start-up (FR-007): each one reports its own
    failure in the views, and a missing audit file is never created.
    """
    import uvicorn  # noqa: PLC0415

    from snapl_presentation.dashboard.app import create_app  # noqa: PLC0415
    from snapl_presentation.dashboard.sources import (  # noqa: PLC0415
        AuditSource,
        SotSource,
        Sources,
        TemporalSource,
    )

    sources = Sources(
        audit=AuditSource(settings.audit_db),
        sot=SotSource(settings, use_case),
        temporal=TemporalSource(settings, use_case),
    )
    app_ = create_app(sources, settings, use_case)
    server = uvicorn.Server(uvicorn.Config(app_, host=host, port=port, log_level="warning", access_log=False))
    asyncio.run(server.serve())


@app.command()
def dashboard(
    use_case: UseCaseOpt = DEFAULT_USE_CASE,
    port: Annotated[int | None, typer.Option("--port", help="Port to serve on [env: DASHBOARD_PORT].")] = None,
    host: Annotated[str | None, typer.Option("--host", help="Address to bind [env: DASHBOARD_HOST].")] = None,
) -> None:
    """Serve a local, read-only dashboard of the fabric and the loop."""
    settings = CliSettings.from_env()
    host = host or settings.dashboard_host
    port = port if port is not None else settings.dashboard_port

    if not _is_loopback(host):
        _stderr.print(
            f"[yellow]warning:[/] the dashboard has no authentication; anyone who can reach {host}:{port} can read it.",
            highlight=False,
            soft_wrap=True,
        )
    if _port_in_use(host, port):
        _stderr.print(
            f"[red]error:[/] port {port} is in use. Set DASHBOARD_PORT or pass --port.",
            highlight=False,
            soft_wrap=True,
        )
        raise typer.Exit(int(ExitCode.ERROR))

    url_host = f"[{host}]" if ":" in host else host
    typer.echo(f"Dashboard for {use_case} at http://{url_host}:{port} (read-only, Ctrl-C to stop)")
    # Nothing runs server-side on the dashboard's behalf: stopping it is the
    # whole of what Ctrl-C means here, so it is a clean exit.
    with contextlib.suppress(KeyboardInterrupt):
        _serve_dashboard(settings, use_case, host, port)
    raise typer.Exit(int(ExitCode.OK))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
