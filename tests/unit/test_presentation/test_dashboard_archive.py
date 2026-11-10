"""Opening a history archive and the sources it gives (spec 011 T069, research R13).

The archives are built in a temporary directory with tasks/history.py's own
packing function (`_write`), from the captured demo arc and the fixture
server's exports with every source up, so the parts are what a real
`history.archive` writes.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import shutil
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from snapl_presentation.dashboard.archive import (
    ARCHIVE_REMEDY,
    NOT_IN_ARCHIVE,
    ArchivedIntent,
    ArchivedMetrics,
    ArchiveError,
    archived_lab_runs,
    archived_view,
    open_archive,
)
from snapl_presentation.dashboard.models import CiStatus, MetricsViewName, ScheduleStatus, WorkerStatus
from snapl_presentation.dashboard.prometheus import plan_range
from snapl_presentation.dashboard.sources import SourceUnavailable
from tasks import history
from tests.unit.test_presentation.dashboard_fakes import ARC_FIXTURE

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
SERVER = ROOT / "dev" / "scripts" / "serve_dashboard_fixtures.py"
LAB_RUNS = Path(__file__).parent / "fixtures" / "lab_runs"
CREATED = "2026-11-07T21:20:48Z"
EXPORTS = ("overview", "history", "links", "sources", "topology", "devices", "runs", "drift-timeline")


def _exports() -> dict[str, bytes]:
    """The fixture server's answers with every source up, as history.archive would save them."""
    spec = importlib.util.spec_from_file_location("serve_dashboard_fixtures", SERVER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client = TestClient(module.build_app(ui_dir=ROOT / "no-built-ui", up=True))
    start = (datetime.fromisoformat(CREATED) - timedelta(days=7)).isoformat()
    files = {}
    for name in EXPORTS:
        params = {"start": start} if name in ("history", "drift-timeline") else {}
        files[f"{name}.json"] = client.get(f"/api/v1/{name}", params=params).content
    for view in MetricsViewName:
        files[f"metrics/{view.value}.json"] = client.get(
            f"/api/v1/metrics/{view.value}", params={"start": start}
        ).content
    return files


EXPORTED = _exports()


def build_archive(
    tmp_path: Path,
    *,
    omit: tuple[str, ...] = (),
    manifest: dict[str, Any] | None = None,
    prometheus: bool = True,
) -> Path:
    """An archive as history.archive writes one. ``omit`` leaves export files out."""
    staging = tmp_path / "staging"
    (staging / "audit").mkdir(parents=True)
    shutil.copy(ARC_FIXTURE, staging / "audit" / "snapl-audit.sqlite")
    shutil.copytree(LAB_RUNS, staging / "test-runs", ignore=shutil.ignore_patterns("README.md"))
    for name, body in EXPORTED.items():
        if name.removesuffix(".json") not in omit:
            (staging / "export" / name).parent.mkdir(parents=True, exist_ok=True)
            (staging / "export" / name).write_bytes(body)
    snapshot = staging / "prometheus" / "20261107T212048Z-abc"
    snapshot.mkdir(parents=True)
    (snapshot / "chunks").write_bytes(b"\x00" * 64)
    parts = [
        {"name": "audit_db", "path": "audit/snapl-audit.sqlite", "present": True, "source": "x"},
        {"name": "metrics_snapshot", "path": "prometheus/20261107T212048Z-abc", "present": True, "source": "x"},
        {"name": "test_runs", "path": "test-runs", "present": True, "source": "x"},
        {"name": "dashboard_export", "path": "export", "present": not omit, "source": "x", "reason": None},
    ]
    body = {"format": history.FORMAT, "created_at": CREATED, "snapl_commit": "6baff39", "use_case": "dcfabric"}
    body |= {"parts": parts} | (manifest or {})
    (staging / "manifest.json").write_text(json.dumps(body))
    members = [
        (staging / "manifest.json", "manifest.json"),
        (staging / "audit" / "snapl-audit.sqlite", "audit/snapl-audit.sqlite"),
    ]
    members += [(staging / "test-runs", "test-runs"), (staging / "export", "export")]
    members += [(snapshot, "prometheus/20261107T212048Z-abc")] if prometheus else []
    target = tmp_path / "snapl-history-20261107T212048Z.tar.gz"
    history._write(target, members)
    return target


def _raw_tar(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


# --- open_archive ----------------------------------------------------------------


def test_open_gives_the_manifest_and_a_directory_it_removes_on_close(tmp_path: Path):
    archive = build_archive(tmp_path)

    with open_archive(archive) as opened:
        root = opened.root
        assert opened.manifest["snapl_commit"] == "6baff39"
        assert opened.use_case == "dcfabric"
        assert opened.created_at == datetime(2026, 11, 7, 21, 20, 48, tzinfo=UTC)
        assert opened.info.name == "snapl-history-20261107T212048Z.tar.gz"
        assert [part.name for part in opened.info.parts] == [
            "audit_db",
            "metrics_snapshot",
            "test_runs",
            "dashboard_export",
        ]
        assert (root / "audit" / "snapl-audit.sqlite").is_file()
    assert not root.exists()


def test_the_directory_is_removed_when_the_body_raises(tmp_path: Path):
    archive = build_archive(tmp_path)

    seen: list[Path] = []

    def serve() -> None:
        with open_archive(archive) as opened:
            seen.append(opened.root)
            raise RuntimeError("the server stopped")

    with pytest.raises(RuntimeError):
        serve()
    assert not seen[0].exists()


def test_the_metrics_snapshot_is_not_extracted(tmp_path: Path):
    with open_archive(build_archive(tmp_path)) as opened:
        assert not (opened.root / "prometheus").exists()


def test_a_missing_part_is_listed_with_its_reason(tmp_path: Path):
    parts = [{"name": "metrics_snapshot", "path": "prometheus", "present": False, "source": "x", "reason": "down"}]
    with open_archive(build_archive(tmp_path, manifest={"parts": parts}, prometheus=False)) as opened:
        [part] = opened.info.parts
        assert (part.name, part.present, part.reason) == ("metrics_snapshot", False, "down")


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda p: p / "nope.tar.gz", "does not exist."),
        (
            lambda p: (p / "notes.txt").write_text("hello") and p / "notes.txt",
            "is not a snapl history archive (not a tarball).",
        ),
        (
            lambda p: _raw_tar(p / "a.tar.gz", {"export/x.json": b"{}"}),
            "is not a snapl history archive (no manifest.json).",
        ),
        (
            lambda p: _raw_tar(p / "b.tar.gz", {"manifest.json": json.dumps({"format": "snapl-history/2"}).encode()}),
            "has format snapl-history/2; this snapl reads snapl-history/1.",
        ),
    ],
)
def test_refusals_say_what_is_wrong(tmp_path: Path, make, message: str):
    path = make(tmp_path)

    with pytest.raises(ArchiveError) as raised, open_archive(path):
        pass
    assert str(raised.value) == f"{path} {message}"


