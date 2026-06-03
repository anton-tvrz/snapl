# Research: NAF Observability — Drift Detection & Audit

**Feature**: 004-observability-drift
**Date**: 2026-05-14

## R1: Drift Comparison Strategy

**Decision**: Implement structural diff as a pure function `diff_desired_vs_actual(desired: DesiredState, actual: dict[str, Any]) -> list[DriftItem]`. The function walks each field of the `Device`, `Interface`, and `BGPSession` Pydantic models and compares value-by-value against the corresponding entry under known YANG paths in the actual collected dict. Comparison is value-equality on fields explicitly enumerated in a per-entity field map; fields absent from the map are not compared.

**Rationale**: A pure function is trivially unit-testable (no I/O, no clocks unless injected) and matches the spec's Assumption that comparison is structural rather than semantic. The Observer ABC then becomes a thin orchestration layer that calls this function and constructs a `DriftReport`. Walking the intent models field-by-field — rather than the collected dict — guarantees the comparison surface matches what the platform actually intends. The Executor renders the same intent fields into device payloads, so symmetry between render and diff is preserved.

**Per-entity comparison surface**:

| Entity | Compared fields | Path in collected dict |
|--------|----------------|------------------------|
| Interface | `name`, `description`, `ip_address`, `prefix_length`, `enabled`, `mtu` | `/interface[name=<name>]` |
| BGPSession | `peer_address`, `peer_asn`, `peer_group`, `enabled` | `/network-instance[name=default]/protocols/bgp/neighbor[peer-address=<peer>]` |
| Device | `description` | `/system` |

**Alternatives considered**:
- Walk the collected dict and look up corresponding intent fields: Inverts control — collector returns vendor-shaped data; intent models are the platform's stable contract. Walking intent first means new vendors with different YANG paths only need to extend the path map — rejected.
- Use `pydantic.BaseModel.model_dump()` and dict-diff the two dicts: Loses information about which fields are intentionally compared vs incidentally present in collected data; produces noisy reports for fields the platform doesn't manage — rejected.
- Use a third-party diff library (e.g., `deepdiff`): Heavy dependency for a small surface; we control both ends of the comparison and can hand-roll the field walk in <100 lines — rejected per constitution principle VII (Simplicity).

## R2: Drift Status Classification

**Decision**: Three-state `DriftStatus` enum: `CLEAN`, `DRIFTED`, `ERROR`. Resolution rules:
1. If the input `CollectResult.success == False` → status is `ERROR`, items list is empty, `error` field carries the collector's error string.
2. Otherwise, the diff function runs. If it returns zero items → status is `CLEAN`.
3. If it returns one or more items → status is `DRIFTED`.

**Rationale**: FR-002 requires exactly these three states. Centralising the rule in one place (the Observer's `detect_drift` method) keeps the diff function pure (returns items only) and the report assembly explicit. An empty desired state combined with empty actual state is `CLEAN` — a legitimate match. An empty desired state with non-empty actual state is `DRIFTED` only if the platform owns that path (per R1's field map); fields outside the map are ignored.

**Alternatives considered**:
- A fourth state `UNKNOWN` for partial collection failures: The Collector already classifies its own errors; replaying them as `UNKNOWN` adds a layer the caller doesn't need — `ERROR` with the verbatim collector error string is more useful. Rejected.
- Bool `is_drifted`: Loses the error-vs-clean distinction. Rejected.

## R3: Event Dispatch Model

**Decision**: Synchronous in-process `EventBus` with a register/emit interface. Handlers are plain callables (`Callable[[ObservabilityEvent], None]`). `emit` iterates handlers in registration order and isolates exceptions per-handler via `try/except` with `logging.warning(...)`; one handler raising does not block the others. Async handler integration is deferred to the Orchestrator (which wraps emit in a Temporal signal).

**Rationale**: FR-006 requires emit-with-no-handlers to succeed silently — a list comprehension with try/except per handler satisfies this in <20 lines. Synchronous dispatch keeps the Observability block free of `asyncio` ceremony for callers that just want to log an event. The Orchestrator block is the right home for durable async dispatch (Temporal signals already provide retry, persistence, and idempotency); duplicating that machinery here would violate Simplicity.

**Logging**: Handler exceptions are logged via the standard `logging` module at WARNING level with the handler's `__qualname__` and the exception message — enough context to identify a buggy handler without crashing the bus.

**Alternatives considered**:
- `asyncio.Queue` + background consumer task: Adds task lifecycle the caller has to manage (start/stop); only useful when the producer must not block — for in-process logging-style handlers, sync is fine. Rejected.
- External pub/sub broker (Redis, NATS): Drags in infrastructure for a problem that does not yet exist. Defer to Orchestrator. Rejected.
- `concurrent.futures` thread pool dispatch: Adds parallelism to handlers that are typically I/O-light; not justified at this scale. Rejected.

## R4: Audit Log Storage

**Decision**: Implement `AuditLog` as an in-memory class wrapping a `list[AuditEntry]`. Provide three methods: `append(entry: AuditEntry) -> None`, `query_by_device(device_id: UUID) -> list[AuditEntry]`, and `all() -> list[AuditEntry]`. Entries are returned as fresh `list` copies so callers cannot mutate the internal storage. `AuditEntry` itself is a frozen Pydantic model — immutable by construction.

