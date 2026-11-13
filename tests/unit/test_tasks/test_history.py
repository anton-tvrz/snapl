"""history.archive and history.purge (spec 012 T028, T029, contracts/history-tasks.md).

Every test works in a temp checkout. One stub HTTP server on a free port plays
Prometheus (it answers the snapshot POST by creating ``snapshots/<name>/`` in
a temp data dir) and the dashboard (it serves canned JSON). A part's source
being down is a closed port. Nothing here touches the real stack or the real
``.snapl/``.
"""

from __future__ import annotations

import json
import socket
import sqlite3
import tarfile
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path  # noqa: TC003 — fixture annotations
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from invoke import Context, Exit

from snapl_orchestrator.audit.sqlite import SqliteAuditLog
from snapl_orchestrator.models import AuditEvent, AuditEventType
from snapl_presentation.dashboard.models import MetricsViewName
from tasks import history, ns

pytestmark = pytest.mark.unit

SNAPSHOT_NAME = "20261108T203115Z-674e642f920efff4"
# Read before any fixture points it at a temp dir.
REAL_PROMETHEUS_DATA = getattr(history, "PROMETHEUS_DATA", None)
# Spec 011 adds topology, devices, runs and drift-timeline, for archive mode.
EXPORTS = ("overview", "history", "links", "sources", "topology", "devices", "runs", "drift-timeline")


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Stub:
    """Prometheus and the dashboard on one port, recording what was asked."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        # Set to answer the snapshot POST the way a Prometheus without the admin API does.
        self.snapshot_refusal: tuple[int, dict] | None = None
        # Dashboard routes that answer 404, as an older server would.
        self.missing: set[str] = set()
        self.requests: list[tuple[str, str, dict[str, list[str]]]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def _answer(self, status: int, body: object) -> None:
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self) -> None:
                url = urlsplit(self.path)
                stub.requests.append(("POST", url.path, parse_qs(url.query)))
                if url.path == "/api/v1/admin/tsdb/snapshot" and stub.snapshot_refusal:
                    self._answer(*stub.snapshot_refusal)
                elif url.path == "/api/v1/admin/tsdb/snapshot":
                    snapshot = stub.data_dir / "snapshots" / SNAPSHOT_NAME / "01HBLOCK"
                    snapshot.mkdir(parents=True)
                    (snapshot / "chunks").write_bytes(b"\x00" * 64)
                    self._answer(200, {"status": "success", "data": {"name": SNAPSHOT_NAME}})
                else:
                    self._answer(404, {})

            def do_GET(self) -> None:
                url = urlsplit(self.path)
                stub.requests.append(("GET", url.path, parse_qs(url.query)))
                if url.path == "/-/ready":
                    self._answer(200, "Prometheus Server is Ready.")
                elif url.path in stub.missing:
                    self._answer(404, {"detail": "Not Found"})
                elif url.path.startswith("/api/v1/"):
                    self._answer(200, {"contract_version": "1", "path": url.path})
                else:
                    self._answer(404, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> Stub:
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()

    def asked(self, method: str, path: str) -> list[dict[str, list[str]]]:
        return [query for m, p, query in self.requests if (m, p) == (method, path)]


def _event(i: int) -> AuditEvent:
    return AuditEvent(
        event_id=uuid4(),
        workflow_id=f"scan-drift-dcfabric-{i}",
        workflow_type="ScanDrift",
        target_id=uuid4(),
        event_type=AuditEventType.WORKFLOW_STARTED,
        outcome=None,
        payload={},
        timestamp=datetime(2026, 11, 8, 20, 0, tzinfo=UTC) + timedelta(minutes=i),
    )


@pytest.fixture
async def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temp checkout: an audit DB with three rows, one recorded test run, a Prometheus data dir."""
    root = tmp_path / "checkout"
    snapl = root / ".snapl"
    audit = root / "snapl-audit.sqlite"
    root.mkdir()
    log = SqliteAuditLog(database_url=str(audit))
    await log.initialize()
    for i in range(3):
        await log.append(_event(i))
    await log.close()

    run = snapl / "test-runs" / "20261108T101500Z-e2e"
    run.mkdir(parents=True)
    (run / "run.json").write_text('{"format": 1}')
    (snapl / "prometheus").mkdir()

    monkeypatch.setattr(history, "PROMETHEUS_DATA", snapl / "prometheus")
    monkeypatch.setenv("SNAPL_AUDIT_DB", str(audit))
    monkeypatch.setenv("SNAPL_TEST_RUNS_DIR", str(snapl / "test-runs"))
    monkeypatch.setenv("SNAPL_ARCHIVE_DIR", str(tmp_path / "archives"))
    monkeypatch.setenv("PROMETHEUS_ADDRESS", f"http://127.0.0.1:{_closed_port()}")
    monkeypatch.setenv("DASHBOARD_PORT", str(_closed_port()))
    return root


