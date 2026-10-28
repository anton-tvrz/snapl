# Feature Specification: Scheduled Drift Scans — The Loop Closes Itself

**Feature Branch**: `008-scheduled-drift-scans`
**Created**: 2026-10-28
**Status**: Draft
**Input**: Issue #99 (the loop never closes itself: drift scans have no schedule, remediation is always operator-triggered), with four decisions settled on 2026-10-28: opt-in auto-reconcile in scope and off by default; a 5-minute default cadence plus on-demand trigger; the schedule is created by an explicit, repeatable task rather than at worker start; and operators see unattended results through the CLI and the Temporal Web UI.

## Context

The NAF loop is drawn as a cycle (Intent → Executor → Collector → Observability → Orchestrator → back to Executor), but in snapl the arrow back into the loop is a person. `ScanDrift` and `ReconcileDevices` start only when someone runs `snapl scan` or `snapl reconcile`. Nothing in the system notices drift on its own.

So the headline claim, "snapl detects and heals drift", is demonstrated as two manual commands. The interesting property, that the network is watched and converges without being asked, is never shown.

### Why this needs a specification rather than a patch

This is a new capability, and its second half writes to devices on a timer, which is exactly what constitution Principle VIII.2 guards: "Auto-remediation is never the silent default." The scheduling itself is simple. The care is in:

