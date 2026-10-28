# Contract: CLI — `snapl schedule`

**Feature**: 009-scheduled-drift-response
**Date**: 2026-10-28
**Type**: Typer command group in `snapl-presentation`

A `schedule` sub-application is added to the existing `snapl` Typer app. Every command follows spec 006's contracts, unchanged:

- connect through `_connect` (TCP probe, then `build_client`);
- one actionable line per anticipated failure (`CliError`);
- exit codes 0 and 1 only. A schedule command reports on the schedule, never on drift, so it never exits 2;
- `--json` puts only valid JSON on stdout;
- `--use-case` defaults to `dcfabric`, is never hard-coded, and is resolved by name.

None of these commands calls `_require_worker`. A schedule can be created, paused or switched to `report` with no worker running (FR-022). `create` and `show` print a one-line warning on stderr when nothing is polling the task queue, because runs will wait.

The audit log is opened as `snapl audit` opens it (`SqliteAuditLog(settings.audit_db)`). Unlike `audit`, a missing file is created: the CLI is now a writer, and the worker initialises the same schema. The actor is `getpass.getuser()`.

## Commands

| command | does | confirmation | exit |
| --- | --- | --- | --- |
| `snapl schedule create [--use-case UC] [--every 5m]` | `ensure_drift_schedule`. Prints the view and the next run time. Re-running changes only the interval. | none: always `report` | 0, or 1 on error (interval below 1m, Temporal unreachable) |
| `snapl schedule show [--use-case UC] [--json]` | `describe_drift_schedule` | none | 0; 1 if no schedule |
| `snapl schedule mode {report\|remediate} [--use-case UC] [--yes] [--json]` | `set_drift_response` | **`remediate` only**: prompt unless `--yes`; with `--json`, refuse unless `--yes` | 0; 1 if no schedule or aborted |
| `snapl schedule pause --reason TEXT [--use-case UC]` | `pause_drift_schedule` (suppress). `--reason` is required. | none | 0; 1 if no schedule |
| `snapl schedule resume [--use-case UC] [--note TEXT]` | `resume_drift_schedule` | none | 0; 1 if no schedule |
| `snapl schedule trigger [--use-case UC] [--yes]` | describe, then `trigger_drift_schedule`. Returns at once and prints the workflow-id prefix to follow. | **when the schedule is in `remediate`**: as for `mode remediate` | 0; 1 if no schedule or aborted |
| `snapl schedule delete [--use-case UC] [--yes]` | `delete_drift_schedule` | prompt unless `--yes` (removing detection is consequential, not destructive to devices) | 0; 1 if no schedule or aborted |

`--every` accepts `<n>s`, `<n>m` or `<n>h` (for example `90s`, `5m`, `1h`) and is parsed in Presentation. The orchestrator takes a `timedelta`.

## The remediate confirmation text

The wording is part of the contract, because FR-012 requires it to state what is being authorized:

```
Switch dcfabric to remediate?
  Every 5m, devices whose scan shows drift a deploy can fix will be redeployed
  without asking — including devices whose intent was changed in the Source of
  Truth. Undesired config is never removed. Devices: 6.
  A failed repair switches the schedule back to report.
Continue? [y/N]
```

The device count comes from the same SoT lookup `snapl deploy` uses (`load_states`). If the SoT is unreachable, the count line reads `Devices: unknown (SoT unreachable)`. The command does not fail on it: switching to remediate does not itself write anything.

## `show` rendering (human)

```
drift schedule  drift-response-dcfabric
use case        dcfabric
every           5m
response        report
state           active                       # or: paused — "maintenance: spine swap"
next runs       03:10:00Z  03:15:00Z  03:20:00Z
recent runs     03:00:00Z completed  drift-response-dcfabric-2026-10-28T03:00:00Z
                03:05:00Z running    drift-response-dcfabric-2026-10-28T03:05:00Z
skipped (overlap) 0
```

`remediate` renders in the warning colour on a TTY. A stood-down schedule shows `response report` and the stand-down reason in `state`'s note line.

## `--json` shape (`show`, `create`, `mode`)

`DriftScheduleView.model_dump(mode="json")`: `interval` in seconds, datetimes in ISO 8601 UTC.

## `snapl status` addition (FR-026)

One extra row in the existing checks list, always `ok=True`, so it never changes the exit code:

```
drift schedule      dcfabric: every 5m, report, active
drift schedule      none — snapl schedule create
```

A Temporal error while looking the schedule up renders as `drift schedule  could not inspect: <first line>`, also `ok=True`. `status` already has a `temporal` row that fails on its own.

## Rendering new audit events

`snapl audit` already renders `event_type.value` generically. `schedule_changed` and `drift_response` events need only one thing: the human renderer shows `payload["change"]` or `payload["decision"]` in the outcome column, so the timeline reads `schedule_changed  response_changed report→remediate  anton` rather than a bare event type.
