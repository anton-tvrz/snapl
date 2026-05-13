# Research: NAF Executor — gNMI Config Deployment

**Feature**: 002-executor-gnmi
**Date**: 2026-05-07

## R1: pygnmi Sync→Async Strategy

**Decision**: Wrap all `gNMIclient` calls with `asyncio.to_thread()` inside async executor methods so the ABC surface is `async def apply(...)` while pygnmi remains synchronous internally.

**Rationale**: pygnmi is a synchronous library (it wraps gRPC blocking stubs). The `Executor` ABC must be async to compose cleanly with Temporal activities and other async NAF blocks. `asyncio.to_thread` runs the blocking call on a thread-pool thread, returning an awaitable — the simplest correct bridge without introducing a secondary async gRPC library. The alternative (`grpcio-asyncio` directly) would require reimplementing the gNMI wire protocol beyond what pygnmi provides.

**Alternatives considered**:
- `asyncio.get_event_loop().run_in_executor(None, ...)`: Identical semantics, but `asyncio.to_thread` is the Python 3.9+ idiomatic form — preferred.
- Switch to a native async gNMI library (e.g., `gnmi-py`): Fewer users, less mature, would require a new learning curve — rejected for prototype.
- Keep `Executor` ABC synchronous: Would block the event loop when called from Temporal activities and other async callers — rejected.

**Implementation pattern**:
```python
async def apply(self, desired: DesiredState) -> ApplyResult:
    payload = self._renderer.render(desired)
    return await asyncio.to_thread(self._gnmi_set, payload)
```

## R2: Template Architecture

**Decision**: Per-entity-type Jinja2 templates, organised into per-use-case directories. Each template renders one entity type (device system config, interface config, BGP config) and is composed into a single payload per device apply.

**Rationale**: The srlinux-gnmi skill and the gNMI SET pattern (one structured JSON payload per SET call) suggests composing the full device config as one object. Organising templates per entity type makes them independently testable and reusable across use cases that share entity types (e.g., interfaces look the same for dcfabric and test_edge). Per-use-case directories scope templates that have use-case-specific fields.

**Template structure**:
```text
packages/executor/snapl_executor/templates/
└── dcfabric/
    ├── interfaces.j2     # renders Interface list into SR Linux JSON
    ├── bgp.j2            # renders BGP sessions into SR Linux JSON
    └── system.j2         # renders device-level config (loopback, hostname)
```

**Alternatives considered**:
- One monolithic template per use case: Hard to test sections independently, difficult to extend — rejected.
- Templates outside the package (configuration directory): Breaks self-contained packaging, harder to distribute — rejected.
- Per-device-role templates (spine.j2, leaf.j2): Premature specialisation — most config is role-agnostic; role differences are handled by data, not template branching — rejected.

## R3: SR Linux YANG Path Inventory

**Decision**: Use the root `/` path for gNMI SET with a merged YANG JSON document covering all entity types in one call per device.

**Rationale**: pygnmi's `gc.set(update=[(path, json)])` accepts a root `/` path with a complete SR Linux JSON payload. The SR Linux YANG model organises config under top-level keys: `interface`, `network-instance[name=default]/protocols/bgp`, `system`. Rendering all entities into one document and SETting once is simpler and more atomic than multiple SETs.

**Key paths** (from srlinux-gnmi skill):
- Interfaces: `interface[name=<N>]/subinterface[index=0]/ipv4/address`
- BGP sessions: `network-instance[name=default]/protocols/bgp/neighbor`
- System/loopback: `interface[name=lo0]/subinterface[index=0]/ipv4/address`
- BGP global ASN: `network-instance[name=default]/protocols/bgp/autonomous-system`

**Alternatives considered**:
- Multiple targeted SETs (one per entity type): Partial-apply risk; harder to rollback atomically — rejected.
- YANG replace (vs update): SR Linux `replace` replaces the entire subtree; `update` merges — `update` is safer for incremental desired-state apply — use `update` as default.

## R4: Connection Lifecycle

**Decision**: Use `gNMIclient` as a context manager (per-call, not per-device session). Each `apply()` opens, uses, and closes one gRPC connection.

**Rationale**: For a stateless Executor in a Temporal activity context, long-lived connections would complicate error recovery and session resumption. A per-call context manager is simpler, matches pygnmi's documented pattern, and is adequate for prototype throughput (one call per device per workflow step). The 30s timeout applies to the entire call (connect + set).

**Alternatives considered**:
- Shared persistent connection pool: Adds lifecycle management complexity without meaningful throughput benefit at prototype scale — rejected.
- Connection per batch (reuse across devices): Requires per-device host in the connection — not possible with one client — rejected; each `GnmiExecutor` instance is scoped to one device.

## R5: Result Objects vs Raised Exceptions

**Decision**: `apply()`, `rollback()`, and `dry_run()` return result objects (`ApplyResult`, `DryRunResult`) for all device-side outcomes. Python exceptions are reserved for programming errors (invalid arguments, broken internal state). This diverges from IntentStore, which raises domain exceptions for device/schema errors.

**Rationale**: The Executor is closer to the "network operation" layer than the "data query" layer. In batch apply (US4), one device's failure must not abort the entire operation — exception semantics would require a try/except per device anyway. Returning a result object makes success/failure a value, composable with Temporal's retry and signal model. The divergence from IntentStore is intentional: IntentStore raises because "not found" is always a caller-side mistake; a device being unreachable is a runtime condition, not a mistake.

**Documented divergence** — to be noted in contracts/executor.md and communicated to Orchestrator callers.

**Exceptions still raised for**: missing required constructor arguments, programming errors in template rendering (syntax errors in j2 files = fatal, not runtime-recoverable).

## R6: Containerlab Integration Test Setup

**Decision**: Integration tests target a running Containerlab SR Linux node, launched separately from the test suite. Tests skip if no node is reachable (mirrors the Infrahub skip pattern from 001).

**Rationale**: The predecessor (quattro) used this exact pattern — test fixtures probe the device address/port before running, skip gracefully if unavailable. The Containerlab topology already exists in `containerlab/`. Integration tests will use environment variables (`SRLINUX_HOST`, `SRLINUX_PORT`, `SRLINUX_USERNAME`, `SRLINUX_PASSWORD`) with sensible Containerlab defaults.

**Containerlab defaults** (from lab topology):
- Host: `clab-dcfabric-spine-01` (Containerlab DNS) or `localhost`
- gNMI port: per-node (Containerlab assigns unique ports; `57400` is the SR Linux default)
- Username: `admin`
- Password: from Containerlab deployment (dev token, allowlisted in gitleaks)

**Alternatives considered**:
- Mock gNMI for all tests: No integration fidelity — the unit tests already mock; at least one integration test must touch a real node — rejected as the only mode.
- Spin up SR Linux in CI: Containerlab + Docker-in-Docker adds CI complexity — skipped for prototype; integration tests run locally only.
