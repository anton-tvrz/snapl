"""Dashboard view and envelope models (spec 010 data-model.md, T008)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from snapl_presentation.dashboard import models
from snapl_presentation.dashboard.models import (
    DeviceView,
    DriftState,
    Envelope,
    LabRun,
    LabRunStatus,
    RepeatedRepairFailure,
    ScheduleStatus,
    Series,
    SeriesName,
    SourceName,
    View,
)

pytestmark = pytest.mark.unit

_T = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


# --- View -------------------------------------------------------------------


def test_an_unavailable_view_needs_a_reason_and_a_remedy() -> None:
    with pytest.raises(ValidationError, match="reason and remedy"):
        View[int](source=SourceName.TEMPORAL, available=False, reason="refused")
    with pytest.raises(ValidationError, match="reason and remedy"):
        View[int](source=SourceName.TEMPORAL, available=False, remedy="uv run invoke dev.deps")

    view = View[int](source=SourceName.TEMPORAL, available=False, reason="refused", remedy="uv run invoke dev.deps")
    assert view.data is None


def test_a_stale_view_needs_its_data_and_when_it_was_fetched() -> None:
    """FR-031: cached data is labelled with its age, so a stale view without
    an age or without the data it is stale about is meaningless."""
    with pytest.raises(ValidationError, match="stale"):
        View[int](source=SourceName.CI, available=True, stale=True, fetched_at=_T)
    with pytest.raises(ValidationError, match="stale"):
        View[int](source=SourceName.CI, available=True, stale=True, data=3)

    assert View[int](source=SourceName.CI, available=True, stale=True, data=3, fetched_at=_T).stale


def test_view_times_must_carry_an_offset() -> None:
    with pytest.raises(ValidationError):
        View[int](source=SourceName.AUDIT, available=True, data=1, fetched_at=datetime(2026, 11, 7))  # noqa: DTZ001


def test_view_times_serialise_in_utc_with_an_offset() -> None:
    cet = timezone(timedelta(hours=1))
    view = View[int](source=SourceName.AUDIT, available=True, data=1, fetched_at=datetime(2026, 11, 7, 13, tzinfo=cet))

    assert view.model_dump(mode="json")["fetched_at"] in {"2026-11-07T12:00:00Z", "2026-11-07T12:00:00+00:00"}


# --- Envelope ----------------------------------------------------------------


def test_envelope_carries_contract_version_one() -> None:
    envelope = Envelope(use_case="dcfabric", generated_at=_T)

    assert envelope.contract_version == "1"
    with pytest.raises(ValidationError):
        Envelope(contract_version="2", use_case="dcfabric", generated_at=_T)  # type: ignore[arg-type]


def test_every_model_is_frozen_and_forbids_extra_fields() -> None:
    classes = [
        obj
        for obj in vars(models).values()
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj.__module__ == models.__name__
    ]
    assert classes, "no models found"
    for cls in classes:
        assert cls.model_config.get("frozen") is True, cls.__name__
        assert cls.model_config.get("extra") == "forbid", cls.__name__


# --- DeviceView ----------------------------------------------------------------


def _device(state: DriftState, **overrides) -> DeviceView:
    fields: dict = {
        "device_id": uuid4(),
        "name": "leaf-01",
        "role": "leaf",
        "in_intent": True,
        "drift_state": state,
        "last_scanned_at": None if state is DriftState.NEVER_SCANNED else _T,
    }
    fields.update(overrides)
    return DeviceView(**fields)


@pytest.mark.parametrize(
    ("state", "paths"),
    [
        (DriftState.MISSING, {"missing_paths": ["/a"]}),
        (DriftState.UNDESIRED, {"undesired_paths": ["/b"]}),
        (DriftState.MISSING_AND_UNDESIRED, {"missing_paths": ["/a"], "undesired_paths": ["/b"]}),
        (DriftState.DRIFTED, {"unclassified_paths": ["/a", "/b"]}),
    ],
)
def test_each_drift_state_accepts_its_own_path_lists(state: DriftState, paths: dict) -> None:
    assert _device(state, **paths).drift_state is state


@pytest.mark.parametrize(
    ("state", "paths"),
    [
        (DriftState.IN_SYNC, {"missing_paths": ["/a"]}),
        (DriftState.MISSING, {"undesired_paths": ["/b"]}),
        (DriftState.UNDESIRED, {"missing_paths": ["/a"]}),
        (DriftState.MISSING_AND_UNDESIRED, {"unclassified_paths": ["/a"]}),
        (DriftState.DRIFTED, {"missing_paths": ["/a"]}),
    ],
)
def test_path_lists_are_populated_only_for_their_states(state: DriftState, paths: dict) -> None:
    with pytest.raises(ValidationError, match="paths"):
        _device(state, **paths)


def test_an_errored_device_needs_its_error() -> None:
    with pytest.raises(ValidationError, match="error"):
        _device(DriftState.ERROR)

    assert _device(DriftState.ERROR, error="dial failed").error == "dial failed"


def test_last_scanned_at_is_empty_exactly_when_never_scanned() -> None:
    with pytest.raises(ValidationError, match="never_scanned"):
        _device(DriftState.NEVER_SCANNED, last_scanned_at=_T)
    with pytest.raises(ValidationError, match="never_scanned"):
        _device(DriftState.IN_SYNC, last_scanned_at=None)


# --- Schedule ----------------------------------------------------------------


def test_schedule_status_holds_a_schedule_exactly_when_one_exists() -> None:
    with pytest.raises(ValidationError, match="schedule"):
        ScheduleStatus(exists=True)

    absent = ScheduleStatus(exists=False, suggested_command="snapl schedule create --use-case dcfabric")
    assert absent.schedule is None


def test_a_repeated_repair_failure_is_three_or_more() -> None:
    with pytest.raises(ValidationError):
        RepeatedRepairFailure(
            device_id=uuid4(),
            name="leaf-01",
            consecutive_failures=2,
            last_failed_at=_T,
            suggested_command="snapl schedule mode report --use-case dcfabric",
        )


# --- History -----------------------------------------------------------------


def test_series_rejects_an_empty_or_inverted_range() -> None:
    with pytest.raises(ValidationError, match="start"):
        Series(name=SeriesName.DRIFTED_DEVICES, start=_T, end=_T, bucket=None, points=[], markers=[])
    with pytest.raises(ValidationError, match="start"):
        Series(
            name=SeriesName.DRIFTED_DEVICES,
            start=_T,
            end=_T - timedelta(hours=1),
            bucket=None,
            points=[],
            markers=[],
        )


# --- Lab runs ----------------------------------------------------------------


def test_an_unreadable_lab_run_says_what_is_wrong_with_it() -> None:
    with pytest.raises(ValidationError, match="problem"):
        LabRun(id="20261107T101500Z-e2e", suite="e2e", status=LabRunStatus.UNREADABLE)

    run = LabRun(id="20261107T101500Z-e2e", suite="e2e", status=LabRunStatus.UNREADABLE, problem="format 2")
    assert run.problem == "format 2"
