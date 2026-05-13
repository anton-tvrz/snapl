# Feature Specification: NAF Collector — gNMI Live Data Retrieval

**Feature Branch**: `003-collector-gnmi`
**Created**: 2026-05-13
**Status**: Draft
**Input**: User description: "NAF Collector gNMI live data retrieval"

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Retrieve the Running Configuration of a Device (Priority: P1)

An operator or automated workflow needs to read the complete running configuration of a network device — what the device is actually doing right now — so it can be compared against the intended state, stored for audit, or fed into downstream analysis. The Collector retrieves the full device configuration in structured form over gNMI and returns it to the caller.

**Why this priority**: Retrieving the running config is the Collector's core function. Without it, there is no basis for drift detection, compliance checking, or audit logging. Every other Collector capability builds on this read path working correctly.

**Independent Test**: Can be tested by calling `get_running_config(device)` against a mock gNMI client and verifying that a `CollectResult` is returned with structured data at the root path. Delivers immediate value as the read counterpart to the Executor's apply, completing the write-read loop.

**Acceptance Scenarios**:

1. **Given** a reachable SR Linux device, **When** `get_running_config(device)` is called, **Then** the Collector issues a gNMI GET at the root path, returns a `CollectResult` with `success=True` and the device's full configuration as structured data.
2. **Given** a device that is unreachable, **When** `get_running_config(device)` is called, **Then** the Collector returns a `CollectResult` with `success=False` and a clear connectivity error — it does not hang and does not raise an exception to the caller.
3. **Given** a device that returns an authentication error, **When** `get_running_config(device)` is called, **Then** the Collector returns a `CollectResult` with `success=False` and an error message identifying the auth failure.

---

### User Story 2 — Collect Specific Configuration Paths (Priority: P2)

The Observability module (or an operator) needs to read only a specific slice of device state — for example, all BGP neighbor states or all interface operational counters — without fetching the entire running configuration. The Collector accepts a list of YANG paths and returns the data at those paths.

**Why this priority**: Targeted path collection is more efficient than full-config retrieval for high-frequency polling. It is the primary data access pattern for the Observability block, which monitors specific attributes rather than the full configuration tree.

**Independent Test**: Can be tested without live infrastructure by calling `collect(device, paths=["/interface", "/network-instance[name=default]/protocols/bgp/neighbor"])` against a mock gNMI client and verifying the returned `CollectResult` contains data keyed by the requested paths.

**Acceptance Scenarios**:

1. **Given** a reachable device and a list of valid YANG paths, **When** `collect(device, paths)` is called, **Then** the Collector issues a gNMI GET for the given paths and returns a `CollectResult` with `success=True` and the data for each requested path.
2. **Given** a valid device and a path that does not exist on the device, **When** `collect(device, paths)` is called, **Then** the Collector returns a `CollectResult` indicating which paths returned data and which produced errors — partial results are not discarded.
3. **Given** an empty path list, **When** `collect(device, paths=[])` is called, **Then** the Collector returns a validation error rather than issuing a gNMI GET with no paths.

---

### User Story 3 — Collect from Multiple Devices (Priority: P3)

A drift-detection workflow or fabric-wide audit needs the running state from all devices in a use case at the same time. The Collector accepts a list of devices (and optionally paths) and returns per-device results. A failure on one device does not prevent the operation from completing on others.

**Why this priority**: Batch collection mirrors the Executor's `apply_batch()` pattern and is the primary mode for fabric-scale operations. It is lower priority than single-device retrieval because the single-device path must be correct first, and the Orchestrator will coordinate at-scale workflows in the broader NAF loop.

**Independent Test**: Can be tested by calling `collect_batch(devices, paths)` with three mock devices, verifying three gNMI GET calls are made, and confirming the result contains a per-device `CollectResult` for each.

**Acceptance Scenarios**:

1. **Given** three devices in a datacenter fabric, **When** `collect_batch(devices, paths)` is called, **Then** the Collector retrieves data from all three concurrently and returns a `BatchCollectResult` with a per-device result for each.
2. **Given** a batch of three devices where one is unreachable, **When** `collect_batch(devices, paths)` is called, **Then** the Collector successfully collects from the two reachable devices, records a connectivity error for the unreachable one, and returns all three results without raising an exception.
3. **Given** a `BatchCollectResult`, **When** inspected, **Then** each entry identifies the device, its outcome (success or failure), and the collected data or error detail.

---

### Edge Cases

