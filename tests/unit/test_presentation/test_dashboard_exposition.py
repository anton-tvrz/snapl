"""The dashboard's own metrics in Prometheus text format (spec 012 T017, research R8)."""

from __future__ import annotations

import re

import pytest

from snapl_presentation.dashboard.exposition import SourceOutcome, render_exposition
from snapl_presentation.dashboard.models import SourceName

pytestmark = pytest.mark.unit

UP = "snapl_dashboard_source_up"
DURATION = "snapl_dashboard_source_fetch_duration_milliseconds"

# Text format 0.0.4: a sample is `name{label="value",...} number`.
_SAMPLE = re.compile(r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)\{source="(?P<source>[a-z_]+)"\} (?P<value>\S+)$')


def parse(text: str) -> tuple[dict[str, tuple[str, str]], dict[tuple[str, str], float]]:
    """A minimal parser: HELP/TYPE per family, and samples by (family, source)."""
    meta: dict[str, list[str]] = {}
    samples: dict[tuple[str, str], float] = {}
    assert text.endswith("\n")
    for line in text.splitlines():
        if line.startswith("# HELP "):
            name, _, help_text = line[len("# HELP ") :].partition(" ")
            assert help_text, line
            meta.setdefault(name, []).append("help")
        elif line.startswith("# TYPE "):
            name, _, kind = line[len("# TYPE ") :].partition(" ")
            meta.setdefault(name, []).append(kind)
        else:
            match = _SAMPLE.match(line)
            assert match, f"not a valid sample line: {line!r}"
            assert meta.get(match["name"]) == ["help", "gauge"], "HELP and TYPE come before the samples"
            samples[(match["name"], match["source"])] = float(match["value"])
    return {k: (v[0], v[1]) for k, v in meta.items()}, samples


def test_one_sample_per_source_in_each_family() -> None:
    outcomes = {
        SourceName.AUDIT: SourceOutcome(answered=True, duration_ms=12.5),
        SourceName.TEMPORAL: SourceOutcome(answered=False, duration_ms=1500.0),
    }

    meta, samples = parse(render_exposition(outcomes))

    assert meta == {UP: ("help", "gauge"), DURATION: ("help", "gauge")}
    assert samples == {
        (UP, "audit"): 1.0,
        (UP, "temporal"): 0.0,
        (DURATION, "audit"): 12.5,
        (DURATION, "temporal"): 1500.0,
    }


def test_a_source_never_fetched_is_omitted_not_zero() -> None:
    _, samples = parse(render_exposition({SourceName.CI: SourceOutcome(answered=True, duration_ms=3.0)}))

    assert {source for _, source in samples} == {"ci"}


def test_nothing_fetched_yet_is_still_valid_text() -> None:
    meta, samples = parse(render_exposition({}))

    assert set(meta) == {UP, DURATION}
    assert samples == {}


def test_sources_come_out_in_a_stable_order() -> None:
    outcomes = {name: SourceOutcome(answered=True, duration_ms=1.0) for name in reversed(SourceName)}

    text = render_exposition(outcomes)

    order = [m["source"] for line in text.splitlines() if (m := _SAMPLE.match(line)) and m["name"] == UP]
    assert order == [name.value for name in SourceName]
