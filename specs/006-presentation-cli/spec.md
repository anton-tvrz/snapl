# Feature Specification: NAF Presentation — Operator CLI

**Feature Branch**: `006-presentation-cli`
**Created**: 2026-07-29
**Status**: Draft
**Input**: User description: "Presentation block: a CLI that lets an operator drive the closed NAF loop — deploy, scan, reconcile — and read the audit trail, without writing Python"

## Context

Presentation is the last unimplemented NAF block. The other five are complete and the closed loop is validated end to end against a live SR Linux fabric (spec 005 SC-001/003/005/007, issue #74). The loop's only interface today is Python: `docs/demo-scenarios.md` drives every scenario by constructing a Temporal client and calling `execute_workflow` from a REPL. That is a demonstration of a library, not of a product.

This feature closes that gap. It is tracked by issues #63 (the CLI) and #102 (this spec), and is the last remaining blocker on the MVP demo-readiness epic (#103).

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Deploy Intended State From the Command Line (Priority: P1)

An operator has intent in the Source of Truth and wants the network to match it. They name a device and run one command. The CLI starts the Orchestrator's deploy workflow, streams progress as it advances through the loop, and prints a terminal verdict with the reason and elapsed time. They never see a Python traceback, a workflow ID they have to construct, or a Temporal concept they did not ask about.

**Why this priority**: This is the CLI's reason to exist and the first beat of the demo arc. Every other command is a variation on "start a workflow and render its result"; getting this one right establishes the client wiring, the output contract, and the error contract that the rest inherit.

**Independent Test**: Can be fully tested by invoking `snapl deploy spine-01` against a mocked Temporal client, asserting the workflow type, argument, and ID it starts, and asserting the rendered output and exit code for both a succeeded and a failed `WorkflowResult`.

**Acceptance Scenarios**:

1. **Given** a device named in the SoT and a running worker, **When** the operator runs `snapl deploy spine-01`, **Then** the CLI resolves the name to its device ID, starts `DeployIntentWorkflow`, and on completion prints the outcome, reason, and duration, exiting 0.
2. **Given** a deploy that terminates with `success=False`, **When** the command completes, **Then** the CLI prints the reason code and detail and exits non-zero — the operator does not have to inspect the worker log to learn it failed.
3. **Given** a device name that does not exist in the SoT, **When** the operator runs `snapl deploy ghost`, **Then** the CLI reports the name as unknown and exits non-zero **without** starting a workflow.
4. **Given** no worker is polling the task queue, **When** the operator runs a deploy, **Then** the CLI reports that the workflow was accepted but no worker is running, rather than appearing to hang silently.

---

### User Story 2 — See What Drifted, Precisely (Priority: P1)

A network engineer wants to know whether the fabric still matches intent. They run one command against a use case and get a per-device summary plus, for each drifted device, the exact configuration paths that differ with their desired and actual values. The output is readable on a terminal and, on request, emitted as JSON for a script or a CI job.

**Why this priority**: This is the platform's most compelling output — "we don't just notice that something changed, we name the path" — and today it is a `print()` from a REPL. It is also the command most likely to be run by something other than a human, which is what makes the machine-readable mode and the exit-code contract load-bearing.

**Independent Test**: Can be fully tested against a mocked client returning a `DriftScanResult` with a mix of clean, drifted, and errored devices, asserting the rendered table, the JSON shape, and the exit code for each combination.

**Acceptance Scenarios**:

1. **Given** a fabric where every device matches intent, **When** the operator runs `snapl scan --use-case dcfabric`, **Then** the CLI prints the per-status counts, reports the fabric clean, and exits 0.
2. **Given** a fabric where one device has drifted, **When** the scan runs, **Then** the CLI names the drifted device and prints each differing path with its desired and actual value, and exits 2.
3. **Given** a fabric where one device is unreachable, **When** the scan runs, **Then** that device is reported as errored with its failure message, every other device is still evaluated and reported, and the exit code reflects the operational error.
4. **Given** the operator passes `--json`, **When** the scan runs, **Then** stdout carries only valid JSON — all human-oriented output is suppressed or routed to stderr — and the same exit-code contract applies.

---

### User Story 3 — Heal the Fabric (Priority: P2)

Having seen drift, the operator reconciles — either a named set of devices or every drifted device from the last scan — and watches the per-device outcomes come back. Because reconcile is the one command that writes to many devices at once, it states what it is about to do and requires confirmation unless explicitly told not to.

**Why this priority**: Detection without remediation is a dashboard. This closes the loop from the operator's side. P2 rather than P1 because it composes US1's deploy path and is meaningless before US1 and US2 work.

**Independent Test**: Can be fully tested against a mocked client returning a `ReconcileResult` with a mix of succeeded, failed and skipped devices, asserting the summary rendering, the confirmation prompt behaviour, and the exit code.

**Acceptance Scenarios**:

1. **Given** a drifted device, **When** the operator runs `snapl reconcile spine-01` and confirms, **Then** the CLI starts the reconcile workflow and prints the per-device outcome with a succeeded / failed / skipped summary.
2. **Given** the operator runs `snapl reconcile --use-case dcfabric --drifted`, **When** the command runs, **Then** the CLI first scans, reports which devices it intends to reconcile, and proceeds only on confirmation.
3. **Given** the operator passes `--yes`, **When** the command runs, **Then** no prompt is shown — this is the form usable from a script.
4. **Given** a reconcile where one device fails and another is skipped, **When** it completes, **Then** both are shown distinctly, and the exit code reflects the failure rather than the skip.

---

### User Story 4 — Read the Audit Trail (Priority: P2)

An operator or auditor wants to reconstruct what the platform did. They query the durable audit log by workflow or by device and get a chronological, readable timeline: what started, which activities ran, what each returned, and how it ended.

**Why this priority**: The audit log is the durability story's payoff, and it is currently reachable only by writing Python against `SqliteAuditLog`. P2 because it reports on activity the P1 commands generate.

**Independent Test**: Can be fully tested against a mocked audit log returning a known event sequence, asserting chronological rendering and the by-workflow and by-device query paths.

**Acceptance Scenarios**:

1. **Given** a completed deploy, **When** the operator runs `snapl audit --workflow deploy-intent-<id>`, **Then** the events print in chronological order with timestamp, event type, activity name, and outcome.
2. **Given** several workflows have touched one device, **When** the operator runs `snapl audit --device spine-01`, **Then** events from all of them are returned in chronological order, each identifying its workflow.
3. **Given** a workflow ID with no events, **When** the query runs, **Then** the CLI says so plainly and exits 0 — an empty result is an answer, not an error.

---

### User Story 5 — Know Whether the System Is Healthy (Priority: P3)

Before demoing or debugging, an operator wants one command that answers "is this thing working?" — worker connected, SoT reachable and populated, devices dialable, workflows currently running.

**Why this priority**: Operability. `invoke demo.check` (issue #97) already covers the environment-preflight half from the developer side; this is the operator-facing view and adds the in-flight workflow list, which preflight does not have. P3 because the loop is demonstrable without it.

**Independent Test**: Can be fully tested against mocked clients, asserting each probe's rendering and that a failing probe produces a non-zero exit.

**Acceptance Scenarios**:

1. **Given** a healthy environment, **When** the operator runs `snapl status`, **Then** each subsystem is listed as reachable with the address probed, and the command exits 0.
2. **Given** two workflows in flight, **When** `snapl status` runs, **Then** both appear with their workflow ID, type, target, and elapsed time.
3. **Given** the worker is not running, **When** `snapl status` runs, **Then** that is reported as a distinct failing line naming the task queue, and the command exits non-zero.

---

### Edge Cases

- What happens when the Temporal cluster is unreachable? Every command MUST fail with a message naming the address it tried and the environment variable that sets it — never a raw connection traceback.
- What happens when a workflow is already running for the requested device? The CLI MUST report that it joined or refused rather than silently starting a second one; per-device serialization is the Orchestrator's contract (005 FR-009) and the CLI MUST NOT circumvent it by minting unique IDs.
- What happens when the operator interrupts (Ctrl-C) a command waiting on a workflow? The workflow MUST keep running — the CLI is a client, not the executor — and the CLI MUST say so, printing the workflow ID needed to re-attach.
- What happens when a device name is ambiguous (two devices, same name, different use cases)? The CLI MUST refuse and list the candidates rather than picking one.
- What happens when output is piped or redirected? Colour and progress animation MUST be suppressed when stdout is not a TTY, without the operator having to pass a flag.
- What happens when `--json` is combined with a command that prompts? The prompt MUST be suppressed and the command MUST refuse unless `--yes` is also given — a JSON consumer cannot answer a prompt.
- What happens when the audit database does not exist yet? The CLI MUST report it as "no audit log at <path>" rather than creating an empty one and reporting zero events, which would be indistinguishable from a real empty log.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The System MUST expose a `snapl` console entry point installed from the `snapl-presentation` package.
- **FR-002**: The System MUST provide a `deploy` command that starts `DeployIntentWorkflow` for one device, waits for its terminal result, and renders it.
- **FR-003**: The System MUST provide a `scan` command that starts `ScanDriftWorkflow` for a use case and renders per-device status plus, for drifted devices, each differing path with desired and actual values.
- **FR-004**: The System MUST provide a `reconcile` command that starts `ReconcileDevicesWorkflow` for a set of devices, selectable either by explicit names or as "every drifted device in a use case".
- **FR-005**: The System MUST provide an `audit` command that queries the durable audit log by workflow ID or by device and renders events chronologically.
- **FR-006**: The System MUST provide a `status` command reporting the reachability of Temporal, the Source of Truth, and the worker, plus any currently running workflows.
- **FR-007**: The System MUST accept device and use-case **names** wherever an identifier is required, resolving names to IDs against the Source of Truth. Operators MUST NOT be required to handle UUIDs.
- **FR-008**: The System MUST connect to Temporal through `snapl_orchestrator.worker.client.build_client`, so the pydantic data converter is registered. Constructing a bare Temporal client is prohibited — payload decoding silently breaks without the converter.
- **FR-009**: The System MUST resolve all connection settings from the same environment variables and defaults the worker uses, reusing the worker module's exported constants rather than restating them.
- **FR-010**: The System MUST support a `--json` flag on every command that produces a result. When set, stdout MUST carry only valid JSON.
- **FR-011**: The System MUST use these exit codes uniformly: **0** — the command ran and found nothing wrong; **1** — an operational error (unreachable dependency, invalid input, failed workflow); **2** — the command ran successfully and found drift. Drift is a finding, not a failure of the command, and MUST be distinguishable from both by a caller that reads only the exit code.
- **FR-012**: The System MUST prompt for confirmation before any command that writes to more than one device, and MUST provide a `--yes` flag to skip the prompt for non-interactive use.
- **FR-013**: The System MUST suppress colour, progress animation, and other TTY affordances when stdout is not a terminal, without requiring a flag.
- **FR-014**: The System MUST render every expected failure as a single actionable message naming the failing subsystem and, where applicable, the environment variable or command that would fix it. Python tracebacks MUST NOT reach the operator for any anticipated failure.
- **FR-015**: The System MUST NOT circumvent the Orchestrator's per-device serialization by generating unique workflow IDs; it MUST use the documented `deploy-intent-<device_id>` ID family and surface conflicts as such.
- **FR-016**: The System MUST consume the Orchestrator's existing public workflow and audit interfaces unchanged. No changes to workflow signatures are part of this feature.
- **FR-017**: The System MUST be independently testable without a live Temporal cluster, a live SoT, or live devices — command tests run against mocked clients.
- **FR-018**: The System MUST NOT duplicate the environment lifecycle that `invoke dev.*` and `invoke demo.*` own (starting the stack, deploying the lab, seeding). `snapl` operates the network; `invoke` operates the developer environment. `status` MAY overlap `demo.check` in what it reports, but MUST NOT start or stop anything.

### Key Entities

- **Command**: One operator-invocable verb (`deploy`, `scan`, `reconcile`, `audit`, `status`), with its options, its rendering, and its exit-code mapping.
- **Renderer**: The component translating an Orchestrator result model into operator-facing output. Owns the human/JSON split, so command logic never branches on output format beyond selecting a renderer.
- **CliSettings**: Resolved connection configuration (Temporal host and namespace, task queue, SoT address and token, audit database path), sourced from the worker's environment contract.
- **ExitCode**: The 0 / 1 / 2 contract in FR-011, applied uniformly so scripts can rely on it across commands.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every scenario in `docs/demo-scenarios.md` is executable using only `snapl` commands — no Python snippet, no REPL, no manually constructed Temporal client.
- **SC-002**: A first-time operator can run a deploy, a scan, and a reconcile using only `snapl --help` and its subcommand help, without reading the source or the spec.
- **SC-003**: 100% of anticipated failure modes (Temporal unreachable, SoT unreachable, unknown device name, no worker polling, missing audit database) produce a single-line actionable message and a documented exit code — zero tracebacks.
- **SC-004**: `snapl scan --json` output parses as valid JSON in 100% of runs, including when devices error, and contains every field the human rendering shows.
- **SC-005**: The exit-code contract holds across all commands: a caller can distinguish "clean", "drifted", and "broken" from the exit code alone, without parsing output.
- **SC-006**: A `snapl deploy` against a reachable device adds no more than 2 seconds of overhead beyond the underlying workflow's own duration.
- **SC-007**: Unit test suite achieves ≥80% line coverage of the Presentation package with no live Temporal cluster, SoT, or devices.
- **SC-008**: Interrupting any waiting command leaves the underlying workflow running and prints the workflow ID needed to re-attach.

## Assumptions

- Typer and Rich are the CLI and rendering libraries — already declared as `snapl-presentation` dependencies and consistent with the package's stated intent.
- The CLI is a **client**: it starts workflows and reads results. It contains no network logic, no gNMI, and no drift computation. All behaviour lives in the blocks it calls; a bug in the loop is never fixed in Presentation.
- `snapl` is the primary demo driver, with the Temporal Web UI as the supporting visual for durability and workflow history. This is the decision recorded in epic #103.
- Drift exits 2 rather than 0 or 1 — the check-tool convention, chosen so cron and CI can separate "drifted" from "broken" without parsing output.
- Authentication and authorization of operators are out of scope, as deferred by 005's assumptions. The CLI inherits whatever credentials the environment provides.
- An HTTP API surface for the Presentation block is out of scope for this iteration. The block is named "CLI / API"; only the CLI is specified here, and the renderer/settings split is intended to keep an API addable without restructuring.
- Scheduled execution is out of scope. If Temporal Schedules land (issue #99), schedule control is a follow-up command set, not part of this feature.
- Live metrics and dashboards are out of scope (issue #101). The `scan` and `audit` renderings are this iteration's answer to operator visibility.
- Shell completion is out of scope for this iteration; Typer provides it for free later if wanted.
- The `dcfabric` use case is the only one with seed data and templates. Commands take `--use-case` and must not hardcode `dcfabric`, but only that use case is exercised.

## Dependencies

- **Blocks**: issue #63 (implementation of this spec).
- **Consumes**: `snapl-orchestrator` (workflows, worker client, audit log) and, transitively, `snapl-intent` for name resolution. Dependency direction matches `AGENTS.md`: `presentation -> orchestrator -> {intent, executor, collector, observability}`.
- **Related**: #101 proposes drift/audit rendering; this spec absorbs it — the renderings specified here are that work, and #101's remaining scope is metrics and dashboards.
- **Related**: #99 (Temporal Schedules) would add schedule-control commands as a follow-up.
