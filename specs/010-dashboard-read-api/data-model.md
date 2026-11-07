# Data Model: Dashboard Read API and MVP Page

**Feature**: 010-dashboard-read-api | **Date**: 2026-11-07

All models are Pydantic v2, `frozen=True` and `extra="forbid"`, in `snapl_presentation.dashboard.models` unless noted. Times are timezone-aware UTC. On the wire they are ISO 8601 strings, and durations are integer seconds.

## Envelope and source status

### `SourceName` (StrEnum)
`audit`, `temporal`, `sot`, `ci`, `lab_runs`

### `View[T]` (generic)

| field | type | rule |
| --- | --- | --- |
| `source` | `SourceName` | |
| `available` | `bool` | false when the last attempt to read the source failed |
| `fetched_at` | `datetime \| None` | when `data` was read. `None` only when the source never answered |
| `stale` | `bool` | true when `data` comes from an earlier successful read than the last attempt (FR-031) |
| `reason` | `str \| None` | required when `available` is false |
| `remedy` | `str \| None` | a command, e.g. `uv run invoke dev.deps`. Required when `available` is false |
| `data` | `T \| None` | |

Invariants:
- `available=False` requires both `reason` and `remedy`.
- `stale=True` requires `data` and `fetched_at`.

### `Envelope`

| field | type |
| --- | --- |
| `contract_version` | `Literal["1"]` |
| `use_case` | `str` |
| `generated_at` | `datetime` |

Each endpoint's response extends `Envelope` with its views (see `contracts/dashboard-api.md`).

## Devices (US1)

### `DriftState` (StrEnum)
`in_sync`, `missing`, `undesired`, `missing_and_undesired`, `drifted` (a pre-FR-012a scan, direction unknown), `error`, `never_scanned`

### `DeployOutcome`

| field | type |
| --- | --- |
| `workflow_id` | `str` |
| `success` | `bool` |
| `reason` | `WorkflowReason \| None` (from `snapl_orchestrator.models`) |
| `ended_at` | `datetime` |

### `DeviceView`

