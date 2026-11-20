"""A history archive opened read-only, and the sources it gives (spec 011 research R13).

``snapl dashboard --archive <file>`` serves the same API from a tarball that
``invoke history.archive`` wrote. The archived audit trail is read by the
normal audit source, so lanes, ``at``, runs and steps work over the whole
trail. Everything else is what was exported, as it was exported, or says it is
not in the archive. Nothing here opens a network connection or runs git.
"""

from __future__ import annotations

import asyncio
import json
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel

from snapl_presentation.dashboard import pages
from snapl_presentation.dashboard.lab_runs import read_lab_evidence
from snapl_presentation.dashboard.models import (
    NETWORK_VIEWS,
    ArchiveInfo,
    ArchivePart,
    CiStatus,
    FabricNetwork,
    LabEvidence,
    LinksResponse,
    MetricsData,
    MetricSeries,
    MetricsViewName,
    MetricsWindow,
    NetworkResponse,
    ScheduleStatus,
    SourceName,
    Topology,
    TopologyResponse,
    View,
    WorkerStatus,
)
from snapl_presentation.dashboard.projections import IntentDevice
from snapl_presentation.dashboard.sources import AuditSource, Sources, SourceUnavailable
from snapl_presentation.settings import CliSettings

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from fastapi import FastAPI

    from snapl_presentation.dashboard.prometheus import NetworkSample, RangePlan

FORMAT = "snapl-history/1"
NOT_IN_ARCHIVE = "not in this archive"
# Every unavailable view carries a remedy (contracts/ui-api.md): this is how to see it live.
ARCHIVE_REMEDY = "uv run snapl dashboard"
NO_GIT_HISTORY = "an archive has no git history"
PREDATES = "this archive predates the topology export (spec 011): devices are listed from the audit trail"
# Archive mode never reads the Prometheus snapshot (R13 rejects starting
# Prometheus on it), and it is most of a real archive's size.
_SKIPPED = ("prometheus/",)

# Spec 016 (#351). Over 7 days the views are at a 42 min step, so a fault of
# minutes falls between two points. The archive also holds the six network
# views at 15 s around each health episode and over its last hour, newest
# first, up to DETAIL_MAX in all. On the lab, five windows (2 h 10 min) were
# 0.88 MB before compression, 0.04 MB after (2026-11-20).
DETAIL_DIR = "metrics-detail"
DETAIL_STEP_S = 15
DETAIL_PAD = timedelta(minutes=5)
DETAIL_TAIL = timedelta(hours=1)
DETAIL_MAX = timedelta(hours=6)
EXPORT_SPAN = timedelta(days=7)

type Window = tuple[datetime, datetime]


def _floor(moment: datetime) -> datetime:
    """To the detail step, as ``plan_range`` floors a range, so a window is asked and answered alike."""
    seconds = int(moment.timestamp())
    return datetime.fromtimestamp(seconds - seconds % DETAIL_STEP_S, UTC)


def episodes_in(exported: dict[str, Any] | None) -> list[tuple[datetime, datetime | None]]:
    """Each health episode in an exported ``/health`` answer, oldest first; none when it wasn't exported."""
    data = ((exported or {}).get("health") or {}).get("data") or {}
    found = [
        (
            datetime.fromisoformat(episode["opened_at"]),
            datetime.fromisoformat(episode["closed_at"]) if episode.get("closed_at") else None,
        )
        for device in data.get("devices") or []
        for episode in device.get("episodes") or []
    ]
    return sorted(found, key=lambda episode: episode[0])


def detail_windows(episodes: Iterable[tuple[datetime, datetime | None]], created_at: datetime) -> list[Window]:
    """The windows an archive made at ``created_at`` exports at 15 s, oldest first.

    Each episode padded by ``DETAIL_PAD`` (an open one runs to ``created_at``),
    and the last hour; clipped to the export's 7 days, floored to the step,
    merged where they meet, and the newest kept up to ``DETAIL_MAX`` in all.
    """
    limit = created_at - EXPORT_SPAN
    spans = [(created_at - DETAIL_TAIL, created_at)]
    spans += [(opened - DETAIL_PAD, closed + DETAIL_PAD if closed else created_at) for opened, closed in episodes]
    floored = sorted((_floor(max(s, limit)), _floor(min(e, created_at))) for s, e in spans)
    merged: list[Window] = []
    for start, end in (w for w in floored if w[1] > w[0]):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    kept: list[Window] = []
    left = DETAIL_MAX
    for start, end in reversed(merged):
        if left <= timedelta():
            break
        cut = max(start, end - left)
        kept.append((cut, end))
        left -= end - cut
    return sorted(kept)


