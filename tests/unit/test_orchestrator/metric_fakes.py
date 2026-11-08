"""Fake Temporal metric meters for the gNMI timing tests (spec 012)."""

from __future__ import annotations

from datetime import timedelta  # noqa: TC003
from typing import Any


class FakeHistogram:
    def __init__(self, name: str, description: str | None, unit: str | None) -> None:
        self.name = name
        self.description = description
        self.unit = unit
        self.samples: list[tuple[timedelta, dict[str, Any]]] = []

    def record(self, value: timedelta, additional_attributes: dict[str, Any] | None = None) -> None:
        self.samples.append((value, dict(additional_attributes or {})))


class FakeMeter:
    """The slice of temporalio.common.MetricMeter that record_gnmi_call uses."""

    def __init__(self) -> None:
        self.histograms: list[FakeHistogram] = []
        # What the SDK adds for the activity (namespace, task_queue,
        # activity_type). Kept apart: the real runtime merges them at export.
        self.runtime_attributes: dict[str, Any] = {}

    def with_additional_attributes(self, additional_attributes: dict[str, Any]) -> FakeMeter:
        self.runtime_attributes.update(additional_attributes)
        return self

    def create_histogram_timedelta(
        self, name: str, description: str | None = None, unit: str | None = None
    ) -> FakeHistogram:
        histogram = FakeHistogram(name, description, unit)
        self.histograms.append(histogram)
        return histogram

    def samples(self) -> list[tuple[str, str | None, timedelta, dict[str, Any]]]:
        return [(h.name, h.unit, value, attrs) for h in self.histograms for value, attrs in h.samples]


class ExplodingMeter:
    def with_additional_attributes(self, _attrs: dict[str, Any]) -> ExplodingMeter:
        return self

    def create_histogram_timedelta(self, *_args: object, **_kwargs: object) -> FakeHistogram:
        raise RuntimeError("meter is broken")
