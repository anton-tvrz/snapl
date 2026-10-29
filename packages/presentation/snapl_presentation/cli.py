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


async def _probe(host_port: str, timeout: float) -> str | None:
    """TCP-reachability probe. Returns a failure reason, or None if reachable."""
    host, _, port = host_port.rpartition(":")
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host or "localhost", int(port or 18033)),
            timeout=timeout,
        )
    except TimeoutError:
        return f"no response within {timeout:g}s"
    except (OSError, ValueError) as exc:
        return str(exc)
    writer.close()
    return None


async def _connect(settings: CliSettings):
    """Temporal client with the pydantic converter wired in (FR-008).

    Probes TCP first. ``Client.connect`` retries its initial handshake forever
    and takes no connect_timeout, and wrapping it in ``asyncio.wait_for`` does
    not help: the Rust core blocks the event loop, so the timer never fires and
    the CLI hangs with no output — the exact opposite of FR-014. A plain
    asyncio socket probe is cancellable, costs milliseconds, and turns "nothing
    is listening" into an immediate, specific message. The wait_for below still
    guards the case where the port accepts but the handshake stalls.
    """
    from snapl_orchestrator.worker.client import build_client  # noqa: PLC0415

    failure = await _probe(settings.temporal_host, settings.connect_timeout)
    if failure is not None:
        raise ConnectionCliError(
            subsystem="Temporal",
            address=settings.temporal_host,
            env_var="TEMPORAL_HOST",
            cause=failure,
        )

    try:
        return await asyncio.wait_for(
            build_client(target=settings.temporal_host, namespace=settings.temporal_namespace),
            timeout=settings.connect_timeout,
        )
    except (TimeoutError, Exception) as exc:
        cause = f"no response within {settings.connect_timeout:g}s" if isinstance(exc, TimeoutError) else str(exc)
        raise ConnectionCliError(
            subsystem="Temporal",
            address=settings.temporal_host,
            env_var="TEMPORAL_HOST",
            cause=cause,
        ) from exc


async def identify_sot(store, *, address: str):
    """Classify the Infrahub a store points at (#107)."""
    from snapl_intent.infrahub.identity import identify  # noqa: PLC0415

    return await identify(store.client, address=address)


async def count_pollers(client: Any, settings: CliSettings) -> int:
    """How many workers are polling our task queue."""
    from temporalio.api.enums.v1 import TaskQueueType  # noqa: PLC0415
    from temporalio.api.taskqueue.v1 import TaskQueue  # noqa: PLC0415
    from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest  # noqa: PLC0415

    request = DescribeTaskQueueRequest(
        namespace=settings.temporal_namespace,
        task_queue=TaskQueue(name=settings.task_queue),
        task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
    )
    response = await asyncio.wait_for(
        client.service_client.workflow_service.describe_task_queue(request),
        timeout=settings.connect_timeout,
    )
    return len(response.pollers)


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
    from snapl_orchestrator.exceptions import ScheduleNotFoundError  # noqa: PLC0415

    settings = CliSettings.from_env()
    client = await _connect(settings)
    await _warn_if_no_worker(client, settings)
    try:
        view = await schedules.describe_drift_schedule(client, use_case_id=use_case)
    except ScheduleNotFoundError:
        raise _no_schedule(use_case) from None

    build_renderer(as_json=as_json).schedule(view)
    return ExitCode.OK


def main() -> None:
    app()


if __name__ == "__main__":
    main()
