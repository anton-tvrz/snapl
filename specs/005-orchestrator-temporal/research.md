# Research: NAF Orchestrator — Temporal Workflows

**Feature**: 005-orchestrator-temporal
**Date**: 2026-05-21

## R1: Temporal Python SDK

**Decision**: Use the official `temporalio` Python SDK (>=1.7). Define workflows as classes with `@workflow.defn` and activities as functions with `@activity.defn`. Workflow methods are async; activity functions are async and call the existing async ABCs of the downstream blocks directly.

**Rationale**: `temporalio` is the canonical Temporal client for Python; the project constitution already names Temporal as the required workflow engine. The SDK's native async support aligns with the existing async ABCs of the Executor, Collector, and Observability blocks — activities can `await` the downstream calls without an `asyncio.to_thread` bridge.

**Workflow pattern**:
```python
@workflow.defn(name="DeployIntent")
class DeployIntentWorkflow:
    @workflow.run
    async def run(self, device_id: UUID) -> WorkflowResult:
        desired = await workflow.execute_activity(
            fetch_desired_state, device_id, start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        ...
```

**Alternatives considered**:
- Run workflows as plain `asyncio` tasks with a custom durable-event store: Reimplements Temporal poorly. Loses retry, replay, signal, query, cancellation, and event history — all of which the spec requires (FR-002, FR-011, FR-013). Rejected.
- A different workflow engine (Cadence, Prefect, Airflow): The constitution explicitly names Temporal as required tech. Rejected.

## R2: Workflow ID Strategy and Per-Device Serialization

**Decision**: Use deterministic workflow IDs scoped per-device for `DeployIntent`: `deploy-intent-{device_id}`. Use `WorkflowIDConflictPolicy.USE_EXISTING` when starting a workflow — concurrent callers receive the existing handle and await the same outcome rather than racing or being rejected. `ScanDrift` uses a UUID-suffixed ID per scan (`scan-drift-{use_case_id}-{uuid4}`) since scans are read-only and parallel scans are safe.

**Rationale**: FR-009 ("serialize concurrent `deploy_intent` for the same device — the second waits for the first to terminate rather than racing on the wire") maps cleanly to Temporal's `USE_EXISTING` policy: Temporal guarantees at most one workflow with a given ID is running at a time, and concurrent callers can join the in-flight execution and await its terminal state. This is Temporal-native and requires no external mutex/lock store.

After a workflow with ID `deploy-intent-{device_id}` terminates, a future caller with the same ID starts a fresh workflow (default reuse policy `ALLOW_DUPLICATE`) — natural per-device serialization with no extra coordination.

**ReconcileDevices** uses a per-call UUID workflow ID and internally invokes `DeployIntent` per device via child workflows, inheriting the per-device serialization through the deterministic child IDs.

**Alternatives considered**:
- External mutex (Redis, database row lock): Adds an extra dependency and a failure mode. Temporal's workflow-ID uniqueness already guarantees the property. Rejected.
- Reject concurrent callers with an error: Spec says "wait", not "reject". Rejected.
- Singleton per-device "guardian" workflow holding a semaphore: Reinvents what Temporal's workflow-ID semantics already provide. Rejected.

## R3: Audit Log Durability — Temporal History vs External Store

**Decision**: Dual-write the audit log. Each activity outcome and workflow lifecycle event is (a) implicitly captured in Temporal's own event history (durable in Temporal's backing store) and (b) explicitly appended to a SQLite-backed `AuditLog` projection via the `record_audit_event` activity. Cross-workflow queries (`by device ID`, `by time range` — FR-007) hit the SQLite projection; per-workflow detail can fall back to Temporal's history API.

**Rationale**:
- Temporal's history is naturally durable and per-workflow queryable, but cross-workflow queries (e.g., "all events for device X across all workflows") are not native — they require Search Attributes plus Elasticsearch, which is overkill for the prototype.
- A SQLite file-backed projection is durable, queryable by any indexed column, requires no extra service, and aligns with Principle VII (Simplicity).
- The `record_audit_event` activity is itself a Temporal activity, so its failures are retried and its successes are recorded in Temporal history — closing the loop on FR-006 ("workflow step is not considered complete until its audit event is durable").
- A future migration to Postgres / a column store is a sibling `AuditLog` implementation; the ABC keeps it swappable.

