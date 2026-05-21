# Feature Specification: NAF Orchestrator — Temporal Workflows

**Feature Branch**: `005-orchestrator-temporal`
**Created**: 2026-05-21
**Status**: Draft
**Input**: User description: "Orchestrator block: Temporal workflows that compose intent, executor, collector, and observability to close the NAF loop with durable, retryable, auditable multi-device operations and reconciliation"

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Deploy Intended State End-to-End on a Single Device (Priority: P1)

An operator (or upstream automation) has a desired state in the Source of Truth and wants the network to reflect it. The Orchestrator reads the intent for the target device, applies the configuration through the Executor, retrieves the running state through the Collector, verifies the device now matches intent through the Observability comparison, and records the outcome as a durable audit event. The whole sequence survives transient failures (connection blips, brief auth issues, restarts of the worker process) and either completes successfully or terminates with a clear, recorded failure.

**Why this priority**: This is the Orchestrator's reason to exist. The other four NAF blocks are independently usable, but it is the Orchestrator that turns "we have intent, we have an executor, we have a collector, we have a drift detector" into "we deploy the intended state, verify it, and have a tamper-evident record of what happened." Without this workflow, the NAF loop does not close.

**Independent Test**: Can be tested by invoking `deploy_intent(device_id)` against a Temporal test environment with mock Intent / Executor / Collector / Observability activities. Verify the workflow runs the four steps in order, records an audit event on each step, returns a terminal success/failure outcome, and survives an activity-level failure that is retried successfully.

**Acceptance Scenarios**:

1. **Given** a device in the SoT with a desired state and reachable network access, **When** `deploy_intent(device_id)` is invoked, **Then** the workflow fetches intent, applies via Executor, collects via Collector, runs Observability comparison, records each step in the durable audit log, and returns a `WorkflowResult` with `success=True` and no remaining drift.
2. **Given** a device where the Executor apply step fails transiently (e.g., one timeout), **When** the workflow runs, **Then** the activity is retried by the workflow engine, succeeds on retry, and the audit log records both the failed attempt and the eventual success.
3. **Given** a device where the Executor apply step fails permanently after retries are exhausted, **When** the workflow runs, **Then** the workflow terminates with `success=False`, the audit log records the failure with its cause, and no later step (collect, verify) is run.
4. **Given** the workflow worker process is restarted mid-workflow, **When** the worker comes back up, **Then** the workflow resumes from the last completed activity — it does not restart from scratch and does not re-apply already-applied configuration.

---

### User Story 2 — Detect Drift Across a Fabric and Trigger Reconciliation (Priority: P2)

A network engineer wants to know, on demand, whether any device in a use case (e.g., a datacenter fabric) has drifted from its intended state, and if so, to remediate. The Orchestrator runs an Observability comparison across all devices in the fabric, summarizes drifted devices, and exposes a reconcile workflow that re-applies the intended state to a chosen subset. The drift scan and the reconcile decision are recorded in the audit log so an auditor can trace what was found, what was decided, who decided it, and what was done.

**Why this priority**: Drift detection is what Observability produces; reconciliation is what makes drift detection actionable. Without an Orchestrator-driven flow, drift findings sit unaddressed and the loop stays open. This is P2 (not P1) because the single-device deploy path must work first — fabric-wide reconcile composes it.

**Independent Test**: Can be tested by invoking `scan_drift(use_case_id)` against a mocked fabric of three devices where one is drifted, verifying the returned `DriftScanResult` identifies the one drifted device, then invoking `reconcile_devices([device_id])` and verifying the deploy workflow runs for that single device.

**Acceptance Scenarios**:

1. **Given** a fabric of three devices, all in sync with intent, **When** `scan_drift(use_case_id)` is invoked, **Then** the workflow returns a `DriftScanResult` with three devices, zero drifted, and records the scan event in the audit log.
2. **Given** a fabric of three devices where one has drifted, **When** `scan_drift(use_case_id)` is invoked, **Then** the workflow returns a `DriftScanResult` identifying the drifted device and the specific paths that differ.
3. **Given** a `DriftScanResult` showing one drifted device, **When** `reconcile_devices([device_id])` is invoked, **Then** the Orchestrator runs the User Story 1 deploy workflow for that device and records the reconciliation event linking back to the originating drift scan.
4. **Given** an operator has not invoked reconciliation, **When** the system is observed, **Then** no automatic remediation occurs — drift findings remain in the audit log but the device is not modified.