def _window_text(start: datetime, end: datetime) -> str:
    to = f"{end:%H:%M}" if end.date() == start.date() else f"{end:%Y-%m-%d %H:%M}"
    return f"{start:%Y-%m-%d %H:%M} to {to} UTC"


class ArchiveError(Exception):
    """An archive refused before anything is served, with the message to print."""


@dataclass(frozen=True)
class OpenedArchive:
    path: Path
    # The extraction directory, removed on close.
    root: Path
    manifest: dict[str, Any]
    info: ArchiveInfo
    use_case: str
    created_at: datetime


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ArchiveError(f"{path} does not exist.")
    if not path.is_file() or not tarfile.is_tarfile(path):
        raise ArchiveError(f"{path} is not a snapl history archive (not a tarball).")
    with tarfile.open(path, "r:*") as tar:
        try:
            member = tar.extractfile("manifest.json")
        except KeyError:
            member = None
        if member is None:
            raise ArchiveError(f"{path} is not a snapl history archive (no manifest.json).")
        manifest: dict[str, Any] = json.load(member)
    found = manifest.get("format")
    if found != FORMAT:
        raise ArchiveError(f"{path} has format {found}; this snapl reads {FORMAT}.")
    return manifest


def _extract(path: Path, root: Path) -> None:
    with tarfile.open(path, "r:*") as tar:
        members = [m for m in tar.getmembers() if not m.name.lstrip("./").startswith(_SKIPPED)]
        try:
            # The `data` filter strips leading slashes and refuses members that
            # would land outside `root`, links included.
            tar.extractall(root, members=members, filter="data")
        except tarfile.FilterError as exc:
            raise ArchiveError(f"{path} has a member that would be written outside the archive: {exc}") from exc


@contextmanager
def open_archive(path: Path) -> Iterator[OpenedArchive]:
    """Read the manifest, extract into a temporary directory, and remove it on close.

    The archive file is only ever opened for reading.
    """
    manifest = _read_manifest(path)
    with tempfile.TemporaryDirectory(prefix="snapl-archive-open-") as tmp:
        root = Path(tmp)
        _extract(path, root)
        created_at = datetime.fromisoformat(manifest["created_at"])
        info = ArchiveInfo(
            name=path.name,
            created_at=created_at,
            snapl_commit=manifest.get("snapl_commit"),
            parts=[
                ArchivePart(name=part["name"], present=part["present"], reason=part.get("reason"))
                for part in manifest.get("parts", [])
            ],
        )
        yield OpenedArchive(
            path=path,
            root=root,
            manifest=manifest,
            info=info,
            use_case=manifest.get("use_case", "dcfabric"),
            created_at=created_at,
        )


def _export(root: Path, name: str) -> dict[str, Any] | None:
    file = root / "export" / name
    if not file.is_file():
        return None
    data: dict[str, Any] = json.loads(file.read_text())
    return data


def _not_here(source: SourceName, reason: str = NOT_IN_ARCHIVE) -> View[Any]:
    return View(source=source, available=False, reason=reason, remedy=ARCHIVE_REMEDY)


def archived_view[M: BaseModel](root: Path, key: str, model: type[M]) -> View[M]:
    """One view inside ``export/overview.json``, as it was exported, with its own age.

    A view that was down when the archive was made is not shown as a failure
    with a live remedy: it is not in the archive (spec 011 FR-037).
    """
    overview = _export(root, "overview.json")
    raw = overview.get(key) if overview else None
    if raw is None:
        return _not_here(SourceName.TEMPORAL if key in ("schedule", "worker") else SourceName(key))
    view: View[M] = View[model].model_validate(raw)  # type: ignore[valid-type]
    return _as_exported(view)


def _as_exported[M: BaseModel](view: View[M]) -> View[M]:
    """A view down when exported reads as not in the archive, not as a failure with a live remedy."""
    if not view.available:
        return View(
            source=view.source,
            available=False,
            fetched_at=view.fetched_at,
            reason=f"{NOT_IN_ARCHIVE}: {view.reason or 'unavailable'} when it was made",
            remedy=ARCHIVE_REMEDY,
            data=view.data,
        )
    return view