**SQLite schema** (see `audit/schema.sql`):
```sql
CREATE TABLE IF NOT EXISTS audit_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      TEXT NOT NULL UNIQUE,         -- UUID4
    workflow_id   TEXT NOT NULL,
    workflow_type TEXT NOT NULL,                -- DeployIntent / ScanDrift / ReconcileDevices
    target_id     TEXT,                          -- device UUID or use-case ID; nullable
    event_type    TEXT NOT NULL,                 -- workflow_started / activity_completed / ...
    activity_name TEXT,                          -- nullable for workflow-lifecycle events
    outcome       TEXT,                          -- success / failure / cancelled / null
    reason        TEXT,                          -- terminal reason code, null while running
    payload_json  TEXT NOT NULL,                 -- JSON blob with event-specific detail
    timestamp     TEXT NOT NULL                  -- ISO 8601 UTC
);
CREATE INDEX IF NOT EXISTS idx_audit_workflow_id ON audit_events(workflow_id);
CREATE INDEX IF NOT EXISTS idx_audit_target_id   ON audit_events(target_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp   ON audit_events(timestamp);
```

The table has no `UPDATE` or `DELETE` path exposed by the `AuditLog` ABC — append-only by contract (FR-008). Direct DB access bypasses the contract but is out of scope of the API.

**Alternatives considered**:
- Temporal Search Attributes + Elasticsearch only: Heavyweight for the prototype; Elasticsearch is not in the dev stack. Rejected for this iteration.
- Postgres for the projection: Heavier than SQLite for a prototype; SQLite is a file, requires no service, and is durable. Postgres is the right next step when the deploy story matures. Rejected for v1.
- Append the audit event from inside the workflow rather than via an activity: Workflows must be deterministic — file IO is forbidden. Activities are the correct durable side-effect surface. Rejected.
- Replace Observability's in-memory `AuditLog` rather than supplement it: Observability's in-process `AuditLog` is still useful for in-process consumers and for the existing Observer ABC's `log_audit()` method. The Orchestrator's durable log is the additional source of truth, not a replacement. Documented in spec FR-015.

## R4: Activity Design — One Activity per Downstream Block

**Decision**: Define one activity per downstream NAF block call:
- `fetch_desired_state(device_id) -> DesiredState` → wraps `IntentStore.get_desired_state()`
- `apply_config(desired_state) -> ApplyResult` → wraps `Executor.apply()`
- `collect_running_state(device, paths) -> CollectResult` → wraps `Collector.collect()` / `get_running_config()`
- `detect_drift(desired, collected) -> DriftReport` → wraps `Observer.detect_drift()`
- `record_audit_event(event) -> None` → appends to the durable `AuditLog`

Each activity is independently retryable with its own retry policy. The downstream-block instances (`InfrahubIntentStore`, `GnmiExecutor`, `GnmiCollector`, `StructuralObserver`, `SqliteAuditLog`) are constructed once in the worker entry point and held in a module-level `Activities` class that the activity functions resolve via dependency injection.

**Rationale**:
- One-activity-per-call gives Temporal the right retry granularity: a transient gNMI timeout retries only the `apply_config` activity, not the whole workflow.
- The activity boundary matches the NAF block boundary — drift in the contract of any downstream block surfaces as a clear activity signature change.
- Constructing concrete blocks once per worker (not per activity) avoids reconnect overhead while keeping the workflows themselves stateless.

**Retry policy defaults**:
| Activity | maximum_attempts | initial_interval | non_retryable |
|----------|------------------|------------------|---------------|
| `fetch_desired_state` | 3 | 2s | `IntentStoreNotFound`, `ValidationError` |
| `apply_config` | 3 | 5s | `ExecutorConfigError`, `ValidationError` |
| `collect_running_state` | 3 | 2s | `ValidationError` |
| `detect_drift` | 1 (no retry — pure compute) | n/a | `ValidationError` |
| `record_audit_event` | 5 | 1s | none — audit failure must be retried aggressively |

