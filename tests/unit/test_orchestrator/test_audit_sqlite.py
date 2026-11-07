"""Unit tests for SqliteAuditLog — file-backed, append-only, queryable."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from snapl_orchestrator.audit.sqlite import SqliteAuditLog
from snapl_orchestrator.exceptions import AuditLogError
from snapl_orchestrator.models import AuditEvent, AuditEventType, WorkflowReason

pytestmark = pytest.mark.unit


def _event(
    *,
    workflow_id: str = "wf",
    target_id: UUID | str | None = None,
    event_type: AuditEventType = AuditEventType.WORKFLOW_STARTED,
    activity_name: str | None = None,
    outcome: str | None = None,
    reason: WorkflowReason | None = None,
    timestamp: datetime | None = None,
) -> AuditEvent:
    return AuditEvent(
        event_id=uuid4(),
        workflow_id=workflow_id,
        workflow_type="DeployIntent",
        target_id=target_id if target_id is not None else uuid4(),
        event_type=event_type,
        activity_name=activity_name,
        outcome=outcome,
        reason=reason,
        payload={"k": "v"},
        timestamp=timestamp or datetime.now(tz=UTC),
    )


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "audit.sqlite")


@pytest.mark.asyncio
async def test_initialize_creates_table_and_indexes(tmp_db_path: str) -> None:
    log = SqliteAuditLog(database_url=tmp_db_path)
    await log.initialize()
    # Second initialize is idempotent.
    await log.initialize()


@pytest.mark.asyncio
async def test_append_then_query_by_workflow(tmp_db_path: str) -> None:
    log = SqliteAuditLog(database_url=tmp_db_path)
    await log.initialize()
    e1 = _event(workflow_id="wf-A")
    e2 = _event(workflow_id="wf-B")
    e3 = _event(workflow_id="wf-A")
    await log.append(e1)
    await log.append(e2)
    await log.append(e3)

    out = await log.query_by_workflow("wf-A")
    assert {e.event_id for e in out} == {e1.event_id, e3.event_id}


@pytest.mark.asyncio
async def test_query_returns_chronological_order(tmp_db_path: str) -> None:
    log = SqliteAuditLog(database_url=tmp_db_path)
    await log.initialize()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    older = _event(workflow_id="wf", timestamp=base)
    newer = _event(workflow_id="wf", timestamp=base + timedelta(seconds=5))
    await log.append(newer)
    await log.append(older)

    out = await log.query_by_workflow("wf")
    # Insertion order is preserved (id ASC), and id is monotonic.
    assert [e.event_id for e in out] == [newer.event_id, older.event_id]


@pytest.mark.asyncio
async def test_query_by_device_filters_uuid(tmp_db_path: str) -> None:
    log = SqliteAuditLog(database_url=tmp_db_path)
    await log.initialize()
    device_a = uuid4()
    device_b = uuid4()
    e1 = _event(target_id=device_a)
    e2 = _event(target_id=device_b)
    e3 = _event(target_id=device_a)
    for e in (e1, e2, e3):
        await log.append(e)

    out = await log.query_by_device(device_a)
    assert {e.event_id for e in out} == {e1.event_id, e3.event_id}


@pytest.mark.asyncio
async def test_query_by_time_range_is_half_open(tmp_db_path: str) -> None:
    log = SqliteAuditLog(database_url=tmp_db_path)
    await log.initialize()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    e0 = _event(timestamp=base)
    e1 = _event(timestamp=base + timedelta(seconds=10))
    e2 = _event(timestamp=base + timedelta(seconds=20))
    for e in (e0, e1, e2):
        await log.append(e)

    out = await log.query_by_time_range(
        base + timedelta(seconds=5),
        base + timedelta(seconds=20),
    )
    # e1 included; e0 before start; e2 == end → excluded (half-open).
    assert [e.event_id for e in out] == [e1.event_id]


_PLUS_2 = timezone(timedelta(hours=2))


@pytest.mark.asyncio
async def test_query_by_time_range_compares_instants_not_text(tmp_db_path: str) -> None:
    """Bounds in another offset name the same instants; text comparison would not."""
    log = SqliteAuditLog(database_url=tmp_db_path)
    await log.initialize()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    e0 = _event(timestamp=base)
    e1 = _event(timestamp=base + timedelta(seconds=10))
    e2 = _event(timestamp=base + timedelta(seconds=20))
    for e in (e0, e1, e2):
        await log.append(e)

    out = await log.query_by_time_range(
        (base + timedelta(seconds=5)).astimezone(_PLUS_2),
        (base + timedelta(seconds=20)).astimezone(_PLUS_2),
    )
    assert [e.event_id for e in out] == [e1.event_id]


@pytest.mark.asyncio
async def test_an_event_stamped_in_another_offset_is_found_by_its_instant(tmp_db_path: str) -> None:
    log = SqliteAuditLog(database_url=tmp_db_path)
    await log.initialize()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    event = _event(timestamp=(base + timedelta(seconds=10)).astimezone(_PLUS_2))
    await log.append(event)

    out = await log.query_by_time_range(base + timedelta(seconds=5), base + timedelta(seconds=20))
    assert [e.event_id for e in out] == [event.event_id]
    assert out[0].timestamp == event.timestamp


@pytest.mark.asyncio
async def test_query_by_time_range_refuses_naive_bounds(tmp_db_path: str) -> None:
    log = SqliteAuditLog(database_url=tmp_db_path)
    await log.initialize()
    with pytest.raises(ValueError, match="timezone"):
        await log.query_by_time_range(datetime(2026, 5, 1), datetime(2026, 5, 2))  # noqa: DTZ001


@pytest.mark.asyncio
async def test_duplicate_event_id_is_stored_once(tmp_db_path: str) -> None:
    """A retried record_audit_event activity re-sends the same event after its
    first attempt already committed; the retry must succeed, not fail the run."""
    log = SqliteAuditLog(database_url=tmp_db_path)
    await log.initialize()
    event = _event(workflow_id="wf-retry")
    await log.append(event)
    await log.append(event)
    assert [e.event_id for e in await log.query_by_workflow("wf-retry")] == [event.event_id]


@pytest.mark.asyncio
async def test_durability_across_reopen(tmp_path: Path) -> None:
    db_path = str(tmp_path / "durable.sqlite")
    log1 = SqliteAuditLog(database_url=db_path)
    await log1.initialize()
    event = _event(workflow_id="wf-durable")
    await log1.append(event)

    # New instance against the same file simulates a process restart.
    log2 = SqliteAuditLog(database_url=db_path)
    # Re-initialize is idempotent on existing file.
    await log2.initialize()
    out = await log2.query_by_workflow("wf-durable")
    assert len(out) == 1
    assert out[0].event_id == event.event_id


@pytest.mark.asyncio
async def test_event_roundtrip_preserves_all_fields(tmp_db_path: str) -> None:
    log = SqliteAuditLog(database_url=tmp_db_path)
    await log.initialize()
    target = uuid4()
    event = AuditEvent(
        event_id=uuid4(),
        workflow_id="wf-x",
        workflow_type="DeployIntent",
        target_id=target,
        event_type=AuditEventType.ACTIVITY_COMPLETED,
        activity_name="apply_config",
        outcome="success",
        reason=None,
        payload={"detail": "ok", "count": 3},
        timestamp=datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC),
        actor="cli:anton",
    )
    await log.append(event)
    out = await log.query_by_workflow("wf-x")
    assert len(out) == 1
    roundtripped = out[0]
    assert roundtripped.event_id == event.event_id
    assert roundtripped.target_id == target
    assert roundtripped.event_type == AuditEventType.ACTIVITY_COMPLETED
    assert roundtripped.activity_name == "apply_config"
    assert roundtripped.outcome == "success"
    assert roundtripped.payload == {"detail": "ok", "count": 3}
    assert roundtripped.actor == "cli:anton"
    assert roundtripped.timestamp == event.timestamp


@pytest.mark.asyncio
async def test_query_returns_event_for_use_case_target(tmp_db_path: str) -> None:
    """target_id can be a use-case string, not just a UUID; round-trip preserves it."""
    log = SqliteAuditLog(database_url=tmp_db_path)
    await log.initialize()
    event = _event(target_id="dcfabric", workflow_id="scan-wf")
    await log.append(event)
    out = await log.query_by_workflow("scan-wf")
    assert len(out) == 1
    assert out[0].target_id == "dcfabric"


# ---------------------------------------------------------------------------
# ":memory:" mode (#60) — must behave like a real in-process store, not a
# fresh empty database per operation.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_mode_append_then_query() -> None:
    log = SqliteAuditLog(database_url=":memory:")
    await log.initialize()
    event = _event(workflow_id="wf-mem")
    await log.append(event)
    events = await log.query_by_workflow("wf-mem")
    assert [e.event_id for e in events] == [event.event_id]
    await log.close()


@pytest.mark.asyncio
async def test_memory_mode_persists_across_many_operations() -> None:
    log = SqliteAuditLog(database_url=":memory:")
    await log.initialize()
    for _ in range(5):
        await log.append(_event(workflow_id="wf-mem"))
    assert len(await log.query_by_workflow("wf-mem")) == 5
    assert len(await log.query_by_workflow("wf-mem")) == 5  # reads don't wipe it either
    await log.close()


@pytest.mark.asyncio
async def test_memory_mode_duplicate_event_id_is_stored_once() -> None:
    log = SqliteAuditLog(database_url=":memory:")
    await log.initialize()
    event = _event(workflow_id="wf-mem")
    await log.append(event)
    await log.append(event)
    assert [e.event_id for e in await log.query_by_workflow("wf-mem")] == [event.event_id]
    await log.close()


@pytest.mark.asyncio
async def test_memory_mode_requires_initialize() -> None:
    log = SqliteAuditLog(database_url=":memory:")
    with pytest.raises(AuditLogError, match="initialize"):
        await log.append(_event())


@pytest.mark.asyncio
async def test_close_is_safe_for_file_mode(tmp_db_path: str) -> None:
    log = SqliteAuditLog(database_url=tmp_db_path)
    await log.initialize()
    await log.append(_event(workflow_id="wf-file"))
    await log.close()  # no-op for file mode
    assert len(await log.query_by_workflow("wf-file")) == 1


# ---------------------------------------------------------------------------
# read_only=True (spec 010 FR-003, research R2): the dashboard opens the audit
# trail in a mode SQLite itself enforces, so no handler can write to it.
# ---------------------------------------------------------------------------


async def _seeded(db_path: str, *events: AuditEvent) -> None:
    writer = SqliteAuditLog(database_url=db_path)
    await writer.initialize()
    for event in events:
        await writer.append(event)


@pytest.mark.asyncio
async def test_read_only_returns_the_same_events_as_a_writable_instance(tmp_db_path: str) -> None:
    events = [_event(workflow_id="wf-ro") for _ in range(3)]
    await _seeded(tmp_db_path, *events)

    reader = SqliteAuditLog(database_url=tmp_db_path, read_only=True)
    await reader.initialize()

    assert [e.event_id for e in await reader.query_by_workflow("wf-ro")] == [e.event_id for e in events]


@pytest.mark.asyncio
async def test_read_only_refuses_a_missing_file_and_does_not_create_it(tmp_path: Path) -> None:
    db_path = tmp_path / "absent.sqlite"
    reader = SqliteAuditLog(database_url=str(db_path), read_only=True)

    with pytest.raises(AuditLogError, match="no audit log"):
        await reader.initialize()
    assert not db_path.exists()


@pytest.mark.asyncio
async def test_read_only_initialize_runs_no_ddl(tmp_path: Path) -> None:
    """A database without snapl's schema stays without it: no CREATE ran."""
    import sqlite3

    db_path = tmp_path / "foreign.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE other (x INTEGER)")
    conn.close()

    await SqliteAuditLog(database_url=str(db_path), read_only=True).initialize()

    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert tables == {"other"}


