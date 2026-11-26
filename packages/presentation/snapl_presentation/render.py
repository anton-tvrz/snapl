"""Rendering of Orchestrator results for operators and for machines.

Spec 006 keeps this separate from command logic so a command never branches on
output format beyond picking a renderer — and so an HTTP surface can reuse the
JSON side later without restructuring.

Rich handles the TTY question on its own: when stdout is not a terminal it
drops colour and animation, which satisfies FR-013 without a flag.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from snapl_observability.models import DriftStatus
from snapl_orchestrator.models import WorkflowReason
from snapl_presentation.exit_codes import ExitCode
from snapl_presentation.settings import DEFAULT_USE_CASE

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime, timedelta
    from uuid import UUID

    from snapl_observability.models import RemovalPlan
    from snapl_orchestrator.models import (
        AuditEvent,
        DriftScanResult,
        DriftScheduleView,
        ReconcileResult,
        RemovalResult,
        RunSummary,
        ScheduledRunInfo,
        WorkflowResult,
    )
    from snapl_presentation.settings import TemporalTarget

_STATUS_STYLE = {
    DriftStatus.CLEAN: "green",
    DriftStatus.DRIFTED: "yellow",
    DriftStatus.ERROR: "red",
}


def _seconds(result: Any) -> float:
    return float((result.ended_at - result.started_at).total_seconds())


def _esc(value: Any) -> str:
    """Escape data before it meets Rich's markup parser.

    gNMI paths are full of square brackets — ``/interface[name=ethernet-1/1]``
    reads as a style tag and Rich silently deletes it, mangling the single most
    important line the CLI prints. Everything interpolated from a device, a
    workflow or the SoT goes through here; only literal styling we write
    ourselves is left unescaped.
    """
    return escape(str(value))


def _desired_cell(item: Any) -> str:
    """What to show in the 'desired' column for one drift item.

    An undesired entity has no desired value, and rendering it as a bare
    ``None`` makes it read identically to intent wanting a field the device
    has not set — the opposite problem, needing the opposite fix (#54). Name
    the direction instead.
    """
    if getattr(item, "undesired", False):
        return "[dim]not in intent[/]"
    return _esc(item.desired)


def format_interval(interval: timedelta) -> str:
    """``5m``, ``90s``, ``1h`` — the same shape ``--every`` accepts."""
    seconds = int(interval.total_seconds())
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _clock(at: datetime) -> str:
    return at.strftime("%H:%M:%SZ")


def _wrote(summary: RunSummary) -> str:
    if not summary.wrote:
        return "wrote: no"
    devices = summary.succeeded + summary.failed
    return f"wrote: {devices} device{'s' if devices != 1 else ''} ({summary.succeeded} ok, {summary.failed} failed)"


def _run_line(run: ScheduledRunInfo) -> str:
    head = f"{_clock(run.started_at)} {_esc(run.status):<9}"
    if run.summary is None:
        return f"{head}  {_esc(run.workflow_id)}"
    s = run.summary
    return f"{head}  {s.total} devices: {s.clean} clean, {s.drifted} drifted, {s.errored} errored   {_wrote(s)}"


@dataclass(frozen=True)
class FabricHealthSummary:
    """The fabric's recorded health, by device name, for `snapl status` (spec 016).

    A device with nothing recorded is ``unknown``. ``error`` says why the
    audit trail could not be read; then the lists are empty.
    """

    healthy: list[str]
    unhealthy: list[str]
    unknown: list[str]
    recorded: bool = True
    error: str | None = None

    def line(self) -> str:
        if self.error:
            return f"could not read: {self.error}"
        if not self.recorded:
            return "no telemetry recorded"
        unhealthy = f" ({', '.join(self.unhealthy)})" if self.unhealthy else ""
        return f"{len(self.healthy)} healthy, {len(self.unhealthy)} unhealthy{unhealthy}, {len(self.unknown)} unknown"

    def as_json(self) -> dict[str, list[str]] | None:
        if self.error or not self.recorded:
            return None
        return {"healthy": self.healthy, "unhealthy": self.unhealthy, "unknown": self.unknown}


def _schedule_outcome(event: AuditEvent) -> str:
    payload = event.payload
    change = str(payload.get("change", ""))
    if "from" in payload and "to" in payload:
        return f"{change} {payload['from']}→{payload['to']}"
    if "from_seconds" in payload and "to_seconds" in payload:
        return f"{change} {payload['from_seconds']}s→{payload['to_seconds']}s"
    return change


def _response_outcome(event: AuditEvent) -> str:
    return str(event.payload.get("decision") or event.payload.get("mode") or event.outcome or "")


def _health_outcome(event: AuditEvent) -> str:
    # The change, then each reason on its own line (spec 016).
    payload = event.payload
    reasons = [str(reason.get("text", "")) for reason in payload.get("reasons") or []]
    return "\n".join([f"{payload.get('before')}→{payload.get('after')}", *reasons])


_OUTCOME_BY_EVENT = {
    "schedule_changed": _schedule_outcome,
    "drift_response": _response_outcome,
    "health_changed": _health_outcome,
}


def _audit_outcome(event: AuditEvent) -> str:
    """What the outcome column says. Schedule events carry their meaning in the
    payload, so a bare event type would hide which policy changed (spec 009)."""
    if event.workflow_type == "RemoveConfig" and event.event_type.value == "workflow_terminated":
        return _removal_outcome(event)
    by_type = _OUTCOME_BY_EVENT.get(event.event_type.value)
    return by_type(event) if by_type is not None else event.outcome or ""


def _removal_outcome(event: AuditEvent) -> str:
    """A removal's end in one cell: what it deleted and whether the scan after agreed (spec 013)."""
    payload = event.payload
    deleted = [str(path) for path in payload.get("deleted_paths") or []]
    reason = event.reason.value if event.reason is not None else (event.outcome or "")
    if not deleted:
        return f"{reason.replace('_', ' ')}; nothing removed"
    removed = f"removed {len(deleted)}: {', '.join(deleted)}"
    if reason == "succeeded":
        return f"{removed}; verified"
    remaining = [str(path) for path in payload.get("remaining") or []]
    lost = [str(path) for path in payload.get("new_forward") or []]
    notes = [f"still there: {', '.join(remaining)}"] if remaining else []
    notes += [f"newly missing: {', '.join(lost)}"] if lost else []
    if reason == "verification_failed":
        return f"{removed}; not verified, {'; '.join(notes)}"
    return f"{removed}; {reason.replace('_', ' ')}"


_REFUSAL_TEXT = {
    "protected": "protected",
    "not_removable": "not removable in this version",
}


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _relative(path: str, entity: str) -> str:
    """``…/description`` for a drift path under ``entity``; the whole path otherwise."""
    return f"…{path[len(entity) :]}" if path.startswith(f"{entity}/") else path


def remove_hint(device: str, use_case: str) -> str:
    """The line that points an operator at ``snapl remove`` for ``device`` (spec 008 FR-022).

    ``snapl remove`` reads the default use case unless told otherwise, so any
    other use case is named, or the command would not find the device.
    """
    target = device if use_case == DEFAULT_USE_CASE else f"{device} --use-case {use_case}"
    return f"To remove what intent never asked for: snapl remove {target}"


def _leaves_undesired(outcome: WorkflowResult) -> bool:
    """Whether a deploy's verification left config on the device that intent never asked for."""
    return bool(outcome.undesired_paths) or any(item.undesired for item in outcome.drift_items)


def _dump(model: Any) -> Any:
    """JSON-ready form of a pydantic model, with enums and UUIDs stringified."""
    return json.loads(model.model_dump_json())


class Renderer:
    """Base renderer. Subclasses own the format; the exit-code mapping is shared.

    Exit codes live here rather than in the commands because the mapping is a
    property of the *result*, not of how it is printed — and stating it once is
    what makes FR-011 hold uniformly.
    """

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()

    # -- exit-code mapping (identical for both formats) ---------------------

    @staticmethod
    def deploy_code(result: WorkflowResult) -> ExitCode:
        return ExitCode.OK if result.success else ExitCode.ERROR

    @staticmethod
    def scan_code(result: DriftScanResult) -> ExitCode:
        # Errored devices mean the scan could not answer the question for them:
        # operational failure outranks drift, so the caller is not told "just
        # drift" when part of the fabric was never actually evaluated. A
        # cancelled scan did not evaluate all of it either, whatever the part
        # it finished shows (#36).
        if result.errored or result.cancelled:
            return ExitCode.ERROR
        return ExitCode.DRIFT if result.drifted else ExitCode.OK

    @staticmethod
    def removal_plan_code(plan: RemovalPlan) -> ExitCode:
        # Something to remove is drift found (spec 006 FR-011); refusals alone are not.
        return ExitCode.OK if plan.is_empty else ExitCode.DRIFT

    @staticmethod
    def removal_code(result: RemovalResult | None) -> ExitCode:
        # None: the plan had nothing to remove, so no removal started.
        return ExitCode.OK if result is None or result.success else ExitCode.ERROR

    @staticmethod
    def reconcile_code(result: ReconcileResult) -> ExitCode:
        # Skips are not failures — a device already being deployed, or absent
        # from the SoT, is a reported outcome, not a broken run (#66/#35).
        return ExitCode.ERROR if result.failed else ExitCode.OK

    # -- format-specific output --------------------------------------------

    def deploy(self, result: WorkflowResult) -> None:
        raise NotImplementedError

    def scan(self, result: DriftScanResult) -> None:
        raise NotImplementedError

    def reconcile(
        self, result: ReconcileResult, *, names: Mapping[UUID, str] | None = None, use_case: str = DEFAULT_USE_CASE
    ) -> None:
        raise NotImplementedError

    def audit(self, events: list[AuditEvent]) -> None:
        raise NotImplementedError

    def status(
        self, checks: list[tuple[str, bool, str]], running: list[Any], health: FabricHealthSummary | None = None
    ) -> None:
        raise NotImplementedError

    def schedule(self, view: DriftScheduleView, *, temporal: TemporalTarget) -> None:
        raise NotImplementedError

    def removal_plan(self, plan: RemovalPlan, *, applying: bool = False) -> None:
        raise NotImplementedError

    def removal(self, plan: RemovalPlan, result: RemovalResult | None) -> None:
        raise NotImplementedError


class HumanRenderer(Renderer):
    """Rich output for a person at a terminal."""

    def deploy(self, result: WorkflowResult) -> None:
        verdict = "[green]succeeded[/]" if result.success else f"[red]{_esc(result.reason.value)}[/]"
        self._console.print(f"deploy {_esc(result.target_id)} — {verdict} in {_seconds(result):.1f}s")
        if result.detail:
            self._console.print(f"  {_esc(result.detail)}")
        for item in result.drift_items:
            self._console.print(f"  {_esc(item.path)} desired={_desired_cell(item)} actual={_esc(item.actual)!r}")

    def scan(self, result: DriftScanResult) -> None:
        self._console.print(
            f"{result.total} devices: [green]{result.clean} clean[/], "
            f"[yellow]{result.drifted} drifted[/], [red]{result.errored} errored[/] "
            f"({_seconds(result):.1f}s)"
        )
        for report in sorted(result.reports.values(), key=lambda r: r.device_name):
            if report.status is DriftStatus.CLEAN:
                continue
            style = _STATUS_STYLE[report.status]
            self._console.print(f"\n[{style}]{_esc(report.device_name)}[/] — {_esc(report.status.value)}")
            if report.error:
                self._console.print(f"  {_esc(report.error)}")
            if report.items:
                table = Table(box=None, pad_edge=False, show_edge=False)
                table.add_column("path")
                table.add_column("desired")
                table.add_column("actual")
                for item in report.items:
                    table.add_row(f"  {_esc(item.path)}", _desired_cell(item), _esc(item.actual))
                self._console.print(table)
            if any(item.undesired for item in report.items):
                # soft_wrap: a command to copy must not break across lines.
                self._console.print(f"  {_esc(remove_hint(report.device_name, result.use_case_id))}", soft_wrap=True)

    def reconcile(
        self, result: ReconcileResult, *, names: Mapping[UUID, str] | None = None, use_case: str = DEFAULT_USE_CASE
    ) -> None:
        self._console.print(
            f"{result.succeeded}/{result.total} succeeded, "
            f"[red]{result.failed} failed[/], {result.skipped} skipped ({_seconds(result):.1f}s)"
        )
        for device_id, outcome in result.device_results.items():
            mark = "[green]ok[/]" if outcome.success else f"[red]{_esc(outcome.reason.value)}[/]"
            self._console.print(f"  {_esc(device_id)} {mark}")
            if _leaves_undesired(outcome):
                name = (names or {}).get(device_id, str(device_id))
                self._console.print(f"    {_esc(remove_hint(name, use_case))}", soft_wrap=True)

    def audit(self, events: list[AuditEvent]) -> None:
        if not events:
            self._console.print("no audit events matched")
            return
        table = Table(box=None)
        table.add_column("timestamp")
        table.add_column("workflow")
        table.add_column("event")
        table.add_column("activity")
        table.add_column("outcome")
        table.add_column("actor")
        # A removal's end row names who confirmed it, which only its start event records (spec 013).
        started_by = {e.workflow_id: e.actor for e in events if e.event_type.value == "workflow_started" and e.actor}
        for event in events:
            actor = event.actor
            if actor is None and event.workflow_type == "RemoveConfig":
                actor = started_by.get(event.workflow_id)
            table.add_row(
                event.timestamp.isoformat(timespec="seconds"),
                _esc(event.workflow_id),
                _esc(event.event_type.value),
                _esc(event.activity_name or ""),
                _esc(_audit_outcome(event)),
                _esc(actor or ""),
            )
        self._console.print(table)

    def status(
        self, checks: list[tuple[str, bool, str]], running: list[Any], health: FabricHealthSummary | None = None
    ) -> None:
        for name, ok, detail in checks:
            mark = "[green]ok[/]" if ok else "[red]FAIL[/]"
            self._console.print(f"  {mark} {_esc(name)}{f' — {_esc(detail)}' if detail else ''}")
        if health is not None:
            # The network's state, not snapl's: never marked ok or FAIL.
            self._console.print(f"\nfabric health   {_esc(health.line())}")
        if running:
            self._console.print(f"\n{len(running)} workflow(s) running:")
            for info in running:
                self._console.print(f"  {_esc(info.workflow_id)} ({_esc(info.workflow_type)})")
        else:
            self._console.print("\nno workflows running")

    def schedule(self, view: DriftScheduleView, *, temporal: TemporalTarget) -> None:
        mode = view.mode.value
        # Remediate means unattended writes; it should never read as routine.
        response = f"[yellow]{mode}[/]" if mode == "remediate" else mode
        # The note shows whether paused or not: a stand-down leaves the schedule
        # active, with why on its note (#389).
        state = "paused" if view.paused else "active"
        if view.note:
            state = f'{state} — "{_esc(view.note)}"'
        rows = [
            ("drift schedule", _esc(view.schedule_id)),
            ("use case", _esc(view.use_case_id)),
            ("temporal", _esc(str(temporal))),
            ("every", format_interval(view.interval)),
            ("response", response),
            ("state", state),
            ("next runs", "  ".join(_clock(at) for at in view.next_runs) or "none"),
        ]
        for label, value in rows:
            self._console.print(f"{label:<17} {value}")
        if view.recent_runs:
            for index, run in enumerate(view.recent_runs):  # oldest first, as the contract shows
                label = "recent runs" if index == 0 else ""
                # soft_wrap: a run line is one record; hard-wrapping it at the
                # console width splits its summary from its "wrote" verdict.
                self._console.print(f"{label:<17} {_run_line(run)}", soft_wrap=True)
        else:
            self._console.print(f"{'recent runs':<17} none yet")
        self._console.print(f"{'skipped (overlap)':<17} {view.skipped_overlap}")

    def removal_plan(self, plan: RemovalPlan, *, applying: bool = False) -> None:
        """The preview: each delete and what it answers, the refusals, and what reconcile heals instead.

        Paths print whole, never wrapped: an operator may copy one to act on by hand.
        ``applying``: the preview before ``--apply``'s prompt, which needs no hint to apply.
        """
        out = functools.partial(self._console.print, soft_wrap=True)
        device = _esc(plan.device_name)
        if plan.is_empty:
            out(f"{device}: nothing to remove")
        else:
            at = plan.scanned_at.astimezone(UTC).strftime("%H:%M:%SZ")
            out(f"{device}: {_plural(len(plan.deletes), 'delete')} planned from a scan at {at}\n")
            for delete in plan.deletes:
                out(f"  delete {_esc(delete.path)}")
                if delete.answers != [delete.path]:
                    answers = ", ".join(_relative(path, delete.path) for path in delete.answers)
                    out(f"         answers {_esc(answers)}")
                if delete.entity_kind == "bgp_session":
                    out("         removing a neighbour tears its session down; its peer group stays configured")
        if plan.refused:
            out("")
            for refusal in plan.refused:
                reason = _REFUSAL_TEXT.get(refusal.reason.value, refusal.reason.value)
                out(f"  refused {_esc(refusal.entity_path)} — {reason}")
        if plan.forward_drift:
            count = len(plan.forward_drift)
            verb = "is missing or differs" if count == 1 else "are missing or differ"
            out(f"\n  {_plural(count, 'path')} {verb} from intent: snapl reconcile {device} heals those, not this.")
        if not plan.is_empty:
            out("\nNothing was written." + ("" if applying else f" To remove: snapl remove {device} --apply"))

    def removal(self, plan: RemovalPlan, result: RemovalResult | None) -> None:
        """What a confirmed removal did (contracts/cli-remove.md, "Apply"). The preview is already shown."""
        if result is None:
            return
        out = functools.partial(self._console.print, soft_wrap=True)
        device = _esc(result.device_name or plan.device_name)
        out(_removal_headline(result, device))
        if result.reason == WorkflowReason.PLAN_CHANGED:
            if result.plan is not None:
                out("")
                self.removal_plan(result.plan)
            return
        for line in _removal_details(result):
            out(f"  {line}")


def _removal_headline(result: RemovalResult, device: str) -> str:
    count = len(result.deleted)
    entries = f"{count} {'entry' if count == 1 else 'entries'}"
    written = bool(result.deleted)
    headlines = {
        WorkflowReason.SUCCEEDED: f"removed {entries} from {device}; the verifying scan finds none of them",
        WorkflowReason.PLAN_CHANGED: f"{device} changed since the preview; nothing was removed",
        WorkflowReason.DEPLOY_IN_FLIGHT: (
            f"a deploy to {device} is running; nothing was removed. Run this again when it ends."
        ),
        WorkflowReason.REMOVE_FAILED: f"{device} rejected the removal; nothing was removed",
        WorkflowReason.VERIFICATION_FAILED: f"deleted {entries} from {device}, but the verifying scan disagrees",
        WorkflowReason.COLLECT_FAILED: (
            f"deleted {entries}; the verifying scan could not read {device}"
            if written
            else f"could not read {device}; nothing was removed"
        ),
        WorkflowReason.CANCELLED: (
            f"the removal of {device} was cancelled after deleting {entries}"
            if written
            else f"the removal of {device} was cancelled; nothing was removed"
        ),
    }
    default = f"{device}: {_esc(result.reason.value.replace('_', ' '))}: nothing was removed"
    return headlines.get(result.reason, default)


def _removal_details(result: RemovalResult) -> list[str]:
    """What was deleted, what the verifying scan found, the cause, and what to do next."""
    lines = [f"deleted {_esc(path)}" for path in result.deleted]
    verification = result.verification
    if verification is not None:
        lines += [f"still there: {_esc(path)}" for path in verification.remaining]
        lines += [f"newly missing or different from intent: {_esc(path)}" for path in verification.new_forward]
        lines += [f"still there, refused: {_esc(path)}" for path in verification.still_refused]
    if result.detail and result.reason != WorkflowReason.CANCELLED:
        lines.append(_esc(result.detail))
    if result.reason == WorkflowReason.COLLECT_FAILED and result.deleted:
        lines.append("Run snapl scan to see what the device holds now.")
    return lines


class JsonRenderer(Renderer):
    """Machine output. Only JSON reaches stdout (FR-010)."""

    def _emit(self, payload: Any) -> None:
        # print_json would re-style; a plain write keeps stdout byte-exact.
        self._console.print_json(json.dumps(payload, default=str))

    def deploy(self, result: WorkflowResult) -> None:
        self._emit(_dump(result))

    def scan(self, result: DriftScanResult) -> None:
        payload = _dump(result)
        payload["exit_code"] = int(self.scan_code(result))
        self._emit(payload)

    def reconcile(
        self, result: ReconcileResult, *, names: Mapping[UUID, str] | None = None, use_case: str = DEFAULT_USE_CASE
    ) -> None:
        # The remove hint is human output only (spec 008 FR-022): the result already carries the paths.
        self._emit(_dump(result))

    def audit(self, events: list[AuditEvent]) -> None:
        self._emit([_dump(event) for event in events])

    def status(
        self, checks: list[tuple[str, bool, str]], running: list[Any], health: FabricHealthSummary | None = None
    ) -> None:
        self._emit(
            {
                "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
                "running_workflows": [
                    {"workflow_id": i.workflow_id, "workflow_type": i.workflow_type} for i in running
                ],
                "health": health.as_json() if health is not None else None,
            }
        )

    def schedule(self, view: DriftScheduleView, *, temporal: TemporalTarget) -> None:
        self._emit(
            {**view.model_dump(mode="json"), "temporal": {"host": temporal.host, "namespace": temporal.namespace}}
        )

    def removal_plan(self, plan: RemovalPlan, *, applying: bool = False) -> None:
        # While applying, the plan goes out with the result as one document.
        if not applying:
            self._emit({"plan": _dump(plan)})

    def removal(self, plan: RemovalPlan, result: RemovalResult | None) -> None:
        self._emit({"plan": _dump(plan), "result": _dump(result) if result is not None else None})


def build_renderer(*, as_json: bool, console: Console | None = None) -> Renderer:
    return JsonRenderer(console) if as_json else HumanRenderer(console)