def archived_network(root: Path) -> View[FabricNetwork]:
    """``/network`` as exported, at the archive's end (spec 016). An older archive has none."""
    exported = _export(root, "network.json")
    if exported is None:
        return _not_here(SourceName.PROMETHEUS)
    return _as_exported(NetworkResponse.model_validate(exported).network)


def not_in_archive(source: SourceName) -> View[Any]:
    """What a view the archive lacks answers."""
    return _not_here(source)


def _exported_names(root: Path) -> dict[UUID, str]:
    for name, key in (("devices.json", "devices"), ("overview.json", "devices")):
        exported = _export(root, name)
        rows = ((exported or {}).get(key) or {}).get("data") or []
        if rows:
            return {UUID(row["device_id"]): row["name"] for row in rows}
    return {}


class ArchivedIntent:
    """The Source of Truth as exported: its devices and its topology, no more."""

    def __init__(self, root: Path, created_at: datetime) -> None:
        exported = _export(root, "topology.json")
        self._topology: View[Topology] | None = (
            TopologyResponse.model_validate(exported).topology if exported is not None else None
        )
        self._created_at = created_at
        nodes = self._topology.data.nodes if self._topology and self._topology.data else []
        self._devices = [IntentDevice(UUID(str(node.device_id)), node.name, node.role or "") for node in nodes]
        # Names for the degraded device list: an archive made before the
        # topology export still has the device views it exported.
        self.names: dict[UUID, str] = {device.id: device.name for device in self._devices} or _exported_names(root)

    async def refresh(self) -> View[list[IntentDevice]]:
        if self._topology is None:
            return _not_here(SourceName.SOT, PREDATES)
        if not self._topology.available:
            return _not_here(SourceName.SOT, f"{NOT_IN_ARCHIVE}: {self._topology.reason} when it was made")
        return View(source=SourceName.SOT, available=True, fetched_at=self._topology.fetched_at, data=self._devices)

    def topology(self) -> View[Topology]:
        if self._topology is None:
            return _not_here(SourceName.SOT, PREDATES)
        return self._topology


class ArchivedTemporal:
    """Temporal keeps no history: what ran is in the audit trail, the rest is not archived."""

    async def refresh(self) -> View[Any]:
        return _not_here(SourceName.TEMPORAL)


class ArchivedView:
    """A source whose view is the one exported inside ``overview.json``."""

    def __init__(self, root: Path, key: str, model: type[BaseModel]) -> None:
        self._view = archived_view(root, key, model)

    async def refresh(self) -> View[Any]:
        return self._view


class ArchivedLabRuns:
    """The archived ``test-runs/``. No git: commits since each run are unknown."""

    def __init__(self, root: Path, created_at: datetime) -> None:
        self._runs = root / "test-runs"
        self._created_at = created_at

    async def refresh(self) -> View[LabEvidence]:
        # A run still `running` when archived is incomplete: its process is not ours to ask about.
        evidence = await asyncio.to_thread(
            read_lab_evidence, self._runs, None, is_alive=lambda _pid: False, no_history=NO_GIT_HISTORY
        )
        return View(source=SourceName.LAB_RUNS, available=True, fetched_at=self._created_at, data=evidence)


def archived_lab_runs(root: Path, created_at: datetime) -> ArchivedLabRuns:
    return ArchivedLabRuns(root, created_at)