@pytest.fixture
def stub(checkout: Path, monkeypatch: pytest.MonkeyPatch):
    with Stub(checkout / ".snapl" / "prometheus") as server:
        monkeypatch.setenv("PROMETHEUS_ADDRESS", server.url)
        monkeypatch.setenv("DASHBOARD_PORT", str(server.port))
        yield server


def _ctx() -> MagicMock:
    return MagicMock(spec=Context)


def _the_archive(out: Path) -> Path:
    [archive] = list(out.iterdir())
    return archive


def _members(archive: Path) -> dict[str, bytes]:
    with tarfile.open(archive, "r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}  # type: ignore[union-attr]


# --- archive (T028) --------------------------------------------------------------


def test_archive_holds_every_part_and_exits_0(stub: Stub, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    history.archive(_ctx())

    archive = _the_archive(tmp_path / "archives")
    assert archive.name.startswith("snapl-history-")
    assert archive.name.endswith(".tar.gz")
    members = _members(archive)
    expected = {
        "manifest.json",
        "audit/snapl-audit.sqlite",
        f"prometheus/{SNAPSHOT_NAME}/01HBLOCK/chunks",
        "test-runs/20261108T101500Z-e2e/run.json",
        *(f"export/{name}.json" for name in EXPORTS),
        *(f"export/metrics/{view.value}.json" for view in MetricsViewName),
    }
    assert expected <= set(members)
    out = capsys.readouterr().out
    assert str(archive) in out
    assert all(part in out for part in ("audit_db", "metrics_snapshot", "test_runs", "dashboard_export"))


def test_the_manifest_matches_the_data_model(stub: Stub, tmp_path: Path) -> None:
    history.archive(_ctx(), use_case="dcfabric")

    manifest = json.loads(_members(_the_archive(tmp_path / "archives"))["manifest.json"])

    assert manifest["format"] == "snapl-history/1"
    assert datetime.strptime(manifest["created_at"] + "+0000", "%Y-%m-%dT%H:%M:%SZ%z")
    assert manifest["snapl_commit"] is None or len(manifest["snapl_commit"]) == 40
    assert manifest["use_case"] == "dcfabric"
    parts = {part["name"]: part for part in manifest["parts"]}
    assert set(parts) == {"audit_db", "metrics_snapshot", "test_runs", "dashboard_export"}
    for part in parts.values():
        assert set(part) == {"name", "path", "present", "reason", "source", "bytes"}
        assert part["present"] is True
        assert part["reason"] is None
        assert isinstance(part["bytes"], int)
        assert part["bytes"] > 0
    assert parts["audit_db"]["path"] == "audit/snapl-audit.sqlite"
    assert parts["metrics_snapshot"]["path"] == f"prometheus/{SNAPSHOT_NAME}"
    assert parts["metrics_snapshot"]["source"].startswith(stub.url)
    assert parts["dashboard_export"]["path"] == "export"


def test_the_audit_copy_opens_and_has_every_row(stub: Stub, tmp_path: Path, checkout: Path) -> None:
    history.archive(_ctx())

    copy = tmp_path / "copy.sqlite"
    copy.write_bytes(_members(_the_archive(tmp_path / "archives"))["audit/snapl-audit.sqlite"])
    with sqlite3.connect(copy) as copied, sqlite3.connect(checkout / "snapl-audit.sqlite") as source:
        tables = [row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        assert tables
        for table in tables:
            query = f"SELECT COUNT(*) FROM {table}"  # noqa: S608 — table names read from sqlite_master
            assert copied.execute(query).fetchone() == source.execute(query).fetchone()
        assert sum(copied.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables) >= 3  # noqa: S608


def test_the_snapshot_is_removed_from_the_live_store(stub: Stub, tmp_path: Path, checkout: Path) -> None:
    history.archive(_ctx())

    assert stub.asked("POST", "/api/v1/admin/tsdb/snapshot")
    assert not (checkout / ".snapl" / "prometheus" / "snapshots" / SNAPSHOT_NAME).exists()


def test_the_export_covers_the_last_7_days(stub: Stub) -> None:
    before = datetime.now(UTC)
    history.archive(_ctx())

    ranged = ("/api/v1/history", "/api/v1/drift-timeline", *(f"/api/v1/metrics/{v.value}" for v in MetricsViewName))
    for path in ranged:
        [query] = stub.asked("GET", path)
        start = datetime.fromisoformat(query["start"][0])
        assert abs((before - timedelta(days=7)) - start) < timedelta(minutes=1), path
    for name in ("overview", "links", "sources", "topology", "devices", "runs"):
        assert stub.asked("GET", f"/api/v1/{name}")


def test_a_dashboard_without_the_spec_011_routes_is_a_failed_export_part(stub: Stub, tmp_path: Path) -> None:
    """An older dashboard answers 404 for /topology: the archive is still written, the part says why."""
    stub.missing = {"/api/v1/topology"}
    with pytest.raises(Exit):
        history.archive(_ctx())

    [archive] = (tmp_path / "archives").glob("*.tar.gz")
    with tarfile.open(archive) as tar:
        manifest = json.load(tar.extractfile("manifest.json"))  # type: ignore[arg-type]
        assert "export/devices.json" in tar.getnames()
    export = next(part for part in manifest["parts"] if part["name"] == "dashboard_export")
    assert export["present"] is False
    assert "/api/v1/topology (HTTP 404)" in export["reason"]
    assert manifest["format"] == "snapl-history/1"


def test_prometheus_down_still_writes_the_archive_and_exits_1(
    stub: Stub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("PROMETHEUS_ADDRESS", f"http://127.0.0.1:{_closed_port()}")

    with pytest.raises(Exit) as caught:
        history.archive(_ctx())

    assert caught.value.code == 1
    members = _members(_the_archive(tmp_path / "archives"))
    parts = {p["name"]: p for p in json.loads(members["manifest.json"])["parts"]}
    assert parts["metrics_snapshot"]["present"] is False
    assert "not reachable" in parts["metrics_snapshot"]["reason"]
    assert parts["dashboard_export"]["present"] is True
    assert "audit/snapl-audit.sqlite" in members
    assert "metrics_snapshot" in capsys.readouterr().err


def test_dashboard_down_still_writes_the_archive_and_exits_1(
    stub: Stub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("DASHBOARD_PORT", str(_closed_port()))

    with pytest.raises(Exit) as caught:
        history.archive(_ctx())

    assert caught.value.code == 1
    members = _members(_the_archive(tmp_path / "archives"))
    parts = {p["name"]: p for p in json.loads(members["manifest.json"])["parts"]}
    assert parts["dashboard_export"]["present"] is False
    assert "snapl dashboard" in parts["dashboard_export"]["reason"]
    assert parts["metrics_snapshot"]["present"] is True
    assert not any(name.startswith("export/") for name in members)
    assert "dashboard_export" in capsys.readouterr().err


def test_a_missing_audit_db_is_a_missing_part(stub: Stub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNAPL_AUDIT_DB", str(tmp_path / "nowhere.sqlite"))

    with pytest.raises(Exit):
        history.archive(_ctx())

    parts = {p["name"]: p for p in json.loads(_members(_the_archive(tmp_path / "archives"))["manifest.json"])["parts"]}
    assert parts["audit_db"]["present"] is False
    assert "nowhere.sqlite" in parts["audit_db"]["reason"]
    assert not (tmp_path / "nowhere.sqlite").exists(), "the archive must never create the audit DB"


def test_a_prometheus_without_the_admin_api_is_a_missing_part(stub: Stub, tmp_path: Path) -> None:
    stub.snapshot_refusal = (
        405,
        {"status": "error", "errorType": "unavailable", "error": "admin APIs disabled"},
    )

    with pytest.raises(Exit):
        history.archive(_ctx())

    parts = {p["name"]: p for p in json.loads(_members(_the_archive(tmp_path / "archives"))["manifest.json"])["parts"]}
    assert parts["metrics_snapshot"]["present"] is False
    assert parts["metrics_snapshot"]["reason"] == "Prometheus refused the snapshot: admin APIs disabled"


def test_no_recorded_test_runs_is_a_missing_part(stub: Stub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "no-runs"
    empty.mkdir()
    monkeypatch.setenv("SNAPL_TEST_RUNS_DIR", str(empty))

    with pytest.raises(Exit):
        history.archive(_ctx())

    members = _members(_the_archive(tmp_path / "archives"))
    parts = {p["name"]: p for p in json.loads(members["manifest.json"])["parts"]}
    assert parts["test_runs"]["present"] is False
    assert "uv run invoke test-e2e" in parts["test_runs"]["reason"]
    assert not any(name.startswith("test-runs/") for name in members)


def test_no_partial_file_is_left_behind(stub: Stub, tmp_path: Path) -> None:
    history.archive(_ctx())

    assert [p.name for p in (tmp_path / "archives").iterdir() if p.name.endswith(".partial")] == []


def test_a_failure_while_writing_leaves_no_file_that_looks_finished(
    stub: Stub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_add(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(tarfile.TarFile, "add", broken_add)

    with pytest.raises(OSError, match="disk full"):
        history.archive(_ctx())

    assert list((tmp_path / "archives").iterdir()) == []


# --- purge (T029) ----------------------------------------------------------------


@pytest.fixture
def store(checkout: Path, tmp_path: Path) -> dict[str, Path]:
    """What purge may delete, and everything it must leave alone."""
    data = checkout / ".snapl" / "prometheus"
    (data / "wal").mkdir(parents=True)
    (data / "wal" / "00000001").write_bytes(b"\x01" * 128)
    archives = tmp_path / "archives"
    archives.mkdir()
    (archives / "snapl-history-20261101T000000Z.tar.gz").write_bytes(b"old archive")
    return {
        "data": data,
        "audit": checkout / "snapl-audit.sqlite",
        "runs": checkout / ".snapl" / "test-runs" / "20261108T101500Z-e2e" / "run.json",
        "archive": archives / "snapl-history-20261101T000000Z.tar.gz",
    }


def _tty(monkeypatch: pytest.MonkeyPatch, *, answer: str | None) -> None:
    monkeypatch.setattr(history.sys.stdin, "isatty", lambda: answer is not None)
    monkeypatch.setattr("builtins.input", lambda _prompt="": answer)


def test_purge_refuses_while_prometheus_answers(
    store: dict[str, Path], stub: Stub, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _tty(monkeypatch, answer="purge")

    with pytest.raises(Exit) as caught:
        history.purge(_ctx(), yes=True)

    assert caught.value.code == 1
    assert "uv run invoke dev.stop" in capsys.readouterr().err
    assert (store["data"] / "wal" / "00000001").exists()


def test_purge_refuses_without_a_tty_or_yes(store: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _tty(monkeypatch, answer=None)

    with pytest.raises(Exit) as caught:
        history.purge(_ctx())

    assert caught.value.code == 1
    assert (store["data"] / "wal" / "00000001").exists()


@pytest.mark.parametrize("answer", ["", "yes", "y", "PURGE", "purge please"])
def test_any_answer_but_purge_deletes_nothing(
    store: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, answer: str
) -> None:
    _tty(monkeypatch, answer=answer)

    history.purge(_ctx())

    assert (store["data"] / "wal" / "00000001").exists()
    assert "nothing deleted" in capsys.readouterr().out.lower()


def test_typing_purge_deletes_the_store(
    store: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _tty(monkeypatch, answer="purge")

    history.purge(_ctx())

    assert not store["data"].exists()
    out = capsys.readouterr().out
    assert str(store["data"]) in out


def test_yes_deletes_only_the_metrics_store(store: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _tty(monkeypatch, answer=None)
    kept = {name: path.read_bytes() for name, path in store.items() if name != "data"}

    history.purge(_ctx(), yes=True)

    assert not store["data"].exists()
    assert {name: store[name].read_bytes() for name in kept} == kept


def test_purge_with_nothing_to_delete_says_so(
    store: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    import shutil

    shutil.rmtree(store["data"])

    history.purge(_ctx(), yes=True)

    assert "nothing to delete" in capsys.readouterr().out.lower()


def test_a_permission_error_deletes_through_a_throwaway_container(
    store: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Linux the files belong to the container's `nobody` (research R6)."""

    def denied(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(history.shutil, "rmtree", denied)
    ctx = _ctx()

    history.purge(ctx, yes=True)

    [command] = [call.args[0] for call in ctx.run.call_args_list]
    assert command.startswith("docker run --rm")
    assert f"{store['data'].parent}:/snapl" in command
    assert "rm -rf /snapl/prometheus" in command


def test_the_history_collection_is_registered() -> None:
    assert set(ns.collections["history"].tasks) == {"archive", "purge"}


def test_the_store_is_the_one_dev_deps_creates() -> None:
    from tasks import dev

    assert REAL_PROMETHEUS_DATA is not None
    assert REAL_PROMETHEUS_DATA == dev.PROMETHEUS_DATA
