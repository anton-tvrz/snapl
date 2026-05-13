# Feature Specification: NAF Executor — gNMI Config Deployment

**Feature Branch**: `002-executor-gnmi`
**Created**: 2026-05-07
**Status**: Draft
**Input**: User description: "gNMI-based config deployment for Nokia SR Linux — NAF Executor building block"

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Deploy Desired State to a Device (Priority: P1)

An automated workflow or network operator has retrieved the desired state for a network device from the Intent module and now needs to push that configuration onto the physical (or virtual) device. The Executor takes the structured desired state, renders it into the correct device-native format, and applies it via gNMI. The calling system receives a result indicating success or failure, with enough detail to act on it.

**Why this priority**: Applying configuration is the core function of the Executor — without it, no other capability has meaning. Every other story builds on knowing that a successful apply works end-to-end.

**Independent Test**: Can be tested by providing a pre-built desired state (two interfaces, one BGP session), calling `apply()`, and verifying the gNMI SET request contains the correct structured payload matching the desired state. Delivers immediate value as the primary integration point between Intent and the live network.

**Acceptance Scenarios**:

1. **Given** a desired state with interface assignments and BGP peering for a single SR Linux device, **When** `apply()` is called, **Then** the Executor issues a gNMI SET request carrying the rendered configuration in SR Linux YANG-modelled JSON format and returns a success result.
2. **Given** a device that is unreachable (wrong address, gNMI port closed), **When** `apply()` is called, **Then** the Executor returns a clear connection error — it does not hang and does not silently succeed.
3. **Given** a gNMI SET that is rejected by the device (e.g., schema validation error from the device), **When** `apply()` is called, **Then** the Executor returns a structured failure result carrying the device's error response.

---

### User Story 2 — Validate a Deployment Without Applying (Priority: P2)

Before pushing configuration to a production network, an operator or orchestration workflow needs to validate that the rendered configuration is syntactically correct and structurally consistent — without changing anything on the device. The Executor provides a dry-run mode that renders the configuration and reports what would be applied, optionally verifying the payload against the device without committing it.

**Why this priority**: Dry-run is a safety gate that enables automation with human verification. It is the second most critical capability because it unlocks safe adoption of the Executor in controlled environments before full automation.

**Independent Test**: Can be tested without a live device by calling `dry_run()` with a valid desired state and verifying the returned object contains the rendered configuration payload and a "would apply" status rather than a committed result.

**Acceptance Scenarios**:

1. **Given** a desired state for a device, **When** `dry_run()` is called, **Then** the Executor renders the configuration, returns the rendered payload for inspection, and makes no changes to the device.
2. **Given** a desired state with a field that cannot be rendered (e.g., a missing required template variable), **When** `dry_run()` is called, **Then** the Executor returns a render error identifying the specific missing or invalid field, without attempting a gNMI connection.
3. **Given** a desired state that produces a well-formed payload, **When** `dry_run()` is called, **Then** the returned result clearly indicates this was a dry run (not a committed change) alongside the rendered configuration.

---

### User Story 3 — Roll Back a Failed Deployment (Priority: P3)

If an `apply()` partially succeeds — for example, interfaces are configured but the BGP session fails — an operator or orchestration workflow needs to restore the device to a known-good state. The Executor accepts a rollback configuration (typically the last known-good desired state) and applies it, overwriting the partial change.

**Why this priority**: Rollback is essential for safe automation but is implemented as a targeted re-apply to the prior known-good state. It is lower priority because the basic apply/dry-run loop must be solid first, and because Temporal-based orchestration (a later NAF block) is the primary recovery coordinator.

**Independent Test**: Can be tested by applying a desired state, then calling `rollback()` with an alternate desired state, and verifying the gNMI SET payload corresponds to the rollback configuration, not the failed one.

**Acceptance Scenarios**:

1. **Given** a prior known-good desired state, **When** `rollback()` is called after a failed apply, **Then** the Executor issues a gNMI SET with the rollback configuration and returns a result indicating whether the rollback succeeded.
2. **Given** a device that is unreachable at rollback time, **When** `rollback()` is called, **Then** the Executor returns a connectivity error and does not silently succeed or hang.
3. **Given** a successful rollback, **When** the result is inspected, **Then** it clearly indicates the rollback was applied (not a normal apply) so the caller can distinguish the two in audit logs.

---

### User Story 4 — Deploy to Multiple Devices in One Operation (Priority: P4)

A datacenter fabric bring-up or use-case-wide reconfiguration event requires applying desired state to all devices in a use case simultaneously. The Executor accepts a list of desired states and applies them, reporting per-device results. Failures on individual devices do not prevent the operation from completing on other devices.

**Why this priority**: Multi-device apply is the operational pattern for any real fabric workflow. It is lower priority because the single-device apply must be solid first, and because the Orchestrator (a later NAF block) is the primary multi-device coordinator — the Executor's batch capability is a convenience layer.

**Independent Test**: Can be tested by calling `apply_batch()` (or equivalent) with a list of three desired states, verifying three gNMI SET requests are made, and confirming the result map contains a per-device outcome for each.

**Acceptance Scenarios**:

1. **Given** desired state for three devices in a datacenter fabric, **When** a batch apply is requested, **Then** the Executor applies configuration to all three and returns a per-device result map.
2. **Given** a batch of three devices where one is unreachable, **When** a batch apply is requested, **Then** the Executor applies successfully to the two reachable devices, records a connectivity failure for the unreachable one, and returns all three results without raising an exception.
3. **Given** a batch apply result, **When** inspected, **Then** each entry identifies the device, the outcome (success or failure), and enough detail to diagnose any failure.