| field | type | rule |
| --- | --- | --- |
| `device_id` | `UUID` | |
| `name` | `str` | |
| `role` | `str \| None` | `None` only in the degraded view (research R13) |
| `in_intent` | `bool` | false for a device seen in the audit trail but no longer in the SoT |
| `drift_state` | `DriftState` | |
| `missing_paths` | `list[str]` | empty unless the state is `missing` or `missing_and_undesired` |
| `undesired_paths` | `list[str]` | empty unless the state is `undesired` or `missing_and_undesired` |
| `unclassified_paths` | `list[str]` | used only when the state is `drifted` |
| `error` | `str \| None` | required when the state is `error` |
| `last_scanned_at` | `datetime \| None` | `None` iff the state is `never_scanned` |
| `last_scan_workflow_id` | `str \| None` | |
| `last_deploy` | `DeployOutcome \| None` | |
| `suggested_command` | `str \| None` | `snapl reconcile <name>` for a forward drift. For undesired-only drift it is `None`, with `note` saying removal is not automatic (#65) |
| `note` | `str \| None` | |

The device view's `data` is `list[DeviceView]`, sorted by role (spines first) and then by name.

## Runs and schedule (US2)

### `Trigger` (StrEnum)
`schedule`, `operator`

### `RunState` (StrEnum)
`running`, `succeeded`, `failed`, `cancelled`, `unfinished` (started, no terminal event, and not running in Temporal)

### `RunView`

| field | type |
| --- | --- |
| `workflow_id` | `str` |
| `workflow_type` | `str` (`DeployIntent`, `ScanDrift`, `ReconcileDevices`, `DriftResponse`) |
| `target` | `str \| None` (a device name if resolvable, else the use case or the raw target id) |
| `started_at` | `datetime` |
| `ended_at` | `datetime \| None` |
| `duration_s` | `int \| None` (for a running run: seconds so far) |
| `state` | `RunState` |
| `reason` | `str \| None` |
| `trigger` | `Trigger` |
| `actor` | `str \| None` (only when recorded) |
| `web_ui_url` | `str` |

A run's identity is `(workflow_id, started_at)`, because `deploy-intent-{device}` is reused across runs (research R5). The runs view's `data` is `list[RunView]`, newest first, bounded by `limit` (default 50, at most 500).

### `RepeatedRepairFailure`

| field | type |
| --- | --- |
| `device_id` | `UUID` |
| `name` | `str` |
| `consecutive_failures` | `int` (≥ 3) |
| `last_failed_at` | `datetime` |
| `suggested_command` | `str` (`snapl schedule mode report --use-case <uc>`) |

### `ScheduleStatus`

| field | type | rule |
| --- | --- | --- |
| `exists` | `bool` | |
| `schedule` | `DriftScheduleView \| None` | reused from `snapl_orchestrator.models`. Required iff `exists` |
| `paused_by` | `str \| None` | the actor of the latest `SCHEDULE_CHANGED` with change `paused` |
| `paused_at` | `datetime \| None` | |
| `repeated_failures` | `list[RepeatedRepairFailure]` | |
| `suggested_command` | `str \| None` | `snapl schedule create --use-case <uc>` when `exists` is false |

### `WorkerStatus`

| field | type |
| --- | --- |
| `pollers` | `int` |
| `task_queue` | `str` |

## History (US3)

### `Bucket` (StrEnum)
`5m`, `1h`, `1d`, `1w`

### `SeriesName` (StrEnum)
`drifted_devices`, `undesired_paths`, `deploys`, `repairs`

### `Point`

| field | type |
| --- | --- |
| `t` | `datetime` (the scan time for per-scan series, the bucket start for counted series) |
| `value` | `int \| None` (for per-scan series) |
| `by_outcome` | `dict[str, int] \| None` (for counted series: `succeeded` and `failed`, each `null` in an empty bucket) |

### `Marker`

| field | type |
| --- | --- |
| `t` | `datetime` |
| `kind` | `Literal["reconcile", "remediation"]` |
| `workflow_id` | `str` |
| `trigger` | `Trigger` |

### `Series`

| field | type |
| --- | --- |
| `name` | `SeriesName` |
| `start` | `datetime` |
| `end` | `datetime` |
| `bucket` | `Bucket \| None` (`None` for per-scan series) |
| `points` | `list[Point]` |
| `markers` | `list[Marker]` |
| `first_event_at` | `datetime \| None` (the oldest audit event, so a UI can say "history starts here") |

Validation:
- `start < end`.
- At most 2,000 buckets per request, otherwise 422.
- Naive datetimes give 422, matching `AuditLog.query_by_time_range`.

## CI (US4)

### `JobResult` (StrEnum)
`success`, `failure`, `cancelled`, `skipped`, `in_progress`, `queued`, `neutral`

### `CiJob`

| field | type |
| --- | --- |
| `name` | `str` |
| `result` | `JobResult` |
| `url` | `str` |

### `CiTarget`

| field | type |
| --- | --- |
| `kind` | `Literal["main", "pull_request"]` |
| `number` | `int \| None` (PR number) |
| `title` | `str \| None` |
| `head_sha` | `str` |
| `gate` | `JobResult` (the result of the quality gate check run) |
| `jobs` | `list[CiJob]` |
| `run_url` | `str \| None` |
| `coverage_pct` | `float \| None` (`main` only, from Codecov) |

### `CiStatus`

| field | type |
| --- | --- |
| `repo` | `str` (`owner/name`) |
| `authenticated` | `bool` |
| `rate_limit_remaining` | `int \| None` |
| `targets` | `list[CiTarget]` (`main` first, then PRs by number) |

## Lab runs (US4)

The file format is in `contracts/lab-runs.md`. The reader's model:

### `LabRunStatus` (StrEnum)
`running`, `passed`, `failed`, `interrupted`, `incomplete` (derived: still `running` on disk, and its pid is gone), `unreadable`

### `LabRun`

| field | type |
| --- | --- |
| `id` | `str` (the folder name) |
| `suite` | `Literal["e2e", "integration"]` |
| `status` | `LabRunStatus` |
| `started_at` | `datetime \| None` |
| `ended_at` | `datetime \| None` |
| `commit` | `str \| None` |
| `branch` | `str \| None` |
| `dirty` | `bool \| None` |
| `device_image` | `str \| None` |
| `passed` / `failed` / `skipped` / `errors` | `int \| None` |
| `problem` | `str \| None` (required when `unreadable`) |

### `LabEvidence`

| field | type |
| --- | --- |
| `latest` | `dict[suite, LabRun]` (the newest readable run per suite) |
| `commits_on_main_since` | `dict[suite, int \| None]` |
| `commits_unknown_reason` | `dict[suite, str]` (only for suites whose count is `None`) |
| `runs` | `list[LabRun]` (newest first, at most 20) |

## Overview (the MVP page's poll)

### `Overview` (extends `Envelope`)

| field | type |
| --- | --- |
| `devices` | `View[list[DeviceView]]` |
| `runs` | `View[list[RunView]]` (default limit 50) |
| `running` | `View[list[RunView]]` (state `running` only, from Temporal) |
| `schedule` | `View[ScheduleStatus]` |
| `worker` | `View[WorkerStatus]` |
| `ci` | `View[CiStatus]` |
| `lab` | `View[LabEvidence]` |

## Change to an existing model

### `ScanDrift` per-device audit payload (FR-012a)

`snapl_orchestrator/workflows/scan_drift.py::_audit_device` gains one key:

| key | type | when |
| --- | --- | --- |
| `undesired_paths` | `list[str]` | always present when `paths` is present. A subset of `paths`, in the same order |

Existing keys (`status`, `drift_items`, `paths`, `error`) are unchanged. No `AuditEvent` field, `AuditEventType` value or schema DDL changes.

### `SqliteAuditLog(read_only: bool = False)`

A new keyword argument. When it is true:
- `initialize()` opens `file:{path}?mode=ro` and skips the DDL and the WAL pragma.
- A missing file raises `AuditLogError`.
- `append()` raises `AuditLogError("audit log opened read-only")`.

The `AuditLog` ABC does not change.
