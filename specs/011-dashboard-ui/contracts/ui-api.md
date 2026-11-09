# Contract: Dashboard API additions for the UI (additions to v1)

**Feature**: 011-dashboard-ui | **Extends**: [010's dashboard API](../../010-dashboard-read-api/contracts/dashboard-api.md) and [012's metrics API](../../012-measured-metrics/contracts/metrics-api.md)

Every addition below is **compatible** under 010's versioning rules: new routes, new optional query parameters and new models. 010's common rules hold: GET only, the envelope, failure as a view inside a `200`, `422` for bad input, `404` for unknown routes. Models are in [data-model.md](../data-model.md).

## Pages and files

| route | answer |
| --- | --- |
| `GET /` | The designed UI's `index.html` when it is built. Otherwise the plain page, with a notice naming `uv run invoke dashboard.build`. |
| `GET /plain` | 010's plain page, always. |
| `GET /assets/{path}` | A built file from the UI's `assets/` directory. `404` for anything else, including any path that resolves outside that directory. |

- `index.html` and the plain page are served `Cache-Control: no-cache`. Assets carry a content hash in their name and are served `Cache-Control: public, max-age=31536000, immutable`.
- These three are not in the OpenAPI document, like 010's `/`.
- They answer `GET` only. `HEAD` returns `405`, as on every other route.

## Response header on every route

`Content-Security-Policy`, as in research R4:

- **UI, assets and API**: `default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`
- **The plain page** (at `/plain`, and at `/` when it is the fallback): the same with `script-src 'unsafe-inline'` and `style-src 'unsafe-inline'`.

## `GET /api/v1/topology`

`TopologyResponse`. Source: `sot`, with the device view's cache (30 s) and identity check (010 FR-015).

| query | default | |
| --- | --- | --- |
| `use_case` | the server's | `422` if it isn't the server's |

- Source of Truth unavailable or foreign: `topology.available` is false with the reason and remedy, and `data` is null. There is no degraded topology. The device view still has its degraded list.
- In archive mode: from the archive's `export/topology.json`, or unavailable with the reason "this archive predates the topology export".

## `GET /api/v1/drift-timeline`

`DriftTimelineResponse`. Source: `audit`.

| query | default | |
| --- | --- | --- |
| `start` | `end − 24h` | aware datetime |
| `end` | the server's now | aware datetime |
| `use_case` | the server's | |

- `422` when `start >= end`, or when the range holds more than 20,000 scan points. The message says how many it holds and asks for a shorter range.
- Device names come from the Source of Truth's last good answer. A device no longer in intent keeps its lane, with `in_intent: false` and its id as its name when no name is known.

## `GET /api/v1/runs/steps`

`RunStepsResponse`. Source: `audit`.

| query | | |
| --- | --- | --- |
| `workflow_id` | required | |
| `started_at` | required | aware datetime; with `workflow_id` it identifies one run (010 `RunView`) |

- No such run: `200` with `steps.data: null` and `steps.available: true`. It is not a `404`, because the route exists and the audit trail answered.
- The step-to-block table lives on the server only (FR-026).

## `GET /api/v1/mode`

`ModeResponse`. No source: it describes the server.

## `GET /api/v1/devices` and `GET /api/v1/runs` (amended)

Both gain `at`:

| query | default | |
| --- | --- | --- |
| `at` | none (now) | aware datetime. `422` if naive, or later than the server's now |

- With `at`, the answer is projected from audit events with `timestamp <= at`.
- `/runs?at=`: a run open at `at` is `running` when the trail holds its end after `at`, and `unfinished` otherwise. The `running` view (Temporal's) is returned unavailable with the reason "the workflow engine has no history" and the remedy to drop `at`.
- Names and roles are today's, in both.
- `/overview` takes no `at`.

## `GET /api/v1/sources` (unchanged shape)

In archive mode, each entry reports the archive's part for that source: available with the export's `fetched_at`, or `available: false` with "not in this archive".

## Archive mode: how views answer

| view | in archive mode |
| --- | --- |
| devices, runs, history, drift-timeline, run steps | computed from the archived audit trail, as live |
| topology | `export/topology.json` |
| schedule, worker, CI | the views inside `export/overview.json`, as exported, with their original `fetched_at` |
| running | unavailable: "not in this archive" |
| lab runs | read from the archived `test-runs/`; `commits_on_main_since` is null with the reason "an archive has no git history" |
| links | `export/links.json` |
| metrics | `export/metrics/<view>.json` when the requested range is the exported one; otherwise unavailable: "an archive holds only the exported range <start> to <end>" |

Every unavailable view above carries the remedy `uv run snapl dashboard`.

## OpenAPI

The new routes and models are in `/api/v1/openapi.json`, and the snapshot test's fixture is updated in the PR that adds each. `contract_version` stays `"1"`.