def test_a_member_that_would_escape_the_directory_is_refused_and_not_written(tmp_path: Path):
    manifest = json.dumps(
        {"format": history.FORMAT, "created_at": CREATED, "use_case": "dcfabric", "parts": []}
    ).encode()
    path = _raw_tar(tmp_path / "evil.tar.gz", {"manifest.json": manifest, "../escaped.txt": b"x"})

    with pytest.raises(ArchiveError, match="outside the archive"), open_archive(path):
        pass
    assert not list(tmp_path.parent.glob("escaped.txt"))
    assert not (tmp_path / "escaped.txt").exists()


def test_an_absolute_member_is_written_inside_the_directory_only(tmp_path: Path):
    outside = tmp_path / "outside.txt"
    manifest = json.dumps(
        {"format": history.FORMAT, "created_at": CREATED, "use_case": "dcfabric", "parts": []}
    ).encode()
    path = _raw_tar(tmp_path / "abs.tar.gz", {"manifest.json": manifest, str(outside): b"x"})

    with open_archive(path):
        assert not outside.exists()


def test_the_archive_file_is_not_changed(tmp_path: Path):
    archive = build_archive(tmp_path)
    before = hashlib.sha256(archive.read_bytes()).hexdigest()

    with open_archive(archive):
        pass

    assert hashlib.sha256(archive.read_bytes()).hexdigest() == before


# --- the archived sources --------------------------------------------------------


async def test_intent_and_topology_come_from_the_exported_topology(tmp_path: Path):
    with open_archive(build_archive(tmp_path)) as opened:
        intent = ArchivedIntent(opened.root, opened.created_at)
        view = await intent.refresh()
        topology = intent.topology()

    assert view.available is True
    # As exported: the age is the topology's own.
    assert view.fetched_at == topology.fetched_at
    assert sorted(device.name for device in view.data) == [
        "leaf-01",
        "leaf-02",
        "leaf-03",
        "leaf-04",
        "spine-01",
        "spine-02",
    ]
    assert {device.role for device in view.data} == {"spine", "leaf"}
    assert set(intent.names.values()) == {device.name for device in view.data}
    assert topology.available is True
    assert len(topology.data.links) == 8


