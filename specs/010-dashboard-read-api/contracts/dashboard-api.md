# Contract: Dashboard HTTP API, v1

**Feature**: 010-dashboard-read-api | **Consumers**: the MVP page (010), the designed UI and snapshot export (011), metric proxying (012)

The server listens on `127.0.0.1:${DASHBOARD_PORT:-18030}`. Every route is `GET` only. Any other method on a known route, `HEAD` included, returns `405` with no side effect, and an unknown route returns `404`. The one exception is the framework's own OpenAPI route, which also answers `HEAD` (research R1). The Swagger and ReDoc pages are turned off, because they load scripts from a CDN. Models are in [data-model.md](../data-model.md). The machine-readable form is the generated OpenAPI document at `/api/v1/openapi.json`, which a unit test snapshots.

## Common rules

- **Envelope**: every JSON response carries `contract_version: "1"`, `use_case` and `generated_at`.
- **Status codes**: a source failure is never an HTTP error. It is a view with `available: false`, `reason` and `remedy`, inside a `200`. HTTP errors are only for bad requests (`422`) and unknown routes (`404`).
- **`use_case`**: query parameter, default the server's `--use-case`. A use case the server was not started for returns `422`. One server serves one use case in 010.
- **Times**: query times are ISO 8601 and must carry an offset. A naive time returns `422`.
- **Caching**: each source has its own refresh time, so requests never wait on a slow source longer than its timeout (`SNAPL_CONNECT_TIMEOUT`, default 10 s):
  - *Amended in PR F*: a request waits at most 1.5 s for any one source, or `SNAPL_CONNECT_TIMEOUT` if that is shorter. A source still fetching after that is shown as unavailable ("still waiting"), or with its last answer marked stale. Its fetch keeps running, shared by every request, and the next request picks up the answer. Restarting it on every poll would never let a slow source finish, and would spend GitHub's rate limit. Each fetch is bounded by `SNAPL_CONNECT_TIMEOUT`, except CI's, which is bounded at 60 s: a cold CI refresh is several GitHub calls in a row, each with its own 10 s timeout.

  | source | refresh | notes |
  | --- | --- | --- |
  | audit | every request | re-reads a trailing 5-minute window, and rebuilds from a full read every 15 minutes, research R3 |
  | temporal | 5 s | |
  | sot | 30 s | |
  | ci | 120 s with token, 600 s without | ETag, rate-limit backoff, research R9 |
  | lab_runs | every request | directory scan |

- **Security**: no request makes the server write anywhere (FR-003). Audit payload strings are returned as data and never interpreted.
- **Known limitation, late audit rows**: an audit row held back for more than 5 minutes after its timestamp, for example while the worker was down mid-write, shows up at the next 15-minute rebuild, not on the next refresh. It is never dropped (research R3).

## Routes

### `GET /`
The MVP page (`text/html`). No external resources.

### `GET /api/v1/overview`
Everything the page needs in one request. Returns `Overview`.

| query | default | |
| --- | --- | --- |
| `runs_limit` | 50 | 1–500 |

### `GET /api/v1/devices`
`Envelope + { devices: View[list[DeviceView]] }`.

Source: `sot` for the device list and roles (identity-checked, FR-015), plus `audit` for state. When the SoT is unavailable or foreign, `devices.available` is false and `devices.data` holds the degraded, audit-only list (research R13). When the audit trail is unavailable, `devices.data` lists every device as `never_scanned`, and `reason` says the audit trail could not be read. The two failures are reported independently through `/api/v1/sources`.

### `GET /api/v1/runs`
`Envelope + { runs: View[list[RunView]], running: View[list[RunView]] }`.

| query | default | |
| --- | --- | --- |
| `limit` | 50 | 1–500 |
| `workflow_type` | all | repeatable |
| `device` | all | a device name |

Known limitation: `web_ui_url` opens the workflow id's page, not one run's. The audit trail holds no Temporal run id (research R12).

### `GET /api/v1/schedule`
`Envelope + { schedule: View[ScheduleStatus], worker: View[WorkerStatus] }`.

Source: `temporal` for current state and the worker, plus `audit` for the pause actor and repeated failures. If Temporal is down, `schedule.available` is false, and `remedy` is `uv run invoke dev.deps`. `repeated_failures` is still computed from the audit trail and returned in `data`, marked `stale: true`.

*Amended in PR F*: if the audit trail is down instead, `schedule.available` is also false, with the audit trail's reason and remedy. `data` still holds Temporal's schedule, but its `repeated_failures` and `paused_by` could not be read, and an empty list must not read as "repairs are fine". `worker` is unaffected.

### `GET /api/v1/history`
`Envelope + { series: list[Series] }`.

| query | default | |
| --- | --- | --- |
| `series` | all four | repeatable `SeriesName` |
| `start` | `end − 24h` | |
| `end` | now | |
| `bucket` | `1h` | `Bucket`. Ignored for per-scan series |

Returns `422` when `start >= end` or the range exceeds 2,000 buckets. The range is never clamped to the data. Points before `first_event_at` are simply absent.

### `GET /api/v1/ci`
`Envelope + { ci: View[CiStatus] }`.

Without network access, or when rate-limited, `ci` is the last answer with `stale: true`, or `available: false` if there has never been one. The remedy for a spent rate limit is `export GITHUB_TOKEN=…`.

Without a token, check runs are fetched for at most 5 open PRs, the most recently updated first. `omitted_prs` counts the rest, and the view carries the same remedy (research R9).

### `GET /api/v1/lab-runs`
`Envelope + { lab: View[LabEvidence] }`.

When `.snapl/test-runs/` does not exist, the view is available with no runs, and `remedy` is `uv run invoke test-e2e`.

### `GET /api/v1/sources`
`Envelope + { sources: list[View[None]] }`. One entry per `SourceName`, giving availability, reason, remedy and `fetched_at` without the data. The page uses it for its status strip, and SC-003 tests use it.

### `GET /api/v1/links`
`Envelope + { links: list[Link], reason: str | null, remedy: str | null }`, where `Link` is `{ group: "stack" | "github" | "api" | "docs", label, url }`. Added after 010's PRs at the maintainer's request (#173). It is a compatible change under the versioning rules below.

- **Settings:** every URL is built from the settings the server already reads, so the links point at the stack and repo the views describe:
  - Infrahub: `INFRAHUB_ADDRESS`.
  - The Temporal Web UI's workflows page and the use case's drift schedule: `TEMPORAL_UI_ADDRESS` and the namespace.
  - The repository, its pull requests and Actions: the resolved GitHub repo.
  - The project board: `SNAPL_PROJECT_BOARD_URL`.
  - The quickstart and the demo scenarios, on `main`.
- **API links:** these are paths on this dashboard.
- **No repo:** when no GitHub repo resolves, the repository and docs links are left out, and `reason` and `remedy` say so.
- **Page:** fetches this route once at load. The settings don't change while the server runs.

## Versioning

A change is **compatible** when it only adds an optional field, a route, a query parameter, or an enum value that consumers are documented to tolerate. `DriftState`, `RunState`, `Trigger`, `JobResult` and `SourceName` consumers must render an unknown value as its raw string.

**Amended 2026-11-08 (spec 012):** `SourceName` joined the list above, and gained `prometheus`. The routes `GET /api/v1/metrics/{view}` and `GET /metrics` were added. See [012's contracts/metrics-api.md](../../012-measured-metrics/contracts/metrics-api.md). This page already rendered the source name as a raw string, so no consumer broke.

Any other change is **breaking**. It moves to `/api/v2/`, keeps `/api/v1/` until spec 011's UI has moved, and bumps `contract_version`.
