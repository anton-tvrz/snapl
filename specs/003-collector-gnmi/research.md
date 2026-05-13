# Research: NAF Collector — gNMI Live Data Retrieval

**Feature**: 003-collector-gnmi
**Date**: 2026-05-13

## R1: pygnmi GET API

**Decision**: Use `gc.get(path=["/"])` for full running config retrieval and `gc.get(path=[path1, path2, ...])` for targeted YANG path collection. Wrap the synchronous call with `asyncio.to_thread()` — identical bridge pattern to the Executor.

**Rationale**: pygnmi exposes `gNMIclient.get(path=..., datatype="all")` which maps directly to a gNMI GET RPC. The `path` argument is a list of YANG path strings. For a full config read, the root path `"/"` retrieves the complete device configuration tree as one structured JSON response. For targeted reads, a list of specific paths returns only those subtrees — more efficient for high-frequency polling. The response is a dict with a `"notification"` key containing the path-value pairs.

**pygnmi GET return structure**:
```python
{
    "notification": [
        {
            "timestamp": 1234567890,
            "update": [
                {"path": "/interface[name=ethernet-1/1]", "val": {...}},
                ...
            ]
        }
    ]
}
```

**Implementation pattern**:
```python
async def collect(self, device: Device, paths: list[str]) -> CollectResult:
    return await asyncio.to_thread(self._blocking_get, paths)

def _blocking_get(self, paths: list[str]) -> CollectResult:
    with gNMIclient(...) as gc:
        response = gc.get(path=paths, datatype="all", encoding="json_ietf")
        return self._parse_response(response, paths)
```

**Alternatives considered**:
- gNMI SUBSCRIBE (stream): More complex, stateful — unnecessary for a one-shot read; subscribe is the pattern for the Observability block, not the Collector — rejected for this feature.
- Multiple separate GET calls (one per path): Adds round-trip overhead; a single GET with a path list is more efficient and atomically consistent — rejected.
- `asyncio.get_event_loop().run_in_executor(None, ...)`: Identical semantics to `asyncio.to_thread`; `to_thread` is the Python 3.9+ idiomatic form — rejected.

## R2: Response Parsing Strategy

**Decision**: Extract the `update` entries from the `"notification"` list and build a `dict[str, Any]` keyed by the normalised YANG path. Return the full parsed dict as `CollectResult.data`. An empty `update` list (device path exists but has no entries) returns an empty dict with `success=True`.

**Rationale**: The spec (FR-009) requires collected data as Python dicts parseable directly against the Executor's rendered payloads. The Executor renders YANG-modelled JSON documents; the Collector should return the same shape so the Observability module can diff them without transformation. Keying by path aligns `CollectResult.data` with the `paths` list passed to `collect()`.

**Parse error handling**: If `gc.get()` returns a response that cannot be walked as expected (missing `"notification"` key, non-dict `"val"` entries), the Collector catches the `KeyError`/`TypeError` and returns `CollectResult(success=False, error="parse error: ...")` — it never propagates a raw exception to the caller.

**Root path special case**: `get_running_config()` calls `collect(device, paths=["/"])`. The response is a single notification at path `/` with the full JSON tree as `val`. The Collector stores this as `data = {"/": full_response_dict}`.

**Alternatives considered**:
- Return the raw pygnmi response dict unchanged: Too coupled to pygnmi internals — callers would need to know the `notification[].update[]` structure. Collector should normalise to a clean contract — rejected.
- Return a list of `(path, value)` tuples: Less ergonomic for drift comparison than a dict; dict lookup by path is O(1) vs O(n) scan — rejected.

## R3: Connection Lifecycle

**Decision**: Use `gNMIclient` as a per-call context manager (same pattern as the Executor). Each `collect()` / `get_running_config()` call opens, reads, and closes one gRPC connection. The GnmiCollector holds connection parameters (host, port, credentials, TLS, timeout) but no live connection state.

