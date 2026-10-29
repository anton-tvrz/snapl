# Quickstart: Scheduled Drift Scans and Opt-In Remediation

**Feature**: 009-scheduled-drift-response

This describes the feature **as it will behave once implemented**. None of these commands exist yet.

## Prerequisites

The demo environment from `docs/demo-scenarios.md`, converged before you start:

```bash
uv run invoke demo.up                 # stack + lab + seed + report-mode schedule + preflight
uv run invoke orchestrator.start      # worker, in its own terminal
snapl reconcile --use-case dcfabric --drifted --yes   # converge first
snapl scan --use-case dcfabric        # expect: 6 clean
```

## Unit tests

```bash
uv run pytest tests/unit/test_orchestrator/test_schedules.py \
              tests/unit/test_orchestrator/test_remediation.py \
              tests/unit/test_orchestrator/test_workflow_drift_response.py \
              tests/unit/test_presentation/test_cli_schedule.py \
              tests/unit/test_tasks/test_demo.py -m unit -v
```

No Temporal cluster is needed. `DriftResponse` runs in the time-skipping environment with mocked activities. Schedules themselves are tested as pure builders and against a fake client, because the time-skipping test server does not implement schedules (research R11).

The one schedule round trip against a real server:

```bash
uv run pytest tests/integration/test_orchestrator/test_schedules_local.py -m integration -v
# uses WorkflowEnvironment.start_local(); downloads the dev-server binary on first run
```

## 1. Watch the fabric (report mode, the default)

```bash
snapl schedule show                                    # demo.up already created it: every 5m, report
snapl schedule create --use-case dcfabric --every 1m   # optional: demo cadence; changes only the interval
```

**Expect**: `response report`, `state active`, next runs one minute apart. Recent runs read like `6 devices: 6 clean, 0 drifted, 0 errored   wrote: no`. In the Temporal Web UI (http://localhost:18034), go to **Schedules** and open `drift-response-dcfabric`.

Break something by hand, then wait. Do not run `snapl scan`:

```bash
docker exec clab-dcfabric-spine-01 sr_cli -ec \
  'set / interface ethernet-1/1 admin-state disable'
```

Within one minute:

```bash
snapl audit --device spine-01
```

**Expect**: the scheduled scan's `detect_drift` event for spine-01 under workflow `drift-response-dcfabric-<time>-scan`. The run's use-case-level `drift_response` event (`mode=report`, attributed to `schedule:drift-response-dcfabric`) is under `snapl audit --workflow drift-response-dcfabric-<time>`; device-level `drift_response` records are written only in remediate mode (FR-020). `snapl scan` still reports spine-01 drifted: report mode wrote nothing.

## 2. Steer it

```bash
snapl schedule pause --reason "maintenance: spine-01 optics"   # suppress
snapl schedule show                                             # state paused — "maintenance: ..."
snapl schedule resume
snapl schedule trigger                                          # one run now; completed in `show` within 30s (SC-012)
snapl audit --workflow drift-response-dcfabric                  # the schedule's policy history
```

## 3. Opt in to self-healing

```bash
snapl schedule mode remediate --use-case dcfabric
# prompts with what this authorizes; answer y (or pass --yes)
```

The next run (or `snapl schedule trigger --yes`) redeploys spine-01. Then:

```bash
snapl scan                     # expect: 6 clean, without anyone running reconcile
snapl audit --device spine-01  # drift_response decision=remediated, then the DeployIntent events
```

**Temporal UI**: the scheduled run has a child `ScanDrift` and a child `ReconcileDevices`, which has the per-device `DeployIntent` child.

Now add config intent never asked for, which only a delete could fix:

```bash
docker exec clab-dcfabric-spine-01 sr_cli -ec \
  'set / interface ethernet-1/7 description hand-added'
```

**Expect** on the next runs: spine-01 is reported drifted with an undesired path, and `snapl audit --device spine-01` shows `decision=undesired_only`. No `DeployIntent` is started for it, and no run ever removes the description.

Switch back:

```bash
snapl schedule mode report
```

## 4. A repair that fails

Stand-down is deferred. In this cut, a failed scheduled repair shows as `failed` in `snapl schedule show`, and the schedule stays in `remediate`. The next run tries again. The workflow unit test covers this. A live check is optional: switch to `report` or pause to stop the retries.

## Live evidence the implementation PR must state (Principle III)

This feature adds device writes nobody types, so the implementation PR lists what was run on the dcfabric lab and what was seen, at least:

1. **Report mode is read-only.** Run 3 or more scheduled runs against a drifted spine. The device is still drifted afterwards, and the worker log shows no `apply_config` activity.
2. **Remediate heals forward drift** within two intervals (SC-003), with the audit links scan → reconcile → deploy.
3. **Undesired-only drift is never written.** On the `ethernet-1/7` case above, no `DeployIntent` starts across 3 or more runs (SC-005).
4. **Overlap.** With the interval set to 1 minute and a worker that is stopped for 3 minutes and then restarted, exactly one run executes on return, and `skipped (overlap)` counts the rest (SC-006).
5. **Bootstrap.** `invoke demo.up` on a reset environment leaves one report-mode schedule, and `demo.check` reports it. Pause it, run `demo.up` again, and the pause and its reason survive (SC-013).
6. **Persistence.** After `docker restart snapl-temporal-1` (or the compose service), `snapl schedule show` reports the same interval, response and paused state (SC-008).

Captured real `ScheduleDescription` payloads from step 5 become test fixtures if any decode bug surfaces (Principle III: bugs found live leave a fixture behind).

## Docs to update with the implementation

- `docs/demo-scenarios.md`: a new scenario, "The loop closes itself" (steps 1 to 3 above), and a demo-arc variant where beat 3 is the schedule rather than `snapl reconcile`.
- `README.md` and the known-limitations list: say that scheduled remediation never removes config (#65), and that remediate is opt-in per use case.