---

### User Story 3 — Durable Audit Log for All NAF Operations (Priority: P2)

An auditor or operator needs to reconstruct exactly what the platform did to the network and when. Every workflow records its events (started, step completed, step failed, terminated) and these records survive worker restarts, deployments, and crashes. The Orchestrator owns the durable audit log that the Observability block defers to it; queries against the log return a chronological, per-device, per-workflow view of activity.

**Why this priority**: Closes the explicit gap left by the Observability block, whose `AuditLog` is in-memory only with persistence deferred to the Orchestrator. Without durability, the platform's compliance and post-incident story is incomplete. P2 because the deploy workflow (US1) must exist before there is anything meaningful to durably record.

**Independent Test**: Can be tested by running a deploy workflow against a Temporal test environment, killing the worker process, restarting it, and querying the audit log for the workflow's events — all events recorded before the crash must still be retrievable.

**Acceptance Scenarios**:

1. **Given** a deploy workflow has run to completion, **When** the audit log is queried for that workflow ID, **Then** an ordered list of events is returned covering each activity start, completion, and outcome.
2. **Given** a workflow has run, the worker process has been restarted, and a new workflow has run since, **When** the audit log is queried for the original workflow ID, **Then** the original events are still returned in full.
3. **Given** events for multiple workflows targeting the same device, **When** the audit log is queried by device ID, **Then** events are returned in chronological order spanning all workflows that touched that device.
4. **Given** an audit log entry has been written, **When** any caller attempts to modify or delete it, **Then** the operation is rejected — entries are append-only.

---

### User Story 4 — Inspect and Manage In-Flight Workflows (Priority: P3)

A network engineer needs to see what the Orchestrator is currently doing and, occasionally, to stop a workflow that is misbehaving or no longer needed (e.g., scope-of-change reduced mid-deploy). The Orchestrator exposes the list of running workflows, their current step, their elapsed time, and a cancel control. Cancellation is recorded in the audit log; in-flight activities are given a chance to clean up.

**Why this priority**: Operability and human override. Important for production trust but not needed for the loop to function or be auditable. P3 because the deploy, reconcile, and audit capabilities can be validated without an introspection surface; introspection is a layer above.

**Independent Test**: Can be tested by starting a long-running deploy workflow with a mocked Executor that sleeps, querying the workflow list to confirm it appears with status "running", issuing a cancel, and verifying the workflow terminates with a "cancelled" audit event and `success=False`.

**Acceptance Scenarios**:

1. **Given** two deploy workflows are running, **When** the workflow list is queried, **Then** both are returned with their workflow ID, target device(s), current activity, and start time.
2. **Given** a running workflow, **When** cancellation is requested, **Then** the workflow stops at the next cancellation-safe point, the audit log records a `cancelled` event with the requester, and the workflow result reports `success=False, reason=cancelled`.
3. **Given** a cancelled workflow, **When** the workflow list is queried, **Then** it is no longer reported as "running" and its terminal state is retrievable from the audit log.

---

### Edge Cases

