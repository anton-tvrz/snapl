# Contract: `snapl dashboard` and the recorded test tasks

**Feature**: 010-dashboard-read-api

## `snapl dashboard`

```text
snapl dashboard [--use-case TEXT] [--port INT] [--host TEXT]
```

| option | default | env |
| --- | --- | --- |
| `--use-case` | `dcfabric` | |
| `--port` | `18030` | `DASHBOARD_PORT` |
| `--host` | `127.0.0.1` | `DASHBOARD_HOST` |

Behaviour:
- **On start**: prints one line, `Dashboard for dcfabric at http://127.0.0.1:18030 (read-only, Ctrl-C to stop)`, then serves until interrupted. Exits `0` on Ctrl-C.
- **Port in use**: refuses before serving: `error: port 18030 is in use. Set DASHBOARD_PORT or pass --port.` Exits `ExitCode.ERROR`. It never tries another port (#107).
- **Non-loopback host**: prints `warning: the dashboard has no authentication; anyone who can reach <host>:<port> can read it.` before the start line.
- **Source availability**: does **not** require any source at start-up. Temporal, the SoT and the audit trail may all be down (FR-007). A missing audit file is reported in the view, and never created.
- **Settings**: uses `CliSettings.from_env()` (spec 006 FR-009), extended with `dashboard_port`, `dashboard_host`, `temporal_ui_address`, `github_repo` and `github_token`. The token is never printed, logged or returned by any route.
- **`--json`**: not offered. The API is the machine interface.

## `invoke test-e2e` (changed) and `invoke test-integration` (new)

Both record a lab run (FR-026). Each prints the record's folder as its last line:

```text
Lab run recorded: .snapl/test-runs/20261107T101500Z-e2e (passed: 14, failed: 0, skipped: 1)
```

- `test-e2e` keeps its current command (`SNAPL_E2E=1 pytest tests/e2e -m e2e -v`) and adds `--junitxml`.
- `test-integration` runs `pytest tests/integration -m integration -v --junitxml=…`. It assumes the live stack is up, like `test-e2e`, and says so in its docstring.
- Each exits with pytest's exit code. Recording failures, such as an unwritable folder, print a warning and never change the exit code.

## Lab run record format (`format: 1`)

See [lab-runs.md](lab-runs.md).