**Rationale**: The Assumption section of the spec explicitly defers durable persistence to a future iteration. An in-memory list is the minimum that satisfies FR-007/8/9 and SC-004. Returning `list` copies enforces FR-008 (immutability) at the collection level too. Query by device uses a linear scan — acceptable at the prototype scale; the audit log is small (one entry per drift check, not per packet).

**Thread safety**: A `threading.Lock` guards `append()` and the read methods. The lock is internal and uncontested in the synchronous test path; the cost is one acquire per call. This protects against the future case where a multi-threaded Orchestrator activity logs concurrently, and is cheaper to add now than to retrofit.

**Alternatives considered**:
- SQLite-backed log: Adds a dependency and a file lifecycle for a feature explicitly deferred. Rejected.
- Append-only file (JSONL): Same dependency-vs-yagni argument; deferred to durable storage iteration. Rejected.
- `collections.deque`: Marginal append performance benefit irrelevant at this scale; `list` is the simpler primitive. Rejected.

## R5: Pydantic vs Dataclass for Models

**Decision**: Use Pydantic v2 `BaseModel` with `frozen=True` for all four observability models (`DriftItem`, `DriftReport`, `ObservabilityEvent`, `AuditEntry`). Match the snapl-intent style (Pydantic), not the snapl-collector style (frozen dataclass).

**Rationale**: Pydantic provides validation at construction, immutability via `frozen=True`, and JSON serialisation for free — useful when an `AuditEntry` is later persisted by the Orchestrator. The Collector chose dataclasses because its result types are constructed entirely by trusted internal code; the Observability models are constructed at the boundary between subsystems (a handler may receive an `ObservabilityEvent` from the bus and want to validate it), so validation pulls its weight. Pydantic is already a transitive dependency through snapl-intent — no new requirement.

**Alternatives considered**:
- Frozen dataclasses (matching Collector): No validation, no JSON. Rejected.
- TypedDict: No runtime enforcement of required fields. Rejected.
- Plain classes with `__slots__`: Hand-rolled what Pydantic gives for free. Rejected.

## R6: Batch Drift Detection Concurrency

**Decision**: `detect_drift_batch(pairs: list[tuple[DesiredState, CollectResult]]) -> BatchDriftReport` runs each comparison synchronously in a Python loop. No `asyncio.gather`, no thread pool. The method itself is `async` for API uniformity with the Collector/Executor batch methods, but the loop body is plain Python.

**Rationale**: A single drift comparison is pure CPU and completes in <100 ms (SC-001) — for 10 devices that is <1 s total in serial. The Collector's batch concurrency exists because each call is an I/O-bound gNMI request taking seconds; the Observability batch has no I/O. Adding `asyncio.to_thread` or `gather` here is overhead with no benefit and complicates testing. Keeping the method `async` preserves the cross-block API shape so the Orchestrator can `await` it like the others.

**Alternatives considered**:
- `asyncio.gather`: No I/O to overlap; pure overhead. Rejected.
- `concurrent.futures.ThreadPoolExecutor`: Same reasoning. Rejected.
- A synchronous (non-async) `detect_drift_batch`: Inconsistent with the rest of the platform's async surface; would force the Orchestrator to special-case Observability. Rejected.

## R7: Test Strategy — No Integration Tests

**Decision**: Ship unit tests only. No `tests/integration/test_observability/` directory. The block's inputs are `DesiredState` (constructible from snapl-intent fixtures) and `CollectResult` (constructible from snapl-collector fixtures); both are deterministic and synchronous to instantiate.

**Rationale**: The spec's SC-003 requires that all tests run with no infrastructure. The Observability block has no external service to integrate with — it consumes already-fetched data structures. Real end-to-end validation is a job for the Orchestrator block's tests, which will exercise Intent → Executor → Collector → Observability as a single flow. Adding empty integration scaffolding here would violate Simplicity.

**Alternatives considered**:
- Integration tests against the dcfabric Containerlab: Would require running the Collector first, which is the Collector block's responsibility; duplicates that block's integration coverage. Rejected.
- E2E tests against a stubbed handler: Belongs in the Orchestrator's E2E suite. Rejected.

## R8: Why Not Metrics Yet?

**Decision**: Do not ship Prometheus / OpenTelemetry exporters in this iteration. Drift counts and audit entry counts are queryable from the in-memory `AuditLog` and `DriftReport` objects directly.

**Rationale**: The spec lists metrics as an Observability concern but does not include a single user story or FR requiring metric export. Constitution principle VII forbids speculative features. When the Presentation block (or an external dashboard) needs metrics, an exporter sub-package can be added without touching the ABC. Adding it now with no consumer guarantees we get the metric names and labels wrong.

**Alternatives considered**:
- Prometheus `Counter` for drift events: No consumer, no scrape endpoint, no decision on label cardinality. Premature. Rejected.
- OpenTelemetry traces for drift detection: Diff completes in <100 ms; tracing overhead would dominate. Rejected.
