# Research: Scheduled Drift Scans and Opt-In Remediation

**Feature**: 009-scheduled-drift-response
**Date**: 2026-10-28

All findings below were checked against the code on `main` at `601a014` and against the `temporalio` SDK pinned in `uv.lock` (**1.25.0**, installed in the workspace venv). No live lab, Temporal cluster or Infrahub was run to produce this document.

## R1: Temporal Schedules, not a workflow loop

**Decision**: Use Temporal's native **Schedules** (`Client.create_schedule`). Each use case gets one schedule, with an interval spec and a `ScheduleActionStartWorkflow` action.

**What the pinned SDK provides** (`temporalio/client.py`, 1.25.0):

| need | SDK surface |
| --- | --- |
| create | `Client.create_schedule(id, schedule, *, trigger_immediately=False, ...)` raises `ScheduleAlreadyRunningError` if the id exists |
| address an existing schedule | `Client.get_schedule_handle(id) -> ScheduleHandle` |
| suppress / un-suppress | `ScheduleHandle.pause(note=...)`, `ScheduleHandle.unpause(note=...)` |
| run now | `ScheduleHandle.trigger(overlap=None)` |
| change interval, action args, policy | `ScheduleHandle.update(updater)`, where the updater receives the current `ScheduleDescription` and returns a `ScheduleUpdate` |
| inspect | `ScheduleHandle.describe() -> ScheduleDescription` (`schedule.state.paused`, `schedule.state.note`, `info.recent_actions` (last 10), `info.next_action_times` (next 10), `info.num_actions_skipped_overlap`) |
| remove | `ScheduleHandle.delete()` |
| overlap | `SchedulePolicy.overlap: ScheduleOverlapPolicy`, default `SKIP` |
| missed firings | `SchedulePolicy.catchup_window`, **default `timedelta(days=365)`** |
| interval | `ScheduleSpec(intervals=[ScheduleIntervalSpec(every=...)])` |
| future windows | `ScheduleSpec.skip: Sequence[ScheduleCalendarSpec]` (calendar exclusions) |

The dev server snapl runs (`temporalio/temporal:latest`, `server start-dev`, `development/docker-compose.yml`) includes the scheduler. History is persisted to SQLite on the `temporal_data` volume since #112 (#81), so schedules survive a container restart, which FR-008 needs. `invoke demo.reset` removes the volume, and the schedule with it.

`packages/orchestrator/pyproject.toml` declares `temporalio>=1.7`. The deferred stand-down design would also use `temporalio.activity.client()` (R8), which is present in 1.25.0. The implementation should raise the floor to the locked version rather than assert which older release first shipped each API.

**Rationale**:
- **Visible and controllable outside snapl.** The cadence, paused state, pause note, recent runs and next runs all show in the Temporal Web UI with no snapl code. That is the demo beat #99 asks for.
- **Survives the worker.** The schedule is cluster state. A worker restart or crash changes nothing about when scans fire. Firings that fall due while no worker is polling wait in the task queue, and under `SKIP` they do not pile up behind each other (R4).
- **Pause, trigger and overlap come native.** Hand-rolling them would rebuild what the server already does.
- **Each run is an ordinary workflow execution.** It has its own history, is replayable, and shows in `snapl status` and the Web UI like any other.

**Alternatives considered**:
- **A long-running workflow that loops with `workflow.sleep(interval)`, using continue-as-new to bound history.** Pausing and triggering would need hand-written signals, overlap would be implicit, and the cadence would be invisible in the UI except as a sleeping workflow. A worker deploy that changes the loop's code would need workflow versioning. Rejected.
- **Legacy `cron_schedule` on `start_workflow`.** Superseded by Schedules. No pause, trigger, note or overlap control, and changing it means terminating and restarting the cron workflow. Rejected.
- **OS cron calling `snapl scan`.** Lives outside Temporal, is invisible in the UI, does not survive moving hosts, and cannot carry a response mode or be paused from snapl. Rejected.

## R2: Schedule a new wrapper workflow, not ScanDrift directly

**Decision**: Add one workflow, **`DriftResponse`** (`DriftResponseWorkflow`), and make it the schedule's action in **both** responses. It takes a `DriftResponseRequest(use_case_id, mode, schedule_id)` and:

1. runs `ScanDriftWorkflow` as a **child** (`id=f"{wf_id}-scan"`);
2. in `report`, records the outcome and stops;
3. in `remediate`, selects eligible devices (R5) and runs `ReconcileDevicesWorkflow` as a child (`id=f"{wf_id}-reconcile"`) for them. (Standing the schedule down after a failed repair is deferred, R8.)