- What happens when intent for the target device cannot be fetched from the SoT (SoT unreachable, device not in inventory)? The workflow MUST terminate before any apply, record a clear failure cause in the audit log, and not attempt later steps.
- What happens when the Executor reports `success=True` but the post-apply Collector retrieval shows the configuration was not applied? The Observability verification step MUST detect this as drift, the workflow MUST terminate with `success=False` and a `verification_failed` reason, and the audit log MUST record the discrepancy.
- What happens when two deploy workflows are invoked concurrently for the same device? The Orchestrator MUST serialize them per device — the second waits for the first to terminate rather than racing on the wire.
- What happens when a reconcile workflow targets a list including a device that no longer exists in the SoT? The workflow MUST skip the missing device with a recorded warning and proceed with the remaining devices.
- What happens when the audit log write itself fails? The workflow MUST treat audit log writes as part of the workflow's durability contract — a workflow step is not considered complete until its audit event is durable. A persistent inability to write audit events MUST fail the workflow.
- What happens when a scheduled or recurring workflow is desired? Out of scope for this iteration — all workflows are on-demand. The architecture must not foreclose adding scheduling later.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The System MUST expose a `deploy_intent(device_id)` workflow that fetches intended state from the Intent block, applies it through the Executor, retrieves running state through the Collector, runs the Observability drift comparison, and returns a `WorkflowResult` summarizing the outcome.
- **FR-002**: The System MUST execute each step of every workflow as a durable, retryable activity such that worker restarts, transient network failures, and brief downstream-service outages do not require the workflow to restart from the beginning or to re-apply already-applied configuration.
- **FR-003**: The System MUST expose a `scan_drift(use_case_id)` workflow that runs the Observability comparison across all devices in the named use case and returns a `DriftScanResult` identifying drifted devices and their differing paths.
- **FR-004**: The System MUST expose a `reconcile_devices(device_ids)` workflow that runs the `deploy_intent` workflow for each named device. Per-device failure MUST NOT prevent execution against the remaining devices, and the per-device outcomes MUST be returned together in a `ReconcileResult`.
- **FR-005**: The System MUST treat drift findings as informational by default. Detected drift MUST NOT cause automatic reapplication of intent — reconciliation is initiated only by an explicit `reconcile_devices` invocation.
- **FR-006**: The System MUST persist an append-only audit log of every workflow event (workflow started, activity started, activity completed, activity failed, workflow terminated, workflow cancelled). The log MUST survive worker restarts and process crashes.
- **FR-007**: The System MUST support audit log queries by workflow ID, by device ID, and by time range. Results MUST be returned in chronological order.
- **FR-008**: The System MUST reject any attempt to mutate or delete an existing audit log entry. The audit log is append-only by contract.
- **FR-009**: The System MUST serialize concurrent `deploy_intent` workflows targeting the same device — only one apply-collect-verify sequence may be in progress for a given device at any time. Workflows for different devices MAY run concurrently.
- **FR-010**: The System MUST expose a workflow introspection surface that lists currently running workflows with their workflow ID, target device(s), current activity, and start time.
- **FR-011**: The System MUST support workflow cancellation. Cancellation MUST be recorded in the audit log and MUST allow in-flight activities a brief, bounded window to clean up before forcible termination.
- **FR-012**: The System MUST treat failures of the Intent fetch step as terminal for the workflow — no apply, no collect, no verify may run when intent is unavailable.
- **FR-013**: When the post-apply verification (Collector + Observability) shows the apply did not take effect, the System MUST mark the workflow `success=False` with reason `verification_failed` and MUST record the discrepancy in the audit log.
- **FR-014**: The System MUST consume the existing public interfaces of the Intent, Executor, Collector, and Observability blocks unchanged — no breaking changes to those blocks are part of this feature.
- **FR-015**: The System MUST take over the durability obligation deferred by the Observability block's in-memory `AuditLog`. Observability MAY continue to expose its in-memory view, but persistent audit entries MUST flow through the Orchestrator's durable log.
- **FR-016**: The System MUST be independently testable without live network infrastructure. Workflow tests MUST be able to run against a Temporal test environment with mocked activities for the four downstream blocks.

### Key Entities

