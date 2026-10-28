# Feature Specification: Scheduled Drift Scans and Opt-In Remediation

**Feature Branch**: `009-scheduled-drift-response`
**Created**: 2026-10-28
**Status**: Draft
**Input**: Issue #99 ("the loop never closes itself: drift scans have no schedule, remediation is always operator-triggered"), epic #103 ("Auto-remediation default. If #99 lands, detect-only vs auto-heal needs to be a deliberate default (recommend opt-in)"), constitution 2.0.0 Principle VIII.2 and VIII.5

## Context

The NAF loop snapl describes is continuous: Intent → Executor → Collector → Observability → Orchestrator → back to Executor. In the implementation, a person closes that loop. A drift scan runs only when someone types `snapl scan`, and a repair runs only when someone types `snapl reconcile`. Nothing in the Orchestrator starts work on its own.

Two consequences:

- **Drift is found only when someone looks.** A hand change made at 02:00 stays invisible until an operator next scans, however long that takes. The audit trail holds no record of when the fabric stopped matching intent, only of when someone noticed.
- **"The fabric converges on its own" is never demonstrated.** The demo shows convergence as two manual commands. Self-healing, the property the loop exists for, is not shown.

### Why this needs a specification rather than a patch

Principle II requires a spec for any change that writes to a device in a way the system did not before. Scheduled remediation is exactly that: a write nobody typed. It is also the first time a change to intent in the Source of Truth can reach a device without an operator running `deploy`. In remediate mode, a scheduled run sees the device "lacking" the new intent and applies it.

Principle VIII.2 fixes the shape of the answer. Every drift-detection path has a defined response, and auto-remediation is never the silent default:

| response | meaning in this feature |
| --- | --- |
| `report` | a scheduled scan records what drifted and changes nothing. **The default for every schedule.** |
| `remediate` | a scheduled scan is followed by a repair of the drifted devices. Only an explicit, confirmed, audited operator action turns it on. |
| `suppress` | scheduled scans stop for a stated reason (a maintenance window, a planned hand change) until an operator resumes them. |