**Rationale**: Stateless per-call connections simplify error recovery — a failed GET has no dangling connection state. For the Collector's read-only use case, connection overhead is acceptable; a full config read is inherently slow (network round-trip) so a single connection-open overhead is negligible. Mirrors the Executor's established pattern for consistency across NAF blocks.

**Alternatives considered**:
- Long-lived persistent connection: Adds reconnection/keep-alive complexity, unclear benefit at prototype scale — rejected.
- Connection pool per host: Premature for single-device polling; adds lifecycle management complexity — rejected.

## R4: Timeout and Error Classification

**Decision**: Apply a single per-call timeout (default: 30 seconds) via pygnmi's `timeout` parameter in the `gNMIclient` constructor. Classify errors into three categories returned in `CollectResult.error`:
1. **Connectivity** (`OSError`, `grpc.RpcError` with `UNAVAILABLE`): `"connectivity error: <detail>"`
2. **Auth** (`grpc.RpcError` with `UNAUTHENTICATED`): `"auth error: <detail>"`
3. **Timeout** (`grpc.RpcError` with `DEADLINE_EXCEEDED`, or `asyncio.TimeoutError`): `"timeout after <N>s"`
4. **Parse** (`KeyError`, `TypeError`, `json.JSONDecodeError`): `"parse error: <detail>"`

**Rationale**: FR-004 and FR-006 require all gNMI errors to be returned as structured results. Classifying errors into known categories lets callers (Orchestrator, Observability) branch on error type without string parsing. The Executor uses the same classification pattern.

**Alternatives considered**:
- Single generic `"error"` field with raw exception message: Sufficient for logging but insufficient for programmatic error routing — rejected.
- Raise domain exceptions for each error type: Requires callers to try/except; incompatible with batch collect's "one failure must not abort others" requirement — rejected.

## R5: Batch Collect Concurrency

**Decision**: Use `asyncio.gather()` with `return_exceptions=False` wrapped in per-device `try/except`, matching the Executor's `apply_batch()` pattern. Each device GET is wrapped as `asyncio.to_thread()` call inside an async helper; all helpers are gathered concurrently.

**Rationale**: FR-003 requires concurrent multi-device collection. `asyncio.gather()` with per-device exception handling ensures one device failure doesn't abort the batch. SC-004 requires 12 devices within 2 minutes — concurrent GETs rather than serial are required to meet this goal. Thread pool parallelism from `asyncio.to_thread` allows simultaneous blocking gRPC calls.

**Alternatives considered**:
- `asyncio.gather(*tasks, return_exceptions=True)`: Would collect exceptions as values, requiring isinstance-checks on results — less clean than per-device try/except — rejected.
- Sequential per-device collect: Would take ~30s × 12 devices = 6 minutes at worst case — violates SC-004 — rejected.
- `concurrent.futures.ThreadPoolExecutor` directly: `asyncio.to_thread` already uses the default thread pool; no need for an explicit executor — rejected.

## R6: Containerlab Integration Test Setup

**Decision**: Reuse the same env var and skip-fixture pattern from 002-executor-gnmi (`SRLINUX_HOST`, `SRLINUX_PORT`, `SRLINUX_USERNAME`, `SRLINUX_PASSWORD`). Integration tests for the Collector live under `tests/integration/test_collector/` with a local `conftest.py` providing the skip fixture.

**Rationale**: The Containerlab dcfabric topology (2 spines, 4 leaves, all gNMI port 57400) is already in the repository. The same lab that is used for Executor integration tests can be reused for Collector integration tests — the Collector reads what the Executor writes. Reusing the same env vars avoids configuration drift between NAF blocks.

**Key YANG paths for integration tests** (mirror the Executor's write paths):
- Full config: `"/"`
- Interfaces: `"/interface"`
- BGP neighbors: `"/network-instance[name=default]/protocols/bgp/neighbor"`

**Alternatives considered**:
- Separate env vars for Collector tests (e.g., `SRLINUX_COLLECTOR_HOST`): Adds indirection for no benefit — rejected.
- Mock-only integration tests: No integration fidelity; real gNMI GET behaviour (response shape, encoding) is the point of integration tests — rejected.