**Rationale**:
- **`ScanDrift.run(use_case_id)` stays untouched** (FR-028). The CLI's `snapl scan` and `reconcile --drifted` call that signature. Adding a mode argument would touch workflow code that has in-flight histories, which raises replay concerns, for a capability the manual path does not need.
- **One workflow type in both responses.** Switching response changes the action's arguments, not its workflow type, so the Web UI shows one continuous series of runs. Every scheduled run, including report-only ones, records its response, which is what VIII.2 asks for (FR-010).
- **Overlap covers scan and repair together.** With `SKIP` (R4), a slow repair keeps the next scan from starting, so a scan never runs while a repair is still writing.
- **Children, not inlined activities.** The scan and the repair keep their own workflow ids, audit trails and Web UI pages. A demo can click from the scheduled run to the scan to the per-device deploys.

**Alternatives considered**:
- **Schedule `ScanDrift` directly in report mode and `DriftResponse` only in remediate mode.** Flipping the response would change the workflow type and split the run history in two. Report-mode runs would not record their response. Rejected.
- **Add an optional `mode` parameter to `ScanDrift`.** Changes a workflow signature the CLI depends on, mixes a write path into a workflow documented as read-only, and touches replay. Rejected.
- **Signal a long-lived "patrol" workflow from the schedule.** This reintroduces the loop design rejected in R1. Rejected.

## R3: The response lives in the schedule's action arguments

**Decision**: The response mode is a field of `DriftResponseRequest`, the single argument the schedule passes to every run it starts. Changing the response means `ScheduleHandle.update(...)` with a rebuilt action carrying the new request. The action's `static_summary` is set to a human string (`"drift response: report"` or `"drift response: remediate"`) so the mode also reads at a glance in the Web UI. Reading the mode back (`snapl schedule show`) decodes the action's raw argument payload with the client's data converter (`ScheduleDescription.schedule.action.args` arrive as raw `Payload`s when described).

**Rationale**:
- **Visible** in the Web UI and in `snapl schedule show`.
- **Per schedule, so per use case**, which matches the settled answer to the granularity question (R10).
- **Switchable live**, with no worker restart.
- **Deterministic.** The workflow receives its response as input and never reads the environment, so replay is stable and a run cannot change response partway through (FR-016).

**Alternatives considered**:
- **`SNAPL_AUTO_RECONCILE` on the worker**, as #99 suggested. Invisible to the CLI and the UI, global across use cases, and needs a restart to change. Worst, a restarted worker that inherits a stale export enables unattended writes nobody chose at that moment. Rejected. It also contradicts VIII.2's "explicitly automated": an environment variable is not an act anyone can be seen to take.
- **Schedule memo.** Visible too, but the workflow would have to read its own schedule's memo, and the mode would be stored twice. Rejected.
- **An attribute in the Source of Truth (Infrahub).** Response policy is operational policy, not intended network state (VIII.1 keeps intent and operational concerns apart). It would also need a schema change and a SoT write path. Rejected for this cut.

## R4: Overlap SKIP, and a catch-up window of one interval

**Decision**: `SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP, catchup_window=<interval>, pause_on_failure=False)`.

**Rationale**:
- `SKIP` is the SDK default. It is stated explicitly anyway, because it is load-bearing for FR-006 and SC-006: a run that outlasts its interval makes the next firing a no-op, and nothing is buffered. `BUFFER_ONE` would run a stale scan straight after a slow one, and `ALLOW_ALL` would run two repairs of the same fabric at once.
- **The SDK's default catch-up window is 365 days** (`SchedulePolicy.catchup_window: timedelta = timedelta(days=365)`). After a laptop sleeps or Temporal is down, the server would try to start the firings it missed. For a report scan that is waste. For a remediate run it would be a write "for" a moment that has passed. Setting the window to one interval means a firing more than one interval late is dropped, and the next regular firing is the next run (FR-007).
- `pause_on_failure=False`. A failed scan (SoT unreachable) must not stop detection. A failed repair does not pause the schedule either. Stand-down is deferred (R8), and pausing would stop detection.

**Alternatives considered**: `pause_on_failure=True` as the failure breaker. See R8.

## R5: Remediation eligibility is forward drift only

**Decision**: In `remediate`, a device is eligible when its `DriftReport.status == DRIFTED` **and** at least one `DriftItem` has `undesired=False`. Every other DRIFTED device is excluded with `ExclusionReason.UNDESIRED_ONLY`, and every ERROR device with `ExclusionReason.ERRORED`. CLEAN devices are neither.