Non-retryable exception types are listed in `ActivityOptions.retry_policy.non_retryable_error_types` so programming errors fail fast rather than chewing through retries.

**Alternatives considered**:
- One mega-activity per workflow step combining apply+collect+verify: Loses retry granularity; a verify-time parse error would re-run the apply. Rejected.
- Per-call construction of downstream blocks: Reconnects every activity (gNMI, Infrahub) and explodes latency. Rejected.

## R5: Workflow Determinism and Test Strategy

**Decision**: Workflow code uses only:
- `workflow.execute_activity(...)` for IO
- `workflow.now()` for time (replay-safe)
- `workflow.logger` for logging (replay-safe)
- `await asyncio.gather()` for fan-out, but **only** awaiting other `execute_activity` calls or `execute_child_workflow` calls

No direct imports of `datetime.now`, `random`, `httpx`, `pygnmi`, file IO, or `asyncio.sleep`. The `temporalio.worker.Worker` validates this at runtime via the workflow sandbox; unit tests use the `WorkflowEnvironment` time-skipping environment, which surfaces non-determinism failures immediately.

**Unit tests**: `WorkflowEnvironment.start_time_skipping()` provides an in-process Temporal cluster with no Docker dependency. Activities are mocked at the function level so the workflow tests exercise the orchestration logic deterministically — no live gNMI, no live Infrahub, no live SQLite required.

**Integration tests**: Use a real Temporal dev cluster (Docker Compose under `development/`) with the real `SqliteAuditLog`, live SR Linux from Containerlab, and a live Infrahub instance.

**Rationale**: The `WorkflowEnvironment` approach gives high-fidelity workflow tests (real Temporal SDK, real workflow replay) at unit-test speed and without external dependencies — matches Principle III (TDD) and the project's "tests require no live infrastructure for unit tests" pattern from the Executor and Collector.

**Alternatives considered**:
- Mock the Temporal SDK entirely: Loses replay/determinism verification; tests would pass against broken workflow code that diverges on replay. Rejected.
- Skip workflow tests, test only activities: Workflows contain the composition logic the spec is fundamentally about. Untestable workflows would be a regression. Rejected.

## R6: Verification Step (FR-013) — Detecting Apply-That-Did-Not-Take

**Decision**: After `apply_config` succeeds, run `collect_running_state` against the same paths the apply touched, then run `detect_drift` between the desired state (which we already have) and the collected state. If `DriftReport.status == DRIFTED`, the workflow terminates with `WorkflowResult(success=False, reason="verification_failed")` and records the drift items in the audit event.

**Rationale**: This is the closed-loop verification that distinguishes the Orchestrator from "fire-and-forget" config push. The Observer ABC's `detect_drift()` already returns a structured `DriftReport` with item-level detail — exactly what's needed for the audit trail. No new comparison logic is required in the Orchestrator.

**Path selection for verification**: The `apply_config` activity returns the list of YANG paths that were written (from the `ApplyResult` shape exposed by the Executor); the verification collect uses those exact paths. This avoids fetching the full running config on every deploy and keeps the verification fast.

**Alternatives considered**:
- Verify by fetching the entire running config: Slow (root-path GET), unnecessary when we know which paths were touched. Rejected.
- Trust the apply result without verification: Defeats the loop. The spec mandates closed-loop verification (US1 acceptance + FR-013). Rejected.

## R7: Cancellation Semantics (FR-011)

**Decision**: Use Temporal's native cancellation. Workflows propagate cancellation to running activities via `ActivityCancellationType.WAIT_CANCELLATION_COMPLETED` (default). Activities check `activity.is_cancelled()` at safe points and raise `CancelledError` to release resources cleanly. Each workflow has a top-level `try/except CancelledError` that writes a `cancelled` audit event before returning `WorkflowResult(success=False, reason="cancelled")`.

