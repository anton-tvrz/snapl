"""Rendering of Orchestrator results for operators and for machines.

Spec 006 keeps this separate from command logic so a command never branches on
output format beyond picking a renderer — and so an HTTP surface can reuse the
JSON side later without restructuring.

Rich handles the TTY question on its own: when stdout is not a terminal it
drops colour and animation, which satisfies FR-013 without a flag.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from snapl_observability.models import DriftStatus
from snapl_presentation.exit_codes import ExitCode

if TYPE_CHECKING:
    from snapl_orchestrator.models import (
        AuditEvent,
        DriftScanResult,
        ReconcileResult,
        WorkflowResult,
    )

_STATUS_STYLE = {
    DriftStatus.CLEAN: "green",
    DriftStatus.DRIFTED: "yellow",
    DriftStatus.ERROR: "red",
}


def _seconds(result: Any) -> float:
    return (result.ended_at - result.started_at).total_seconds()


def _esc(value: Any) -> str:
    """Escape data before it meets Rich's markup parser.

    gNMI paths are full of square brackets — ``/interface[name=ethernet-1/1]``
    reads as a style tag and Rich silently deletes it, mangling the single most
    important line the CLI prints. Everything interpolated from a device, a
    workflow or the SoT goes through here; only literal styling we write
    ourselves is left unescaped.
    """
    return escape(str(value))


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
        # drift" when part of the fabric was never actually evaluated.
        if result.errored:
            return ExitCode.ERROR
        return ExitCode.DRIFT if result.drifted else ExitCode.OK

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

    def reconcile(self, result: ReconcileResult) -> None:
        raise NotImplementedError

    def audit(self, events: list[AuditEvent]) -> None:
        raise NotImplementedError

    def status(self, checks: list[tuple[str, bool, str]], running: list[Any]) -> None:
        raise NotImplementedError


class HumanRenderer(Renderer):
    """Rich output for a person at a terminal."""

    def deploy(self, result: WorkflowResult) -> None:
        verdict = "[green]succeeded[/]" if result.success else f"[red]{_esc(result.reason.value)}[/]"
        self._console.print(f"deploy {_esc(result.target_id)} — {verdict} in {_seconds(result):.1f}s")
        if result.detail:
            self._console.print(f"  {_esc(result.detail)}")
        for item in result.drift_items:
            self._console.print(f"  {_esc(item.path)} desired={_esc(item.desired)!r} actual={_esc(item.actual)!r}")

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
                    table.add_row(f"  {_esc(item.path)}", _esc(item.desired), _esc(item.actual))
                self._console.print(table)

    def reconcile(self, result: ReconcileResult) -> None:
        self._console.print(
            f"{result.succeeded}/{result.total} succeeded, "
            f"[red]{result.failed} failed[/], {result.skipped} skipped ({_seconds(result):.1f}s)"
        )
        for device_id, outcome in result.device_results.items():
            mark = "[green]ok[/]" if outcome.success else f"[red]{_esc(outcome.reason.value)}[/]"
            self._console.print(f"  {_esc(device_id)} {mark}")

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
        for event in events:
            table.add_row(
                event.timestamp.isoformat(timespec="seconds"),
                _esc(event.workflow_id),
                _esc(event.event_type.value),
                _esc(event.activity_name or ""),
                _esc(event.outcome or ""),
            )
        self._console.print(table)

    def status(self, checks: list[tuple[str, bool, str]], running: list[Any]) -> None:
        for name, ok, detail in checks:
            mark = "[green]ok[/]" if ok else "[red]FAIL[/]"
            self._console.print(f"  {mark} {_esc(name)}{f' — {_esc(detail)}' if detail else ''}")
        if running:
            self._console.print(f"\n{len(running)} workflow(s) running:")
            for info in running:
                self._console.print(f"  {_esc(info.workflow_id)} ({_esc(info.workflow_type)})")
        else:
            self._console.print("\nno workflows running")


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

    def reconcile(self, result: ReconcileResult) -> None:
        self._emit(_dump(result))

    def audit(self, events: list[AuditEvent]) -> None:
        self._emit([_dump(event) for event in events])

    def status(self, checks: list[tuple[str, bool, str]], running: list[Any]) -> None:
        self._emit(
            {
                "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
                "running_workflows": [
                    {"workflow_id": i.workflow_id, "workflow_type": i.workflow_type} for i in running
                ],
            }
        )


def build_renderer(*, as_json: bool, console: Console | None = None) -> Renderer:
    return JsonRenderer(console) if as_json else HumanRenderer(console)