async def test_an_archive_without_the_topology_export_predates_it(tmp_path: Path):
    with open_archive(build_archive(tmp_path, omit=("topology",))) as opened:
        intent = ArchivedIntent(opened.root, opened.created_at)
        view = await intent.refresh()
        topology = intent.topology()

    assert view.available is False
    assert "predates" in view.reason
    assert view.remedy == ARCHIVE_REMEDY
    assert topology.available is False
    assert "predates" in topology.reason


@pytest.mark.parametrize(("key", "model"), [("schedule", ScheduleStatus), ("worker", WorkerStatus), ("ci", CiStatus)])
def test_schedule_worker_and_ci_are_the_exported_views_with_their_own_age(tmp_path: Path, key: str, model: type):
    exported = json.loads(EXPORTED["overview.json"])[key]
    with open_archive(build_archive(tmp_path)) as opened:
        view = archived_view(opened.root, key, model)

    assert view.model_dump(mode="json") == exported
    assert isinstance(view.data, model)


def test_a_view_the_archive_lacks_is_not_in_this_archive_with_the_dashboard_command(tmp_path: Path):
    with open_archive(build_archive(tmp_path, omit=("overview",))) as opened:
        view = archived_view(opened.root, "schedule", ScheduleStatus)

    assert (view.available, view.reason, view.remedy, view.data) == (False, NOT_IN_ARCHIVE, ARCHIVE_REMEDY, None)


def test_a_view_that_was_down_when_exported_is_not_in_this_archive_rather_than_a_failure(tmp_path: Path):
    overview = json.loads(EXPORTED["overview.json"])
    overview["ci"] = {"source": "ci", "available": False, "reason": "rate limited", "remedy": "export GITHUB_TOKEN=…"}
    with open_archive(build_archive(tmp_path)) as opened:
        (opened.root / "export" / "overview.json").write_text(json.dumps(overview))
        view = archived_view(opened.root, "ci", CiStatus)

    assert (view.available, view.remedy) == (False, ARCHIVE_REMEDY)
    assert view.reason == f"{NOT_IN_ARCHIVE}: rate limited when it was made"


async def test_metrics_answer_the_exported_range_and_refuse_another(tmp_path: Path):
    exported = json.loads(EXPORTED["metrics/worker-up.json"])["metrics"]["data"]
    start, end = datetime.fromisoformat(exported["start"]), datetime.fromisoformat(exported["end"])
    with open_archive(build_archive(tmp_path)) as opened:
        metrics = ArchivedMetrics(opened.root, opened.created_at)
        assert metrics.exported_range(MetricsViewName.WORKER_UP) == (start, end)
        span = int((end - start).total_seconds())
        data = await metrics.query(MetricsViewName.WORKER_UP, plan_range(start, end, max(15, span // 240)))
        assert data.model_dump(mode="json") == exported

        with pytest.raises(SourceUnavailable) as refused:
            await metrics.query(MetricsViewName.WORKER_UP, plan_range(end - timedelta(hours=1), end, 15))
        assert (await metrics.refresh()).available is True

    assert str(refused.value.reason).startswith("an archive holds only the exported range 2026-10-31")
    assert refused.value.remedy == ARCHIVE_REMEDY


async def test_metrics_the_archive_lacks_are_not_in_this_archive(tmp_path: Path):
    with open_archive(build_archive(tmp_path, omit=("metrics/gnmi-errors",))) as opened:
        metrics = ArchivedMetrics(opened.root, opened.created_at)
        assert metrics.exported_range(MetricsViewName.GNMI_ERRORS) is None
        with pytest.raises(SourceUnavailable, match=NOT_IN_ARCHIVE):
            await metrics.query(
                MetricsViewName.GNMI_ERRORS,
                plan_range(datetime.fromisoformat(CREATED) - timedelta(hours=1), datetime.fromisoformat(CREATED), 15),
            )


async def test_lab_runs_come_from_the_archive_with_no_git_history(tmp_path: Path):
    with open_archive(build_archive(tmp_path)) as opened:
        view = await archived_lab_runs(opened.root, opened.created_at).refresh()

    assert view.available is True
    assert view.data.latest
    assert set(view.data.commits_on_main_since.values()) == {None}
    assert set(view.data.commits_unknown_reason.values()) == {"an archive has no git history"}