**Rationale**:
- `DriftItem.undesired` (spec 007) is exactly the bit that separates "a deploy fixes this" from "only a delete fixes this". `snapl_observability.models.DriftItem` documents it for this purpose.
- `DeployIntentWorkflow` already treats undesired-only drift after an apply as success, with "removal is not automatic" (#121, #124). The device stays DRIFTED in every scan. Without this rule, a remediate schedule would deploy to it on every interval forever and change nothing (SC-005).
- ERROR means the scan could not see the device. Writing to a device whose state is unknown is a guess, not a remediation.
- This deliberately differs from the CLI's `snapl reconcile --drifted`, which takes every DRIFTED device. A human running that command reads the scan first. A schedule does not.

**Alternatives considered**:
- **Every DRIFTED device**, which mirrors the CLI. Causes the infinite rewrite above. Rejected.
- **Remediate only the forward items**, by deploying a partial intent. A deploy applies the device's whole intent and is idempotent (VIII.4), so per-item deploys would add a new write shape for no benefit. Rejected.

## R6: Repair via the ReconcileDevices child

**Decision**: `remediate` runs `ReconcileDevicesWorkflow.run(eligible_ids)` as a child. It does not start `DeployIntent` children itself.

**Rationale**: FR-014. Reconcile already provides, tested:
- per-device serialization through the `deploy-intent-{device_id}` child id;
- skip on a deploy already in flight (`WorkflowAlreadyStartedError` gives a skip, #35);
- dedupe and the structured `DEVICE_NOT_FOUND` skip (#66);
- per-device failure isolation and a `ReconcileResult` with succeeded, failed and skipped counts.

A second write path would have to re-earn all of that. The deferred stand-down rule would read `ReconcileResult.failed > 0` directly, and skips would not count as failures.

## R7: Registration is explicit and idempotent, and preserves operator state

**Decision**: `snapl_orchestrator.schedules.ensure_drift_schedule(client, use_case_id, interval, task_queue, audit_log, actor)`:

1. tries `client.create_schedule(schedule_id, build_drift_schedule(...))` with mode `report` (FR-003);
2. on `ScheduleAlreadyRunningError`, calls `handle.update(updater)`. The updater replaces **only** `spec` (the interval) and `policy.catchup_window`, and carries over the existing `action` (and so the response) and `state` (`paused`, `note`) unchanged (FR-004);
3. records a `SCHEDULE_CHANGED` audit event (`created`, or `interval_changed` with from and to).

Two callers, both explicit acts: `snapl schedule create`, and `invoke demo.up` after it seeds a use case (settled 2026-10-28, FR-009). The worker does not register schedules at boot. Before creating or updating, both callers identify the Source of Truth with `snapl_intent.infrahub.identity.identify` and refuse on `FOREIGN`, as `demo.seed` does (FR-031). `UNKNOWN` (unreadable) does not refuse, which matches the identity module's "never a false foreign" rule.

`demo.up` passes the actor as `invoke:demo.up (<os user>)`. It resolves the audit database exactly as the worker does (`SNAPL_AUDIT_DB`, default `./snapl-audit.sqlite`, repo root), so it may create the file on a clean checkout. The CLI's refusal on a missing audit DB (contracts/cli-schedule.md) is still right for the CLI, which can run from any directory. `demo.up` always runs from the repo root that `orchestrator.start` also uses, so it can't write to a mismatched path.

**Rationale**:
- **Preserving state is the correctness property.** A naive "create or replace" would silently un-pause a suppressed schedule, or flip a remediate schedule back to report (or the reverse), whenever someone re-runs setup.
- **Not at worker boot.**
  - With two workers, both register, and the last one's settings win.
  - The worker would need to know which use cases to schedule, which means another environment variable deciding policy (R3's objection again).
  - A worker restart would become a moment that can change cluster policy.
  - Registration is an operator act on the loop, so under spec 006 FR-018 it belongs to `snapl`, not to `invoke`.
- **Schedule id is `drift-response-{use_case_id}`**, which gives one schedule per use case by construction (FR-002). Temporal appends the scheduled time to the action's workflow id for each run (`drift-response-dcfabric-2026-10-28T03:05:00Z`), so runs never collide with each other or with the CLI's `scan-drift-*` and `reconcile-*` ids.

**Alternatives considered**:
- **A separate `invoke orchestrator.schedules` task**, #99's other option. Not needed. `demo.up` calls the same `ensure_drift_schedule` the CLI does, so there is one registration path. A standalone task would just add a third.
- **`trigger_immediately=True` on create.** Not default. `snapl schedule trigger` is the explicit way to run now, and it keeps "create" free of side effects on the fabric.

## R8: Schedule control runs in the Orchestrator module, outside workflows; stand-down is deferred

**Decision**:
- **Operator control** (`create`, `set_response`, `pause`, `resume`, `trigger`, `delete`, `describe`) is plain async functions in `snapl_orchestrator/schedules.py`. Each takes a connected `Client` (from `worker.client.build_client`, so the pydantic converter encodes `DriftResponseRequest`) and an `AuditLog`, makes the Temporal call, and appends a `SCHEDULE_CHANGED` `AuditEvent`. The `actor` is passed in by the caller. The CLI passes the OS user (`getpass.getuser()`). The CLI is a thin client over these functions, as it is over workflows today.
- **Stand-down (former FR-018) is deferred** to #136 (decided 2026-10-28). The design below is recorded so the follow-up doesn't have to rediscover it. It would be an activity, `stand_down_schedule(schedule_id, reason)`. It would use `temporalio.activity.client()`, the worker's own client, to update the schedule's action to `report` and set `state.note` to the reason. It would leave `paused` alone, then append a `SCHEDULE_CHANGED` event with `actor="snapl:stand-down"`. `DriftResponse` would call it after a reconcile child returns `failed > 0`. Until then, a failed repair is attempted again on the next run (spec Edge Cases).

**Rationale**:
- **FR-022: control must work with the worker down.** "Stop the automatic writes now" is the command most likely to be needed when something is wrong, and that is also when the worker may be the thing that is wrong. Routing control through a workflow would make pause depend on a worker.
- **Audit stays in the Orchestrator's durable log.** `presentation → orchestrator` is the allowed direction, and the CLI already opens `SqliteAuditLog` for `snapl audit`. SQLite runs in WAL mode (`audit/sqlite.py`), so the CLI appending while the worker writes is safe.
- **(Deferred design.) Stand-down must happen from inside the run**, before the next firing, with no human in the loop. An activity is the only place workflow code may do I/O. `activity.client()` avoids adding a `Client` to the `Activities` container. The activity is ordinary orchestration code that names no vendor.
- **(Deferred design.) Stand-down returns to `report` rather than pausing.** Detection keeps running, so the drift that could not be healed stays visible (spec Assumptions).

**Alternatives considered**:
- **`pause_on_failure=True` plus a deliberately failing `DriftResponse`.** It is native and needs no activity. But it pauses detection along with repair, and it would equally pause report-mode schedules whenever the SoT blips. It also relies on a workflow failing on purpose, whereas every snapl workflow today returns structured results. Rejected.
- **No breaker; failed repairs simply retry next interval.** Idempotent applies make that harmless to the device, but it rewrites a device that rejects its intent every interval and buries the audit trail in identical failures. **This is the chosen behaviour for this cut**, until the stand-down follow-up lands.
- **A `ConfigureSchedule` workflow for operator changes.** Fails FR-022. Rejected.

## R9: Suppress is pause with a note; calendar windows are expressible later

**Decision**: Suppress is `ScheduleHandle.pause(note=reason)`. `snapl schedule pause` requires a reason (`--reason`), so no pause is unexplained, and it records it in the audit trail. Resume is `unpause(note=...)`. Calendar maintenance windows are **not** built in this cut (settled 2026-10-28, FR-017).

**If windows are wanted**: `ScheduleSpec.skip` takes `ScheduleCalendarSpec` exclusions (for example, every Sunday 02:00–04:00), and the server then skips matching firings itself. That fits inside `build_drift_schedule` and `ensure_drift_schedule`'s updater without a new model. The cost is a CLI surface for expressing windows and showing them. One-off windows map to `pause` now and `unpause` later, or to `ScheduleSpec.skip` with a single range.

## R10: Clarifications, settled with the maintainer on 2026-10-28

The spec was first written unattended, with provisional answers. The maintainer settled all three, and also deferred the stand-down story (R8).

| # | question (spec) | decision | cost of reversing later |
| --- | --- | --- | --- |
| 1 | Opt-in granularity: per use case, per device, or global (US3) | **Per use case**: one schedule per use case, one response per schedule | *Per device*: `DriftResponseRequest` gains `remediate_devices: list[str]`, eligibility intersects with it, and the CLI gains `--device`. *Global*: one schedule over all use cases, or a check across schedules. Both are contained in `models.py`, `schedules.py` and the CLI. |
| 2 | Calendar maintenance windows in this cut (FR-017) | **No: pause with a reason is the only suppress** | `ScheduleSpec.skip` plus CLI flags (R9). No workflow change. |
| 3 | Who registers schedules: operator only, or also `demo.up` or worker start (FR-009) | **`snapl schedule create` and `invoke demo.up`; never the worker** | `demo.up` calls `ensure_drift_schedule(...)`, which always creates in `report` and stays idempotent and state-preserving under R7. Worker-start registration is rejected (R7). |

## R11: Testing strategy, given that the time-skipping server has no Schedules

**Finding**: The existing workflow tests use `WorkflowEnvironment.start_time_skipping()`. That environment runs Temporal's Java test server. Temporal's own SDK test suite (`temporalio/sdk-python`, `tests/test_client.py`) guards every schedule round-trip test with:

```python
if env.supports_time_skipping_v1:
    pytest.skip("Java test server doesn't support schedules")
```

So schedules cannot be exercised in snapl's existing unit-test environment. `WorkflowEnvironment.start_local()` (the dev-server binary, downloaded on first use) does support them.

**Decision**:
- **Pure unit tests (no server).** `build_drift_schedule()` returns a `Schedule` dataclass. Assert its interval, `SKIP`, the catch-up window equal to the interval, the action's workflow type, id, task queue, request argument, `static_summary`, and `state.paused=False`. Assert the updater functions (interval change, response change) preserve `state` and the parts they should not touch.
- **Mock-client unit tests.** `schedules.py` functions against a fake `Client` and `ScheduleHandle`: create, then `ScheduleAlreadyRunningError`, then update. Pause requires a reason. Every function appends exactly one `SCHEDULE_CHANGED` event with actor and from/to.
- **Workflow unit tests** (`start_time_skipping`, mocked activities, the existing pattern). `DriftResponse` is started directly with a `DriftResponseRequest`. The schedule is only its trigger, so no schedule is needed to test the response logic.
- **Bootstrap unit tests** (`tests/unit/test_tasks/test_demo.py`, the existing pattern). `demo.up` calls `ensure_drift_schedule` after seeding and refuses a foreign SoT. `demo.check` fails on a missing or paused schedule and shows the pause reason.
- **Run summaries.** `describe_drift_schedule` fetches each completed run's `DriftResponseResult` to build its one-line summary (FR-024). Against the fake client, assert the summary text and the `wrote` flag for a report run, a remediate run with repairs, and a remediate run where nothing was eligible.
- *(Deferred with stand-down: the activity test would use `ActivityEnvironment(client=fake_client)`. 1.25.0's `ActivityEnvironment.__init__` accepts `client`.)*
- **Integration test** (`@pytest.mark.integration`). One round trip against `WorkflowEnvironment.start_local()`: create, describe, pause, unpause, trigger, update the response, describe, delete. It proves the real server accepts the built `Schedule` and that the response decodes back.
- **Live evidence (Principle III).** This feature starts device writes on its own and touches the drift boundary's consumer, so the implementation PR states what was run on the dcfabric lab (see quickstart.md, "Live evidence").

## R12: Workflow-sandbox and model hygiene

**Decision**: New models (`DriftResponseMode`, `ExclusionReason`, `DriftResponseRequest`, `DriftResponseResult`) go in `snapl_orchestrator/models.py`. `DriftResponseWorkflow` imports them, the activities and the child workflows under `workflow.unsafe.imports_passed_through()`, as `scan_drift.py` and `reconcile_devices.py` do. Runtime-needed type imports in models keep `# noqa: TC003`. Ruff's TC rules would otherwise move them under `TYPE_CHECKING`, and pydantic then fails inside the sandbox with "not fully defined" and the workflow appears to hang (the existing `worker/sandbox.py` docstring documents the failure mode). Workflow tests use `build_workflow_runner()`, like every existing workflow test.

**Rationale**: This is the known trap for Temporal and pydantic in this repo. `DriftResponseResult` nests `DriftScanResult` and `ReconcileResult`, which nest observability models. These are already passthrough (`PASSTHROUGH_MODULES`), so no sandbox change is needed.

## R13: Corrections to premises in the issue

Some things #99 and #103 describe have changed since they were filed. The plan uses the current state:

- **#81 is fixed** (#112). Temporal history persists, so schedules are no longer lost on a container restart. They are lost on `demo.reset`, which is intended.
- **#63 has landed** (#106). The CLI exists, and this feature adds a `snapl schedule` command group to it, as spec 006 anticipated ("schedule control is a follow-up command set").
- **#66 and #35 have landed** (#105). Reconcile already skips in-flight per-device deploys, which R6 depends on.
- **#119 has landed** (#123). Workflows already import no vendor seam, and `tests/unit/test_orchestrator/test_vendor_seams.py` automatically covers any new file under `workflows/`.
- **Spec 007 detection has landed** (#114) and **#121** (#124). Undesired drift exists and does not fail a deploy's verification. That is what makes R5 necessary.
