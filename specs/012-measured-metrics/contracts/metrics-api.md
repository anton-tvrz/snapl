# Contract: Dashboard Metrics API (additions to v1)

**Feature**: 012-measured-metrics | **Extends**: [010's dashboard API](../../010-dashboard-read-api/contracts/dashboard-api.md)

Every addition below is **compatible** under 010's versioning rules: new routes, a new enum value and new models. 010's common rules hold: GET only, the envelope, failure as a view inside a 200, `422` for bad input, `404` for unknown routes.

## `GET /api/v1/metrics/{view}`

`view` is a `MetricsViewName`. Any other value returns `404`.

| query | default | |
| --- | --- | --- |
| `start` | `end − 1h` | aware datetime |
| `end` | now | aware datetime |
| `step` | `max(15, (end − start) / 240)` seconds | positive integer; raised when needed (FR-013) |
| `use_case` | the server's | `422` if it isn't the server's (010) |

- **Responses:** `422` when `start >= end`, or when `end − start` exceeds 400 days.
- **Response body:** `MetricsResponse`.
- **Unavailable cases:**
  - Prometheus unreachable: `available: false`, `reason` names the address, `remedy: "uv run invoke dev.deps"`.
  - Prometheus answers with an error: `available: false`, with Prometheus's `error` text as the reason.
  - In both cases the last good answer for the same view and range is kept as stale (010 FR-031).
- **Caching:** the server caches each `(view, start, end, step)` for 15 s, after rounding `start` and `end` down to the step. Polling every 5 s therefore costs Prometheus at most one query per view per scrape interval.

## `GET /api/v1/sources` (amended)

The list includes `prometheus`. Its `fetched_at` is the last successful metrics query, and it is `available: false` when the last query failed.

**Amendment to 010's contract:** `SourceName` joins the enums whose unknown values consumers must render as raw strings. The only existing consumer is 010's own page, which already does this.

## `GET /api/v1/overview` (unchanged)

Metrics aren't part of the overview. The page fetches them separately on their own interval of 30 s, so a slow Prometheus can't delay the overview (010 SC-003).

## `GET /metrics`

- **Format:** Prometheus text exposition format 0.0.4 (`text/plain; version=0.0.4`).
- **Not part of the v1 JSON contract:** it isn't in the OpenAPI document. It is GET-only, and HEAD returns `405` like every other route.
- **Content:** the families in data-model.md, "Exported by the dashboard".

## Page

- Under the 010 sections, a **Metrics** section holds one panel per view and a range selector: 1 h, 24 h or 7 d, remembered in `localStorage` with try/catch.
- Each panel is a small inline SVG line chart drawn with DOM APIs (no `innerHTML`, 010 R11), with the latest value as text.
- When the view is unavailable, the panel shows its reason and remedy instead of a chart.
- `worker-up` and `source-health` render as a strip of up/down intervals rather than a line.
- The page still loads nothing from another origin.
