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
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel

from snapl_presentation.dashboard.lab_runs import read_lab_evidence
from snapl_presentation.dashboard.models import (
    ArchiveInfo,
    ArchivePart,
    LabEvidence,
    MetricsData,
    MetricsViewName,
    SourceName,
    Topology,
    TopologyResponse,
    View,
)
from snapl_presentation.dashboard.projections import IntentDevice
from snapl_presentation.dashboard.sources import SourceUnavailable

if TYPE_CHECKING:
    from collections.abc import Iterator

    from snapl_presentation.dashboard.prometheus import RangePlan

FORMAT = "snapl-history/1"
NOT_IN_ARCHIVE = "not in this archive"
# Every unavailable view carries a remedy (contracts/ui-api.md): this is how to see it live.
ARCHIVE_REMEDY = "uv run snapl dashboard"
NO_GIT_HISTORY = "an archive has no git history"
PREDATES = "this archive predates the topology export (spec 011): devices are listed from the audit trail"
# Archive mode never reads the Prometheus snapshot (R13 rejects starting
# Prometheus on it), and it is most of a real archive's size.
_SKIPPED = ("prometheus/",)


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
        self.names: dict[UUID, str] = {device.id: device.name for device in self._devices}

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
    """The metrics as exported: each view over the exported range only."""

    def __init__(self, root: Path, created_at: datetime) -> None:
        self._created_at = created_at
        self._data: dict[MetricsViewName, MetricsData] = {}
        for view in MetricsViewName:
            exported = _export(root, f"metrics/{view.value}.json")
            data = (exported or {}).get("metrics", {}).get("data")
            if data is not None:
                self._data[view] = MetricsData.model_validate(data)

    async def refresh(self) -> View[Any]:
        if not self._data:
            return _not_here(SourceName.PROMETHEUS)
        return View(source=SourceName.PROMETHEUS, available=True, fetched_at=self._created_at)

    def exported_range(self, view: MetricsViewName) -> tuple[datetime, datetime] | None:
        data = self._data.get(view)
        return (data.start, data.end) if data is not None else None

    async def query(self, view: MetricsViewName, plan: RangePlan) -> MetricsData:
        data = self._data.get(view)
        if data is None:
            raise SourceUnavailable(NOT_IN_ARCHIVE, ARCHIVE_REMEDY)
        if (plan.start, plan.end) != (data.start, data.end):
            exported = f"{data.start:%Y-%m-%d %H:%M} to {data.end:%Y-%m-%d %H:%M} UTC"
            raise SourceUnavailable(f"an archive holds only the exported range {exported}", ARCHIVE_REMEDY)
        return data