class ArchivedMetrics:
    """The metrics as exported: each view over the exported range, and the network views over each window."""

    def __init__(self, root: Path, created_at: datetime) -> None:
        self._created_at = created_at
        self._data: dict[MetricsViewName, MetricsData] = {}
        for view in MetricsViewName:
            exported = _export(root, f"metrics/{view.value}.json")
            data = (exported or {}).get("metrics", {}).get("data")
            if data is not None:
                self._data[view] = MetricsData.model_validate(data)
        self._detail: dict[MetricsViewName, list[MetricsData]] = {}
        for view in NETWORK_VIEWS:
            folder = root / "export" / DETAIL_DIR / view.value
            answers = [json.loads(f.read_text()) for f in sorted(folder.glob("*.json"))] if folder.is_dir() else []
            windows = [MetricsData.model_validate(d) for a in answers if (d := (a.get("metrics") or {}).get("data"))]
            if windows:
                self._detail[view] = sorted(windows, key=lambda data: data.start)

    def windows(self) -> list[Window]:
        """The ranges the network views are held at 15 s, oldest first (#351)."""
        return sorted({(data.start, data.end) for windows in self._detail.values() for data in windows})

    async def refresh(self) -> View[Any]:
        if not self._data:
            return _not_here(SourceName.PROMETHEUS)
        return View(source=SourceName.PROMETHEUS, available=True, fetched_at=self._created_at)

    def exported_range(self, view: MetricsViewName) -> tuple[datetime, datetime] | None:
        data = self._data.get(view)
        return (data.start, data.end) if data is not None else None

    async def network(self) -> NetworkSample:
        """Never asked in archive mode: ``/network`` answers the exported figures (``archived_network``)."""
        raise SourceUnavailable(NOT_IN_ARCHIVE, ARCHIVE_REMEDY)

    async def query(self, view: MetricsViewName, plan: RangePlan) -> MetricsData:
        data = self._data.get(view)
        detail = self._detail.get(view, [])
        if data is None and not detail:
            raise SourceUnavailable(NOT_IN_ARCHIVE, ARCHIVE_REMEDY)
        if data is not None and (plan.start, plan.end) == (data.start, data.end):
            return data
        # The page floors a range to its step, which can take the start up to one step before a window's.
        step = timedelta(seconds=plan.step)
        window = next((w for w in detail if w.start - step <= plan.start and plan.end <= w.end), None)
        if window is not None:
            return _cut(window, max(plan.start, window.start), plan.end)
        held = []
        if data is not None:
            held.append(f"the exported range {data.start:%Y-%m-%d %H:%M} to {data.end:%Y-%m-%d %H:%M} UTC")
        if detail:
            held.append("15 s detail over " + ", ".join(_window_text(w.start, w.end) for w in detail))
        raise SourceUnavailable(f"an archive holds only {' and '.join(held)}", ARCHIVE_REMEDY)


def _cut(window: MetricsData, start: datetime, end: datetime) -> MetricsData:
    """One window's answer over a range inside it."""
    series = [MetricSeries(labels=s.labels, points=[p for p in s.points if start <= p.t <= end]) for s in window.series]
    return window.model_copy(update={"start": start, "end": end, "series": series})


@dataclass(frozen=True)
class ArchiveExports:
    """What the app shows as exported in archive mode, in place of composing it."""

    schedule: View[ScheduleStatus]
    worker: View[WorkerStatus]
    topology: View[Topology]
    links: LinksResponse | None
    metrics: ArchivedMetrics
    # Spec 016: the network at the archive's end, and whether health was exported.
    network: View[FabricNetwork]
    health_exported: bool


def archive_sources(opened: OpenedArchive) -> tuple[Sources, ArchiveExports, ArchiveInfo]:
    """The sources an archive serves from, what it exported, and its description with the metrics range."""
    root, created = opened.root, opened.created_at
    intent = ArchivedIntent(root, created)
    metrics = ArchivedMetrics(root, created)
    sources = Sources(
        audit=AuditSource(str(root / "audit" / "snapl-audit.sqlite"), clock=lambda: created),
        sot=intent,
        temporal=ArchivedTemporal(),
        ci=ArchivedView(root, "ci", CiStatus),
        lab_runs=ArchivedLabRuns(root, created),
        prometheus=metrics,
    )
    links = _export(root, "links.json")
    exports = ArchiveExports(
        schedule=archived_view(root, "schedule", ScheduleStatus),
        worker=archived_view(root, "worker", WorkerStatus),
        topology=intent.topology(),
        links=LinksResponse.model_validate(links) if links is not None else None,
        metrics=metrics,
        network=archived_network(root),
        health_exported=_export(root, "health.json") is not None,
    )
    ranges = [r for view in MetricsViewName if (r := metrics.exported_range(view)) is not None]
    info = opened.info.model_copy(
        update={"metrics_windows": [MetricsWindow(start=start, end=end) for start, end in metrics.windows()]}
    )
    if ranges:
        info = info.model_copy(update={"metrics_start": ranges[0][0], "metrics_end": ranges[0][1]})
    return sources, exports, info


def create_archive_app(opened: OpenedArchive, *, ui_dir: Path | None = pages.BUILT_UI) -> FastAPI:
    """The dashboard on an opened archive: its own clock, no settings from the environment."""
    from snapl_presentation.dashboard.app import create_app  # noqa: PLC0415 — app imports this module's types

    sources, exports, info = archive_sources(opened)
    created = opened.created_at
    return create_app(
        sources,
        CliSettings(),
        opened.use_case,
        clock=lambda: created,
        archive=info,
        exported=exports,
        ui_dir=ui_dir,
    )