---

### Edge Cases

- What happens when the gNMI connection succeeds but the SET response indicates a partial write? The Executor must surface the device's partial-success response as a failure — no silent partial commits.
- What happens when a Jinja2 template references a variable not present in the desired state? `dry_run()` must catch this at render time; `apply()` must not attempt a gNMI connection with an incomplete payload.
- What happens when the same device appears twice in a batch apply? The Executor must apply once (deduplicate) or return an error for the duplicate entry.
- What happens when gNMI credentials are wrong? The Executor must return an authentication error, not a generic connection error.
- What happens when the rendered payload exceeds the device's gNMI message size limit? The Executor must surface the device's error rather than silently truncating.
- What happens when rollback is called with a state that is identical to the current device state? The Executor applies it idempotently — the device is in the correct state either way, and the result reports success.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST apply the complete desired state for a network device via gNMI SET, rendering the configuration from structured desired state using Jinja2 templates before transmission.
- **FR-002**: System MUST support a dry-run mode that renders the configuration and returns the payload without issuing a gNMI SET. Dry-run MUST catch template render errors before any network connection is attempted.
- **FR-003**: System MUST support rollback by re-applying a prior desired state via gNMI SET. Rollback results MUST be distinguishable from normal apply results in the returned data.
- **FR-004**: System MUST support batch apply across multiple devices, returning per-device results. A failure on one device MUST NOT prevent the operation from completing on other devices.
- **FR-005**: System MUST return structured error results — not raised exceptions — for device connectivity failures, authentication failures, render errors, and device-side validation errors. Exceptions are reserved for programming errors (invalid arguments, broken internal state).
- **FR-006**: System MUST expose a `GnmiExecutor` (concrete implementation of the `Executor` ABC) as its primary entry point, accepting device address, port, and credentials alongside the desired state.
- **FR-007**: System MUST use SR Linux YANG-modelled JSON as the gNMI payload format. CLI-style commands are not acceptable.
- **FR-008**: System MUST be independently testable without live network infrastructure — unit tests require no physical or virtual devices; integration tests require a running lab node.
- **FR-009**: Config templates MUST be maintainable as structured text files packaged with the module. Template selection is driven by the desired state's use case and entity type (device, interface, BGP session).
- **FR-010**: System MUST consume desired state from the `snapl_intent` package via its `DesiredState` model — the Executor does not duplicate the data model.
- **FR-011**: System MUST time out gNMI operations that do not complete within a configurable deadline (default: 30 seconds). Timed-out operations MUST return a timeout error result, not hang indefinitely.

### Key Entities

- **ApplyResult**: The outcome of a single `apply()` or `rollback()` call. Carries device identifier, success/failure status, the rendered payload that was sent, the device's response (or error detail), and a flag indicating whether this was a rollback.
- **DryRunResult**: The outcome of a `dry_run()` call. Carries device identifier, the rendered payload (or render error), and a clear indication that no changes were made.
- **BatchResult**: The aggregated outcome of a batch apply. A map from device identifier to `ApplyResult`, with a top-level summary (total devices, successes, failures).
- **Executor ABC**: The abstract interface shared by all NAF Executor implementations. Defines `apply(desired: DesiredState) -> ApplyResult`, `rollback(config: DesiredState) -> ApplyResult`, and `dry_run(desired: DesiredState) -> DryRunResult`.
- **GnmiExecutor**: The concrete Nokia SR Linux implementation of the `Executor` ABC. Holds device connection parameters (host, port, credentials, TLS settings) and uses pygnmi for transport.
- **ConfigRenderer**: Internal component responsible for Jinja2 template selection and rendering. Accepts a `DesiredState` and returns a structured JSON payload ready for gNMI.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A single-device apply completes (success or failure result returned) within 30 seconds for any reachable device.
- **SC-002**: Dry-run rendering for any valid desired state completes in under 1 second without a network connection.
- **SC-003**: 100% of template render errors are caught during dry-run before any gNMI connection is attempted.
- **SC-004**: A batch apply across 12 devices (a full spine-leaf fabric) completes within 2 minutes.
- **SC-005**: Unit test suite achieves ≥80% line coverage without requiring a live device or gNMI infrastructure.
- **SC-006**: The datacenter fabric use case (spine-leaf eBGP with Nokia SR Linux) is the fully-tested use case at first delivery, with the template architecture validated to support at least one additional use case.
- **SC-007**: When a device is unreachable, the Executor returns a failure result within the configured timeout (default 30s) — no hanging operations.

## Assumptions

- Nokia SR Linux via Containerlab is the only prototyping target for this feature. The architecture supports additional vendors through the driver pattern (new concrete `Executor` implementations), but no other vendor is in scope.
- gNMI is the primary and only device interface in this feature. NETCONF and SSH are explicitly out of scope.
- The Executor consumes `DesiredState` from `snapl_intent` — it does not implement its own data model or Source of Truth query logic. The Intent module must be available and seeded before the Executor can deploy anything meaningful.
- Credentials (username, password) are injected at `GnmiExecutor` construction time. Credential storage and rotation are out of scope — this is a prototype.
- TLS verification is optional (lab environments use `insecure=True`); production hardening of TLS is out of scope.
- The Executor does not implement idempotency checking (comparing desired vs. running state before applying). That is the responsibility of the Collector + Observability blocks. The Executor applies the desired state unconditionally when instructed.
- Temporal-based orchestration (retry logic, saga patterns, cross-device coordination) is the responsibility of the Orchestrator block. The Executor is a stateless, single-call component.
- The Containerlab SR Linux lab topology already exists in the repository (ported from the predecessor project). Integration tests will reference it.