Principle VIII.5 sets a hard boundary. Removing config the device carries but intent never asked for (spec 007, #65) is never automatic. That stays true here. A repair is the same merge-only deploy an operator runs today, and the schedule never deletes anything.

One more fact from the live fabric shapes the design. Since spec 007, a scan reports **undesired** config (present on the device, absent from intent) as drift. A deploy cannot remove it, so the device reads DRIFTED again straight after a successful deploy (#121). If a scheduled repair took every DRIFTED device, it would rewrite an undesired-only device on every interval forever and change nothing. A scheduled repair therefore acts only on drift a deploy can fix.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — The Fabric Is Watched Without Anyone Asking (Priority: P1)

An operator wants to know, without remembering to check, when the fabric stops matching intent. They put the `dcfabric` use case on a drift schedule once. From then on a scan runs on a fixed interval, and each run's findings land in the audit trail, attributed to the schedule. Nothing is written to any device.

**Why this priority**: This is the half of #99 that carries no risk, and it is the precondition for the rest. A repair on a schedule cannot be trusted until a scan on a schedule has been seen to run, report and stay read-only. It also closes the observability gap on its own: drift is found within one interval of happening, not whenever someone next looks.

**Independent Test**: Create a report-mode schedule on a short interval against a fabric with one hand-made change. Without running any other command, verify that a scheduled run starts within one interval, reports the drifted device and its paths, records them in the audit trail as a scheduled run in report mode, and leaves the device unchanged.

**Acceptance Scenarios**:

1. **Given** no schedule exists for a use case, **When** the operator creates one, **Then** a schedule exists with the default interval and the `report` response, and the operator is shown when the first run will start.
2. **Given** a report-mode schedule and a device changed by hand, **When** the next scheduled run completes, **Then** the audit trail records the run, names the schedule as its trigger and `report` as its response, and lists the drifted device with its differing paths.
3. **Given** a report-mode schedule and a drifted device, **When** any number of scheduled runs complete, **Then** no device is written to, and the device is still drifted.
4. **Given** a schedule whose run takes longer than its interval, **When** the next firing falls due while the run is still in progress, **Then** no second run starts for that use case, and the skipped firing is not queued for later.
5. **Given** a schedule already exists for a use case, **When** the operator runs create again with a different interval, **Then** the interval changes, and the schedule's response and paused state stay as they were.
6. **Given** a schedule exists, **When** the workflow engine or the worker restarts, **Then** the schedule is still there with the same interval, response and paused state, and runs resume without anyone re-creating it.

---

### User Story 2 — Control the Schedule From the Command Line (Priority: P2)

An operator needs to see what the schedule is doing and to steer it live without opening the Temporal Web UI. That means its interval, response, paused state, and recent and next runs. They can run a scan now instead of waiting, pause the schedule with a reason before planned hand work (suppress), resume it afterwards, and remove it. Every change is recorded with who made it and when.

**Why this priority**: The demo needs this. Pausing and triggering a schedule live are the beats #99 calls out. So does operations: suppress is one of the three responses VIII.2 requires, and an unexplained pause is as bad as an unexplained write. P2 because US1 delivers value with the Web UI as the only control surface.

**Independent Test**: Against a mocked workflow engine, run each control command and verify the schedule change it requests, its output, its exit code, and the audit record it leaves.

**Acceptance Scenarios**:

1. **Given** a schedule, **When** the operator asks to see it, **Then** they see the use case, interval, response, paused or active (with the pause reason), the next run time, and the outcome of the most recent runs.
2. **Given** an active schedule, **When** the operator pauses it with a reason, **Then** no scheduled run starts until it is resumed, the reason is visible wherever the schedule is shown, and the audit trail records the pause, the reason and the operator.
3. **Given** a paused schedule, **When** the operator resumes it, **Then** runs start again at the next interval, and the audit trail records the resume.
4. **Given** a report-mode schedule, **When** the operator triggers it, **Then** one run starts immediately under the schedule's current response, without waiting for the interval and without changing the interval.
5. **Given** a schedule, **When** the operator deletes it, **Then** no further runs start, a run already in progress finishes, and the audit trail records the deletion.
6. **Given** no schedule exists for the named use case, **When** any control command other than create runs, **Then** the CLI says so plainly and exits non-zero, without creating one.

---

### User Story 3 — Opt In to Self-Healing for a Use Case (Priority: P2)

Once report mode has been trusted, an operator decides a use case should heal itself. They switch its schedule to `remediate` with an explicit command that states what this authorizes and asks for confirmation. From then on, after each scheduled scan, every device with drift a deploy can fix gets the same verified deploy an operator would run. The fabric returns to intent within one interval and nobody types anything. Switching back to `report` stops all scheduled writes from the next run onward. [NEEDS CLARIFICATION: Is remediate opted into per use case (one switch covering every device in the use case, as specified here), per device (an allowlist of devices inside a use case), or globally (one switch for the whole system)?]

**Why this priority**: This is the headline property of the loop. It is P2, not P1, because it composes US1 and the existing reconcile path, and VIII.2 forbids it from being the default. It must be the last thing switched on, never the first.

**Independent Test**: Against a mocked fabric with one device drifted in a way a deploy can fix, one with undesired-only drift, and one unreachable, run a scheduled run in remediate mode. Verify that only the first device is repaired, that the other two are reported with the reason they were left alone, and that the run records the scan it acted on.

**Acceptance Scenarios**:

1. **Given** a report-mode schedule, **When** the operator switches it to `remediate`, **Then** the CLI states that scheduled runs will write to drifted devices in that use case without further confirmation and at what interval, and proceeds only on confirmation or `--yes`.
2. **Given** a remediate-mode schedule and a device changed by hand (a value intent sets was altered or removed), **When** the next scheduled run completes, **Then** that device has been redeployed and verified, a follow-up scan reports it clean, and the audit trail links the repair to the scheduled scan that found the drift and to the schedule.
3. **Given** a remediate-mode schedule and a device whose only drift is undesired config, **When** a scheduled run completes, **Then** that device is not written to, and the run records it as excluded because its drift cannot be fixed by a deploy.
4. **Given** a remediate-mode schedule and a device the scan could not evaluate (unreachable, intent unavailable), **When** a scheduled run completes, **Then** that device is not written to, and the run records it as excluded with the scan error.
5. **Given** a remediate-mode schedule and a fabric that scans clean, **When** a scheduled run completes, **Then** no repair is started.
6. **Given** a remediate-mode schedule, **When** the operator switches it back to `report`, **Then** the next run writes nothing, and the audit trail records the switch, who made it, and the previous and new response.
7. **Given** a deploy already in flight for a device, started by an operator or another run, **When** a scheduled repair targets the same device, **Then** the scheduled repair skips that device rather than failing or racing it.
8. **Given** a remediate-mode schedule, **When** the operator triggers it, **Then** the CLI asks for the same confirmation as the switch to remediate, since the triggered run may write to devices.

---

### User Story 4 — Self-Healing Stands Down When a Repair Fails (Priority: P3)

A scheduled repair fails: the deploy errors, or it applies but verification still shows forward drift. The system does not keep rewriting that device on every interval. The schedule puts itself back in `report` mode, records why, and keeps scanning, so the operator still sees the drift. Self-healing resumes only when an operator switches it on again.

**Why this priority**: Safety under failure. Without it, a device that rejects its intent is written to every interval for as long as nobody looks, and the audit trail fills with identical failures. P3 because US3 already satisfies VIII.2 and this narrows its failure mode. It can be cut from the first round without breaking the constitution.

**Independent Test**: Run a scheduled run in remediate mode where one device's repair fails verification. Verify that the schedule's response becomes `report`, the stand-down and its cause are in the audit trail and visible when the schedule is shown, and the next run writes nothing.

**Acceptance Scenarios**:

1. **Given** a remediate-mode schedule, **When** any device's scheduled repair ends in failure, **Then** the schedule's response becomes `report` before the next run, and the reason names the run and the failed devices.
2. **Given** a schedule that has stood down, **When** the operator shows it, **Then** the response reads `report` and the reason for the stand-down is displayed.
3. **Given** a scheduled repair where a device was skipped because a deploy was already in flight, **When** the run completes, **Then** this is not a failure and the schedule stays in `remediate`.

---

### Edge Cases

- **No worker is running when a run falls due.** The run waits until a worker picks it up. Because overlapping runs are skipped, firings that fall due while it waits do not pile up behind it. When the worker returns, one run executes, not a backlog.
- **The workflow engine or the host was down or asleep across several intervals.** Missed firings are not replayed once it is back. The next regular firing is the next run. A repair must never fire for a moment that has already passed.
- **The Source of Truth is unreachable.** The scheduled scan fails and is recorded as failed, and in remediate mode nothing is written. This is not a repair failure, so it does not cause a stand-down (US4). The next firing tries again.
- **An operator edits intent in the Source of Truth while the schedule is in remediate mode.** The next run sees devices lacking the new intent and deploys it. In remediate mode, a SoT edit therefore reaches the fabric within one interval without anyone running `deploy`. The confirmation in US3 scenario 1 must say so.
- **An operator makes a deliberate hand change on a remediate-mode use case.** The next run reverts it. This is the designed behaviour, since a manual change that "works" is still drift (VIII.1). The way to make a planned hand change is to pause (suppress) first, and the pause reason is the record of why.
- **A bad intent edit makes every device drift at once.** In remediate mode, every device is redeployed with the bad intent. That is the same blast radius as an operator running reconcile on the whole use case. This cut adds no proportional guard (see Assumptions). Report mode, the default, is unaffected.
- **The response is switched while a run is in progress.** That run finishes under the response it started with, and the next run uses the new one. A run never changes response partway through.
- **An operator runs `snapl scan` or `snapl reconcile` by hand while a schedule exists.** Both work as today, and scheduled and manual runs do not block each other. A manual and a scheduled deploy of the same device are serialized by the existing per-device rule, and the later one skips.
- **The schedule is created on an environment that is later reset.** A full environment reset (`invoke demo.reset`) wipes the workflow engine's state, schedules included. Re-creating the schedule afterwards is one command.
- **The interval is set too short.** An interval below the minimum is refused with a message naming the minimum. It is not silently raised.
- **Two schedules for the same use case.** Impossible. A use case has at most one drift schedule, and create on an existing one updates it.

## Requirements *(mandatory)*

### Functional Requirements

**Schedule lifecycle**

- **FR-001**: The System MUST let an operator put a use case on a drift schedule that starts a drift scan of that use case at a fixed interval, with no further operator action.
- **FR-002**: A use case MUST have at most one drift schedule. Creating a schedule for a use case that already has one MUST update the interval of the existing schedule, and MUST NOT create a second.
- **FR-003**: A newly created schedule MUST have the `report` response. No command, option or setting may create a schedule directly in `remediate`.
- **FR-004**: Re-creating, updating or re-registering an existing schedule MUST preserve its response, its paused state and its pause reason. Only an explicit pause, resume or response change alters them.
- **FR-005**: The default interval MUST be 5 minutes. The operator MUST be able to set another at creation. Intervals below 1 minute MUST be refused.
- **FR-006**: At most one scheduled run per use case MAY be in progress at any time. A firing that falls due while a run is in progress MUST be skipped, not queued.
- **FR-007**: Firings missed because the workflow engine was unavailable MUST NOT be replayed beyond one interval's worth of lateness.
- **FR-008**: Schedules MUST persist across restarts of the workflow engine and the worker, with interval, response and paused state intact.
- **FR-009**: Where the schedule is registered from is an explicit decision. [NEEDS CLARIFICATION: Should a report-only schedule exist without anyone asking for it, created by the demo bootstrap (`invoke demo.up`) or at worker start, or only when an operator creates one explicitly? This spec assumes explicit creation only.]

**Responses**

- **FR-010**: Every scheduled run MUST end in exactly one of the defined responses (`report` or `remediate`) and MUST record which one in the audit trail. A paused schedule is the `suppress` response and starts no runs.
- **FR-011**: In `report`, a scheduled run MUST perform no device write.
- **FR-012**: Switching a schedule to `remediate` MUST be an explicit operator command naming the use case. It MUST state what it authorizes (unattended writes to drifted devices in that use case, at the schedule's interval, including intent changed in the SoT) and MUST require confirmation unless `--yes` is given. With `--json`, it MUST refuse without `--yes`, consistent with spec 006 FR-012.
- **FR-013**: In `remediate`, a scheduled run MUST repair only devices whose scan found at least one difference a deploy can fix (a missing or mismatched value intent sets). Devices whose only drift is undesired config, devices the scan reported as errored, and clean devices MUST NOT be written to.
- **FR-014**: A scheduled repair MUST use the existing verified-deploy path: the same per-device deploy, verification and per-device serialization an operator reconcile uses. The schedule MUST NOT introduce a second way of writing to a device.
- **FR-015**: A scheduled run MUST NOT remove device configuration under any response. Removal of undesired config (spec 007 User Story 3, #65) is outside this feature, and stays outside any schedule once it exists (VIII.5).
- **FR-016**: A scheduled run's response MUST be fixed when the run starts. A response change MUST take effect from the next run.
- **FR-017**: Suppress MUST be available as an operator pause carrying a reason, and resumable by the operator. [NEEDS CLARIFICATION: Are calendar maintenance windows (recurring or one-off time ranges during which scheduled runs are skipped automatically) in this cut, or is an operator pause with a reason the only suppression for now? This spec assumes pause only.]
- **FR-018**: (US4) When any device's scheduled repair in a run ends in failure, the System MUST switch that schedule's response to `report` before its next run and record the reason. A device skipped because a deploy for it was already in flight MUST NOT count as a failure.

**Audit and visibility**

- **FR-019**: Every scheduled run MUST be attributable in the audit trail to the schedule that started it, with its response and the scan it ran.
- **FR-020**: In `remediate`, every scheduled run MUST record which devices it repaired and which drifted devices it excluded, with the reason for each exclusion (undesired-only, errored). Each repaired device's own audit history MUST show that the repair was scheduled, and from which scan.
- **FR-021**: Every schedule change (create, interval change, response change, pause, resume, trigger, delete, stand-down) MUST be recorded in the audit trail with its time, its actor (the operator, or the system for a stand-down), and the before and after values where they apply.
- **FR-022**: Schedule control MUST NOT depend on a worker running. An operator MUST be able to pause or switch a schedule to `report` while the worker is down.

**Operator interface**

- **FR-023**: The CLI MUST provide schedule commands to create (or update the interval of), show, set the response of, pause, resume, trigger and delete a use case's drift schedule, accepting the use case by name.
- **FR-024**: Showing a schedule MUST present its use case, interval, response, paused or active state with reason, next run time, and the outcome of recent runs. `--json` MUST emit the same as valid JSON on stdout.
- **FR-025**: Triggering a remediate-mode schedule MUST require the same confirmation as switching to `remediate`.
- **FR-026**: `snapl status` MUST report, for the use case, whether a drift schedule exists and its response and paused state. This MUST be informational and MUST NOT change the command's exit code.
- **FR-027**: The schedule commands MUST follow spec 006's output and exit-code contract (one actionable line per anticipated failure, 0/1 exit codes, `--json`). Schedule commands never exit 2: they report on the schedule, not on drift.

**Boundaries**

- **FR-028**: The existing scan, reconcile and deploy workflows and their inputs MUST NOT change. The CLI's `scan`, `reconcile` and `deploy` MUST behave exactly as before.
- **FR-029**: Workflows and models added by this feature MUST NOT name a vendor (Principle VI).
- **FR-030**: The scheduled-run logic MUST be testable without a live workflow-engine cluster, Source of Truth or devices.

### Key Entities

- **Drift Schedule**: The standing instruction to scan one use case at a fixed interval. Has an identity derived from the use case, an interval, a response, a paused flag with a reason, and a history of recent and upcoming runs. Lives in the workflow engine, not the Source of Truth: it is operational policy, not intent.
- **Response**: What a scheduled run does with what it found. `report` (default) or `remediate`. A paused schedule is the `suppress` response.
- **Scheduled Run**: One firing of a schedule. A scan of the use case, then the response applied to its findings. Records its trigger, its response, the scan it ran, and in `remediate` the devices it repaired and excluded.
- **Remediation Eligibility**: The rule picking which drifted devices a scheduled repair may touch: at least one difference a deploy can fix, and not errored.
- **Exclusion**: A drifted device a scheduled repair deliberately left alone, with its reason (undesired-only, errored).
- **Stand-down**: The schedule switching itself from `remediate` to `report` after a failed repair, with the reason recorded.
- **Schedule Change**: An audit record of an operator or system action on a schedule: what changed, from what, to what, by whom, when.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With a report-mode schedule on the six-device dcfabric fabric, a hand-made change is recorded in the audit trail within one interval plus one scan duration of being made, with no operator command in between.
- **SC-002**: Over any number of report-mode runs against a drifted fabric, zero device writes occur and the drifted device stays drifted.
- **SC-003**: With a remediate-mode schedule, a hand-made change that a deploy can fix is reverted, and the fabric scans clean, within two intervals, with no operator command in between.
- **SC-004**: 100% of newly created schedules start in `report`. Reaching `remediate` takes an explicit command naming the use case plus a confirmation, and leaves an audit record naming the operator.
- **SC-005**: A device whose only drift is undesired config receives zero writes from scheduled runs, across any number of runs in either response.
- **SC-006**: At most one scheduled run per use case is ever in progress, including when a run outlasts its interval and when the worker returns after downtime.
- **SC-007**: A paused schedule starts zero runs until resumed. Pause, resume, response change, trigger, stand-down and delete each leave one audit record with actor and time.
- **SC-008**: After the workflow engine and the worker restart, 100% of schedules keep their interval, response and paused state.
- **SC-009**: (US4) After a failed scheduled repair, the next run performs zero device writes until an operator switches `remediate` on again.
- **SC-010**: An operator can create, inspect, pause, resume, trigger and switch a schedule using only `snapl` commands. The Temporal Web UI is optional.
- **SC-011**: Unit tests achieve ≥80% line coverage of the new code without a live workflow-engine cluster, SoT or devices.

## Assumptions

- **Opt-in granularity is per use case** (pending the US3 clarification). One drift schedule per use case carries one response. Per-device allowlists and a global switch are not built.
- **Suppress is an operator pause with a reason** (pending the FR-017 clarification). Calendar windows are not built. The workflow engine can express them later (the plan records how) without a new schedule model.
- **Schedules are created only by an explicit operator command** (pending the FR-009 clarification). The demo bootstrap and the worker do not create schedules. The worker only runs what schedules start.
- **Default interval is 5 minutes**, from #99's demo-cadence suggestion, set per schedule at creation. The minimum is 1 minute. A six-device scan completes in seconds, and 005 SC-002 bounds a 12-device scan at 3 minutes. An environment variable for the interval is not added: the interval is a property of each schedule and is shown wherever the schedule is.
- **The response lives on the schedule, not in worker configuration.** #99 suggested a worker environment variable (`SNAPL_AUTO_RECONCILE`). An environment variable is invisible from the CLI and the Web UI, applies to every use case at once, needs a worker restart to change, and silently enables writes when a shell with a stale export restarts the worker. Keeping the response on the schedule makes it visible, per use case, switchable live and audited.
- **A repair is a reconcile.** The scheduled repair goes through the existing reconcile path, so it inherits per-device serialization, skip-on-in-flight (#35) and the dedupe and failure handling from #66, unchanged.
- **No proportional blast-radius guard in this cut.** A scheduled repair of N devices carries the same risk as `snapl reconcile --drifted --yes` on the same fabric. The remediate confirmation says so. A guard such as "stand down when more than half the fabric drifts at once" is a candidate follow-up, not part of this feature.
- **Stand-down returns the schedule to `report`, not to paused.** Detection keeps running after a failed repair, so the operator still sees the drift that could not be healed.
- **The audit trail is the existing durable audit log.** Schedule changes are written to it by the Orchestrator's schedule functions, which the CLI calls. CLI and worker share one audit database path, as `snapl audit` already assumes.
- **Temporal history persists across restarts** (#81, fixed in #112), so schedules survive a container restart. `invoke demo.reset` wipes them, along with everything else.
- **Only `dcfabric` is exercised.** Schedules take a use case and must not hard-code `dcfabric`.
- **Authentication and authorization stay out of scope**, as in 005 and 006. Whoever can run `snapl` can switch a schedule to remediate. The audit record of who did is the control.

## Dependencies

- **Implements**: #99. This spec is tracked by its own issue; #99 closes with the implementation.
- **Builds on**: spec 005 (ScanDrift, ReconcileDevices, DeployIntent, the durable audit log), spec 006 (the CLI's output, confirmation and exit-code contracts), spec 007 (the `undesired` drift direction that remediation eligibility reads).
- **Depends on, already landed**: #112 (Temporal persistence, #81), #105 (reconcile hardening, #66 and #35), #124 (verification fails only on forward drift, #121).
- **Bounded by**: #65. Removal of undesired config is never scheduled and never automatic.
- **Epic**: #103 (MVP demo readiness), "Explicit MVP scope decisions: auto-remediation default".