- **What must never happen automatically:**
  - acting on a device whose state could not be read,
  - re-applying forever to a device whose only drift is config apply cannot remove (#65),
  - writing while switched off.
- **Visibility:** an operator who was not watching must be able to tell what ran, what it found, and whether it changed anything.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Drift Is Found Without Anyone Asking (Priority: P1)

An operator disables an interface on spine-01 by hand and walks away. No one runs a scan. Within one scan interval, snapl has found the drift and anyone who looks can see it.

**Why this priority**: This is the claim itself, "the loop closes", and it is read-only, so it carries no risk to the network.

**Independent Test**: With the schedule active and auto-reconcile off, induce drift by hand, wait one interval without running any snapl command, then read the schedule status. It shows the drifted device. The device is unchanged.

**Acceptance Scenarios**:

1. **Given** an active schedule for `dcfabric`, **When** an interval elapses, **Then** a drift scan of `dcfabric` runs with no operator action.
2. **Given** a device drifted by hand, **When** the next scheduled scan completes, **Then** its result, including which devices drifted and on which paths, is visible to an operator who was not watching.
3. **Given** auto-reconcile is off (the default), **When** a scheduled scan finds drift, **Then** no device is written to.

---

### User Story 2 — The Operator Controls the Schedule (Priority: P1)

An operator sets the schedule up once, can pause it for a maintenance window, resume it, run a scan right now instead of waiting, and see what recent runs found, all from the CLI.

**Why this priority**: An unattended process nobody can see or stop is not something an operator should trust. On stage, a 5-minute wait is dead air that "run it now" removes.

**Independent Test**: Create the schedule, trigger it, pause it, confirm no runs happen across an interval, resume it, and read the status after each step.

**Acceptance Scenarios**:

1. **Given** no schedule exists, **When** the operator runs the setup task, **Then** exactly one schedule exists for the use case at the default interval, paused or active as requested.
2. **Given** the schedule already exists, **When** the setup task runs again, **Then** the schedule is updated in place: it is not duplicated and its run history is not lost.
3. **Given** an active schedule, **When** the operator triggers it, **Then** a scan starts immediately and its result appears in the status.
4. **Given** a paused schedule, **When** an interval elapses, **Then** no scan runs. **When** resumed, scans continue at the configured interval.
5. **Given** any state, **When** the operator asks for the schedule status, **Then** they see:
   - whether it is active or paused,
   - its interval,
   - whether auto-reconcile is on,
   - the most recent runs, each with a one-line summary ("6 devices: 5 clean, 1 drifted, 0 errored") and whether it changed any device.
6. **Given** `invoke demo.up` on a clean checkout, **When** it completes, **Then** the schedule exists (auto-reconcile off), and the preflight reports it.

---

### User Story 3 — Opt-in Self-Healing (Priority: P2)

With detection proven, the operator switches auto-reconcile on. From then on, a scheduled scan that finds a device that has drifted from intent re-applies intent to that device, and the next scan reads it clean, with no operator command in between.

**Why this priority**: This is the "it heals itself" demo moment, and the only part of this feature that writes to devices. It depends on User Story 1 being trusted first, and Principle VIII.2 requires it to be an explicit opt-in.

**Independent Test**: Switch auto-reconcile on, disable an interface by hand, wait one interval or trigger a run, and confirm the interface is back up and the following scan is clean. Then switch it off, repeat the drift, and confirm it stays drifted.

**Acceptance Scenarios**:

1. **Given** auto-reconcile is on, **When** a scheduled scan finds devices with drift intent can fix, **Then** those devices, and only those, are reconciled in the same run.
2. **Given** auto-reconcile is switched on or off, **When** the change is made, **Then** it takes effect from the next run without restarting any process, and the status shows the new setting.
3. **Given** auto-reconcile is on, **When** a run reconciles devices, **Then** the run's status entry says which devices were written to and with what outcome.

---

### Edge Cases

- **A scan outlasts its interval.** The next one is skipped, not stacked. Two scheduled scans of the same use case never run at once.
- **The worker is down when an interval elapses.** The run waits for a worker. While it waits, further intervals do not queue up behind it, and the status makes the waiting run visible rather than reporting nothing.
- **A device errors** (unreachable, or its state can't be read). It is reported as errored and **never** auto-reconciled: the system does not write to a device it could not read.
- **A device's only drift is undesired config** (present on the device, absent from intent). It is reported but **not** auto-reconciled. Apply cannot remove it (#65), so reconciling would rewrite the device every interval without converging.
- **A device has both kinds of drift.** It is auto-reconciled for the drift intent can fix. Its undesired config continues to be reported.
- **An operator deploy is in flight on a device the schedule wants to reconcile.** The existing reconcile behaviour applies: that device is skipped for this run (#105). It is never raced.
- **Auto-reconcile is switched on while a scan is running.** That run keeps the setting it started with. The change applies from the next run.
- **The use case has no devices in the Source of Truth.** The run completes with a zero-device summary and writes nothing.
- **Temporal or the stack restarts.** The schedule, its paused state, its auto-reconcile setting and its recent-run history survive, since Temporal history is persisted (#112).
- **The setup task runs against an Infrahub or Temporal that is not snapl's.** It refuses, like `demo.seed` (#115). Creating a schedule is a write to shared state.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The System MUST run a drift scan of a use case on a recurring interval without operator action, once a schedule for that use case exists and is active.
- **FR-002**: The default interval MUST be 5 minutes, configurable when the schedule is created or updated.
- **FR-003**: A schedule MUST be created or updated only by an explicit, idempotent operator task. Starting a worker MUST NOT create or modify schedules.
- **FR-004**: `invoke demo.up` MUST run the setup task with auto-reconcile off, and `demo.check` MUST report whether the schedule exists and is active.
- **FR-005**: Operators MUST be able to pause, resume and trigger a schedule, and read its status, from the snapl CLI.
- **FR-006**: The status MUST show:
  - active or paused,
  - the interval,
  - the auto-reconcile setting,
  - the most recent runs (at least the last 5), each with start time, one-line drift summary, and whether it wrote to any device.
- **FR-007**: Scheduled scans MUST NOT overlap. A run due while the previous one is still running or waiting is skipped.
- **FR-008**: Auto-reconcile MUST be off by default. Only an explicit operator action turns it on, and the setting MUST be visible in the status.
- **FR-009**: With auto-reconcile off, a scheduled run MUST NOT write to any device.
- **FR-010**: With auto-reconcile on, a scheduled run MUST reconcile exactly the devices whose report contains at least one forward drift item (intent wants something the device lacks or has wrong). Devices that are clean, errored, or drifted only by undesired config MUST NOT be reconciled.
- **FR-011**: Changing the auto-reconcile setting MUST take effect from the next run without restarting any process. A run in progress keeps the setting it started with.
- **FR-012**: Every audit event a scheduled run produces, including those of any reconcile it starts, MUST be attributable to the schedule rather than to an operator, so `snapl audit` can tell unattended actions from human ones.
- **FR-013**: The setup task MUST verify the Source of Truth and Temporal it targets are snapl's before creating or updating a schedule, with the same positive-identification rule as `demo.seed`.
- **FR-014**: A schedule MUST survive restarts of the worker, Temporal and the dev stack, including its paused state, its auto-reconcile setting and its recent-run history.
- **FR-015**: Scheduled runs MUST be visible in the Temporal Web UI as ordinary scan (and reconcile) workflows, recognisable as scheduled.

### Key Entities

- **Drift Schedule**: one per use case. It holds the interval, the active or paused state, and the auto-reconcile setting. It is cluster state, owned by Temporal, not configuration on a worker.
- **Scheduled Run**: one firing of a schedule. A drift scan, plus a reconcile of the forward-drifted devices when auto-reconcile is on. Its summary is what the status shows.
- **Auto-Reconcile Setting**: an operator-controlled switch on the schedule. Off unless someone turns it on. It applies per run, not mid-run.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With the schedule active, drift induced by hand appears in the schedule status within one interval plus the scan's own duration, with no snapl command run in between.
- **SC-002**: Triggering the schedule produces a completed run in the status within 30 seconds on the six-device fabric.
- **SC-003**: With auto-reconcile off, scheduled runs over at least three intervals with drift present produce zero device writes. The audit trail contains no apply attributed to the schedule.
- **SC-004**: With auto-reconcile on, an interface disabled by hand is re-enabled by the next scheduled run, and the run after that reports the fabric clean, with no operator command in between.
- **SC-005**: A device drifted only by undesired config is reported on every run and written to on none, with auto-reconcile on.
- **SC-006**: A paused schedule produces no runs across at least two intervals. After resume, the next run happens within one interval.
- **SC-007**: After a restart of the dev stack, `snapl schedule status` shows the same schedule, setting and recent runs as before.
- **SC-008**: Running the setup task twice leaves exactly one schedule per use case.

## Assumptions

- Temporal Schedules are the scheduling mechanism (issue #99), because their cadence, pause state and recent runs are visible in the Temporal Web UI, which the demo uses. A loop inside a workflow would hide all of that. The implementation plan confirms the specifics.
- Temporal history is persisted (#112), so schedules and their run history survive restarts without extra work.
- `dcfabric` is the only use case today. The design is per use case, so site edge (#117) gets its own schedule without changes.
- Run history for the status comes from Temporal itself. This feature adds no metrics or dashboards; those remain #101.
- The auto-reconcile switch lives on the schedule (cluster state), not in a worker environment variable. #99 proposed `SNAPL_AUTO_RECONCILE`, but a per-process variable would need a restart to flip and could disagree between workers, which conflicts with FR-011.
- Reconcile reuses the existing `ReconcileDevices` workflow and its safeguards: skipping devices with in-flight deploys (#105), and verification that ignores undesired-only drift (#121).

## Dependencies

- Issue #99: delivered by this feature.
- Issue #112: persisted Temporal history (merged); required by FR-014.
- Issue #121: verification ignores undesired-only drift (merged); without it, every auto-reconcile of such a device would report failure.
- Issue #65: removal of undesired config, out of scope. Until it lands, FR-010 deliberately excludes undesired-only devices from auto-reconcile.
- Issue #101: metrics and dashboards, out of scope. The status in FR-006 is the operator surface until then.
