"""The dashboard's own metrics, in Prometheus text format 0.0.4 (spec 012, research R8).

Two gauge families, one sample per source that has been read:

- ``snapl_dashboard_source_up{source}``: 1 if the last read answered, 0 otherwise;
- ``snapl_dashboard_source_fetch_duration_milliseconds{source}``: how long it took.

A source not read yet is left out rather than reported as 0. Label values come
from ``SourceName``, a fixed set of plain words, so nothing needs escaping.
"""

from __future__ import annotations

from dataclasses import dataclass

from snapl_presentation.dashboard.models import SourceName

__all__ = ["CONTENT_TYPE", "SourceOutcome", "render_exposition"]

CONTENT_TYPE = "text/plain; version=0.0.4"


@dataclass(frozen=True)
class SourceOutcome:
    """How the last read of one source went."""

    answered: bool
    duration_ms: float


_FAMILIES = (
    (
        "snapl_dashboard_source_up",
        "1 if the dashboard's last read of the source answered, 0 otherwise.",
        lambda outcome: "1" if outcome.answered else "0",
    ),
    (
        "snapl_dashboard_source_fetch_duration_milliseconds",
        "How long the dashboard's last read of the source took, in milliseconds.",
        lambda outcome: repr(round(outcome.duration_ms, 3)),
    ),
)


def render_exposition(outcomes: dict[SourceName, SourceOutcome]) -> str:
    lines: list[str] = []
    for name, help_text, value in _FAMILIES:
        lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
        lines += [
            f'{name}{{source="{source.value}"}} {value(outcomes[source])}'
            for source in SourceName
            if source in outcomes
        ]
    return "\n".join(lines) + "\n"