**Rationale**: This is the idiomatic Temporal cancellation pattern. The "brief, bounded window for cleanup" required by FR-011 is the gap between cancellation signal and the next safe-cancel point; activities that hold gRPC connections can close them in a `finally` block before exit.

**Cleanup safety**: The downstream blocks (Executor, Collector) use per-call context managers for gNMI connections (research from 002/003), so there are no long-lived resources to leak on cancellation.

**Alternatives considered**:
- Forcible termination (TerminateWorkflow): Bypasses workflow code entirely, so no terminal audit event is written. Rejected — violates FR-006.
- Custom cancellation flag in workflow state: Reinvents Temporal's built-in primitive. Rejected.

## R8: SQLite Concurrency for the Audit Log

**Decision**: Use `aiosqlite` with WAL journal mode and a single shared write connection per worker process. Reads use short-lived connections per query. The `SqliteAuditLog` is constructed once and held by the worker; all `record_audit_event` activity calls funnel through the same async lock around the write connection.

**Rationale**: SQLite supports concurrent reads with a single writer; WAL mode reduces writer-reader contention. For a single worker process serving the Orchestrator, one shared write connection with an `asyncio.Lock` is enough. If a future deployment splits work across multiple workers, the writer model must be revisited (Postgres becomes the right choice at that point).

**Failure handling**: A SQLite write failure raises an exception from the `record_audit_event` activity; the activity's retry policy (max 5 attempts) retries the write. If all retries fail, the workflow surfaces the failure through its terminal `WorkflowResult` — consistent with FR-006.

**Alternatives considered**:
- One connection per write: Connection-open overhead is non-trivial in async contexts and unnecessary for the prototype scale. Rejected.
- No WAL (rollback journal): Higher writer-reader contention. Rejected.
- In-memory SQLite for tests: Useful for unit tests; integration tests use a real file-backed database to exercise the durability story. Both modes supported via the `SqliteAuditLog(database_url=...)` constructor.

## R9: Temporal Dev Cluster Bootstrap

**Decision**: Reuse the project's existing `development/` Docker Compose stack — add a Temporal service if not already present. The integration test fixture skips when the cluster is unreachable (same pattern as the Executor's `SRLINUX_HOST` skip fixture).

**Environment variables**:
| Variable | Default | Description |
|----------|---------|-------------|
| `TEMPORAL_HOST` | `localhost:7233` | Temporal frontend gRPC endpoint |
| `TEMPORAL_NAMESPACE` | `default` | Temporal namespace |
| `TEMPORAL_TASK_QUEUE` | `snapl-orchestrator` | Task queue for snapl workers |
| `SNAPL_AUDIT_DB` | `./snapl-audit.sqlite` | SQLite file path for the audit projection |

**Rationale**: Matches the existing pattern in this codebase: integration tests configure via env vars and skip when the target environment is not present. Aligns with the constitution's "must run locally on macOS/OrbStack" requirement.

**Alternatives considered**:
- Hosted Temporal Cloud: Out of scope; constitution forbids cloud-only dependencies. Rejected.
- Embed Temporal as a library: Not supported — Temporal is a service. Rejected.

## R10: Out-of-Scope Decisions (Documented for Future Iterations)

The following are explicitly deferred (per spec Assumptions) and are not researched in depth here:

- **Scheduling / event-driven triggers**: Temporal supports Schedules natively; a future spec will adopt them.
- **Auth/authz on workflow invocation**: Owned by the future Presentation block.
- **Workflow versioning / patching**: Temporal supports `workflow.patched()` and version markers; required only when we change a deployed workflow's shape mid-flight, which is not a concern for v1.
- **Multi-region / HA Temporal cluster**: Single local cluster is sufficient for the prototype.
- **Postgres-backed audit log**: Sibling implementation under `audit/` when single-worker SQLite hits its scale ceiling.
- **Long-term audit archive**: Out of scope; the SQLite file is the source of truth for the prototype.