- **Workflow**: A named, durable, retryable unit of work composed of one or more activities. Has a unique workflow ID, a workflow type (e.g., `DeployIntent`, `ScanDrift`, `ReconcileDevices`), a target (device ID or use case ID), a start timestamp, a current state, and a terminal result on completion.
- **Activity**: A single step within a workflow that delegates to one of the four downstream NAF blocks (fetch intent, apply config, collect running state, run drift comparison, write audit entry). Activities are retried independently of the workflow according to a retry policy.
- **WorkflowResult**: The terminal outcome of a single workflow run. Carries success/failure, a reason code (e.g., `succeeded`, `intent_unavailable`, `apply_failed`, `verification_failed`, `cancelled`), the workflow ID, the target, and any per-step outcomes that the caller needs.
- **DriftScanResult**: The outcome of a `scan_drift` workflow. Lists every device evaluated, marks which drifted, and for each drifted device names the YANG paths whose actual value differs from intended value.
- **ReconcileResult**: The aggregated outcome of a `reconcile_devices` workflow. A per-device `WorkflowResult` for each target device, with a top-level summary (devices attempted, successes, failures).
- **AuditEvent**: A single durable, append-only record of something the platform did. Carries a workflow ID, a target identifier, an event type, a timestamp, the actor (caller), and event-specific payload (e.g., the activity name and its outcome).
- **AuditLog**: The durable, queryable store of `AuditEvent` records. Owned by the Orchestrator block; takes over the persistence obligation deferred by the Observability block.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A single-device `deploy_intent` workflow against a reachable device with a small (≤10 path) intent completes (success or failure terminal state recorded) within 60 seconds.
- **SC-002**: A `scan_drift` workflow across a 12-device fabric completes within 3 minutes.
- **SC-003**: After a worker process is killed and restarted mid-workflow, 100% of in-flight workflows resume from their last completed activity — no workflow restarts from the beginning and no apply step re-runs against a device.
- **SC-004**: 100% of activity-level failures (intent fetch, apply, collect, verify, audit write) produce a terminal `WorkflowResult` with a specific reason code. No workflow exits with an unhandled exception bubbling to the caller.
- **SC-005**: 100% of completed workflow events are retrievable from the audit log after a worker restart.
- **SC-006**: Concurrent invocations of `deploy_intent` for the same device serialize correctly — at most one apply-collect-verify sequence runs against a given device at any time.
- **SC-007**: A drifted device is correctly identified by a `scan_drift` workflow and is correctly returned to intent by a subsequent `reconcile_devices` workflow without operator intervention beyond invoking those two workflows.
- **SC-008**: Unit test suite achieves ≥80% line coverage of the Orchestrator package without requiring a live device, a live Temporal cluster beyond the Temporal test environment, or live Intent / Executor / Collector / Observability dependencies.

## Assumptions

- Temporal is the workflow engine, consistent with project-wide architecture choices stated in `AGENTS.md` and the constitution. No alternative engine is in scope.
- The Orchestrator package depends on `snapl-intent`, `snapl-executor`, `snapl-collector`, and `snapl-observability` (workspace deps), consistent with the dependency direction stated in `AGENTS.md`.
- All workflows are on-demand for this iteration — invoked by a caller (CLI, API, test, or upstream automation). Scheduled and event-driven (e.g., webhook-triggered) execution is out of scope and deferred.
- Drift remediation is operator-initiated, not automatic. A detected drift produces a finding in the audit log; reconciliation is a separate, explicit workflow invocation. This bounds blast-radius risk for the prototype.
- The durable audit log is backed by Temporal's own event history plus a queryable projection sufficient for the query patterns in FR-007. A separate relational or object store for long-term archive is out of scope for this iteration.
- The Observability block's in-memory `AuditLog` continues to exist for in-process use. The Orchestrator's durable log is the source of truth for cross-workflow and post-restart queries.
- Authentication and authorization of workflow invocations (who may call `deploy_intent` / `reconcile_devices`) are out of scope. The CLI / API surface (Presentation block) will own that concern.
- Nokia SR Linux via Containerlab remains the only prototyping target. The Orchestrator is vendor-agnostic; vendor specificity lives in the downstream Executor / Collector implementations.
- Workflow versioning is out of scope. Existing workflows are not expected to be migrated to a new shape during this iteration; future work may introduce Temporal patching policies.
- Multi-region / high-availability deployment of the Temporal cluster is out of scope. A single local Temporal instance (development stack) is the target environment for this iteration.