@pytest.mark.asyncio
async def test_read_only_refuses_append(tmp_db_path: str) -> None:
    await _seeded(tmp_db_path)
    reader = SqliteAuditLog(database_url=tmp_db_path, read_only=True)
    await reader.initialize()

    with pytest.raises(AuditLogError, match="audit log opened read-only"):
        await reader.append(_event())


@pytest.mark.asyncio
async def test_read_only_reader_sees_a_concurrent_writers_appends(tmp_db_path: str) -> None:
    writer = SqliteAuditLog(database_url=tmp_db_path)
    await writer.initialize()
    await writer.append(_event(workflow_id="wf-live"))
    reader = SqliteAuditLog(database_url=tmp_db_path, read_only=True)
    await reader.initialize()
    assert len(await reader.query_by_workflow("wf-live")) == 1

    await writer.append(_event(workflow_id="wf-live"))

    assert len(await reader.query_by_workflow("wf-live")) == 2


@pytest.mark.asyncio
async def test_read_only_opens_a_wal_database_whose_wal_and_shm_files_are_gone(tmp_db_path: str) -> None:
    """macOS keeps the -wal/-shm files when the last writer closes; Linux deletes
    them. Remove them by hand so this case runs on every platform."""
    import sqlite3

    event = _event(workflow_id="wf-closed")
    await _seeded(tmp_db_path, event)
    with sqlite3.connect(tmp_db_path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    for suffix in ("-wal", "-shm"):
        Path(tmp_db_path + suffix).unlink(missing_ok=True)

    reader = SqliteAuditLog(database_url=tmp_db_path, read_only=True)
    await reader.initialize()

    assert [e.event_id for e in await reader.query_by_workflow("wf-closed")] == [event.event_id]


def test_read_only_memory_database_is_refused() -> None:
    """A read-only in-memory log could never hold anything: refuse it up front."""
    with pytest.raises(ValueError, match="memory"):
        SqliteAuditLog(database_url=":memory:", read_only=True)
