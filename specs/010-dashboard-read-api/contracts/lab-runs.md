# Contract: Lab run records

**Feature**: 010-dashboard-read-api | **Writer**: `tasks/lab_runs.py` | **Reader**: `snapl_presentation.dashboard.lab_runs`

## Layout

```text
.snapl/                         # git-ignored; survives `invoke demo.reset`
└── test-runs/
    └── <YYYYMMDDTHHMMSSZ>-<suite>/
        ├── run.json
        └── junit.xml           # absent if pytest never started
```

`<suite>` is `e2e` or `integration`. The folder is overridable with `SNAPL_TEST_RUNS_DIR`, mainly for tests.

## `run.json`

Written before pytest starts and rewritten once it ends. Writes are atomic (write to `run.json.tmp`, then `os.replace`).

```json
{
  "format": 1,
  "suite": "e2e",
  "status": "passed",
  "started_at": "2026-11-07T10:15:00Z",
  "ended_at": "2026-11-07T10:21:42Z",
  "exit_code": 0,
  "commit": "<40-hex commit sha>",
  "tree": "<40-hex tree sha>",
  "branch": "main",
  "dirty": false,
  "device_image": "ghcr.io/nokia/srlinux:26.3.1",
  "pid": 41733,
  "counts": {"passed": 14, "failed": 0, "skipped": 1, "errors": 0}
}
```

| field | at start | at end |
| --- | --- | --- |
| `status` | `running` | `passed` (exit 0), `failed` (exit 1), `interrupted` (KeyboardInterrupt or exit 2), or `failed` (any other non-zero) |
| `ended_at`, `exit_code`, `counts` | absent | present. `counts` is absent if `junit.xml` is missing or unparseable |
| `device_image` | from `containerlab/dcfabric.yml` | unchanged |
| `tree` | `git rev-parse HEAD^{tree}`. Lets a run on a PR branch be matched to its squash commit on `main` (research R10) | unchanged |

Reader rules:
- **Incomplete**: `status == "running"` and `pid` is not a live process means `incomplete`.
- **Unreadable**: unparseable JSON, or `format` other than `1`, means `unreadable` with a `problem`. Other runs are unaffected.
- **Unknown fields**: ignored, so later formats can add fields.