- What happens when gNMI GET returns an empty data set (device path exists but has no entries)? The Collector must return an empty but successful result — no error for legitimately empty paths.
- What happens when the gNMI response cannot be parsed as valid structured data? The Collector must return a parse error in the result rather than propagating a raw exception.
- What happens when the same device appears twice in a batch collect? The Collector must return a validation error rather than issuing duplicate requests.
- What happens when a gNMI GET times out mid-response? The Collector must return a timeout error and not deliver partial data as a success.
- What happens when a very large configuration tree is returned? The Collector must not silently truncate data — the full response or a clear error must be returned.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST retrieve the complete running configuration of a network device via gNMI GET at the root path and return it as structured data in a `CollectResult`.
- **FR-002**: System MUST support targeted collection at one or more caller-supplied YANG paths via gNMI GET, returning data keyed by path in a `CollectResult`.
- **FR-003**: System MUST support batch collection across multiple devices, returning per-device `CollectResult` in a `BatchCollectResult`. A failure on one device MUST NOT prevent completion on others.
- **FR-004**: System MUST return structured error results — not raised exceptions — for device connectivity failures, authentication failures, parse errors, and timeout events. Exceptions are reserved for programming errors (invalid arguments, broken internal state).
- **FR-005**: System MUST expose a `GnmiCollector` (concrete implementation of the `Collector` ABC) as its primary entry point, accepting device address, port, and credentials.
- **FR-006**: System MUST time out gNMI GET operations that do not complete within a configurable deadline (default: 30 seconds). Timed-out operations MUST return a timeout error result, not hang indefinitely.
- **FR-007**: System MUST be independently testable without live network infrastructure — unit tests require no physical or virtual devices; integration tests require a running lab node.
- **FR-008**: System MUST consume the `Device` model from `snapl_intent` as the device descriptor — the Collector does not define its own device model.
- **FR-009**: Collected data MUST be returned as Python dicts (parsed from gNMI's structured JSON encoding) so callers can compare directly with the Executor's rendered payloads and the Intent module's desired state.
- **FR-010**: An empty path list MUST be rejected with a validation error before any gNMI connection is attempted.

### Key Entities

- **CollectResult**: The outcome of a single `collect()` or `get_running_config()` call. Carries the device identifier, success/failure status, the collected data (a dict keyed by YANG path), the paths requested, a wall-clock duration, a timestamp, and an error message if applicable.
- **BatchCollectResult**: The aggregated outcome of a batch collect. A map from device identifier to `CollectResult`, with a top-level summary (total devices, successes, failures).
- **Collector ABC**: The abstract interface shared by all NAF Collector implementations. Defines `collect(device, paths) -> CollectResult` and `get_running_config(device) -> CollectResult`.
- **GnmiCollector**: The concrete Nokia SR Linux implementation of the `Collector` ABC. Holds device connection parameters (host, port, credentials, TLS settings) and uses pygnmi for transport.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A single-device `get_running_config()` call completes (success or failure result returned) within 30 seconds for any reachable device.
- **SC-002**: A targeted `collect()` for a single YANG path completes in under 5 seconds on a reachable device.
- **SC-003**: 100% of gNMI errors (connectivity, auth, parse, timeout) are returned as structured `CollectResult` failures — no exceptions propagate to the caller.
- **SC-004**: A `collect_batch()` across 12 devices (a full spine-leaf fabric) completes within 2 minutes.
- **SC-005**: Unit test suite achieves ≥80% line coverage without requiring a live device or gNMI infrastructure.
- **SC-006**: Collected data format is directly comparable to the Executor's rendered payloads — the Observability module can diff them without additional transformation.

## Assumptions

- Nokia SR Linux via Containerlab is the only prototyping target. The architecture supports additional vendors through the driver pattern (new concrete `Collector` implementations), but no other vendor is in scope.
- gNMI is the only device interface in this feature. NETCONF and SSH are explicitly out of scope.
- The Collector consumes the `Device` model from `snapl_intent` — it does not implement its own device inventory.
- Credentials (username, password) are injected at `GnmiCollector` construction time. Credential storage and rotation are out of scope.
- TLS verification is optional (lab environments use `insecure=True`); production hardening of TLS is out of scope.
- The Collector is read-only and stateless — it does not persist collected data. Persistence is the responsibility of the Observability block.
- The Collector does not interpret or transform the data it returns. Raw YANG-modelled JSON from the device is returned as-is so the Observability module can apply its own diffing and comparison logic.
- pygnmi is used for gNMI transport, consistent with the Executor. The same asyncio bridge pattern applies (`asyncio.to_thread` for blocking pygnmi calls).
- The Containerlab SR Linux lab topology already exists in the repository. Integration tests will reference it.
