# Implementation Plan: Overnight Fixes

**Branch**: `017-overnight-fixes` | **Date**: 2026-11-20 | **Spec**: [spec.md](spec.md)

## Summary

There are four independent slices, each one PR that closes its own issue. No new dependencies and no new routes. The only contract change is the counting views' values and note (#308), which the maintainer chose.

## Technical Context

Python 3.12, FastAPI, pydantic 2, Temporal SDK, the Infrahub SDK. The UI is React 19 with Vitest. Prometheus v3.10.0 serves the queries. The worker is scraped every 5 s, everything else every 15 s.

## Design

### A. Counting views (#308): `presentation`

`prometheus.py` builds the four counting views with `_increase(selector)`: `rate()` times the window, with `$w = max(60 s, step)` and #191's first-observation term. Plain `increase()` over a window equal to the step doesn't work. With 5 s scrapes it extrapolates one event to 1.5 and misses an event that falls between two windows' samples.

Instead, a new `_step_increase(selector)` takes the difference between the counter now and one step ago, `$step`:

```
(sel - sel offset $step) >= 0
or (sel unless sel offset $step)        # first observation: counted from 0
or sel                                  # a reset: the count since it
```

Each piece is wrapped in the view's `sum by (...)`. Check the exact form against Prometheus semantics: `offset` reads the latest sample at or before t − step, so consecutive points telescope and add up to the range's increase. The `or sel` term must not double-count a series the first term already has, which `or` guarantees, because it keeps the left side's label sets.

`_render` gains `$step`, filled from the plan's step. `window_note` returns `None` for counting views. `counts_events` stays: the page uses it for the headline rule.

The spec 015 contract's window note paragraph is amended. Regenerate the UI fixtures that carry the note (`SNAPL_WRITE_UI_FIXTURES=1`, see `test_ui_fixtures.py`). Update the UI only where a test reads the note.

### B. Newest step (#306): `presentation`, `tasks`

`plan_range(start, end, step, *, align_end=False)`. When `align_end` is true, the end is kept and the start becomes `end − ceil((end − start) / step) × step`. The `/metrics/{view}` route passes `align_end=end is not None`. `history.archive` passes `end` (its own `now`) for every ranged export; `snapshot.py` already does. The archive reader's exact-range match is unchanged: an aligned plan reproduces the exported range. #351's tolerance (a start up to one step before a window) still holds. Older archives open at their floored ranges as before (FR-006).

### C. Seed (#62): `intent`

`packages/intent/snapl_intent/infrahub/seed.py`:
- Pass `branch` to every `client.filters(...)` and `client.get(...)`.
- Move `_get_default_namespace_id` inside the translated `try`.
- Translate SDK errors as `store._filters` does: reuse its helper, don't copy it.
- Raise `IntentValidationError` on a missed `local_ip` or `remote_ip`.
- Warn on an unreferenced peer group.

Tests use the existing fake client in `tests/unit/test_intent/`.

### D. Flaky test (#333): `orchestrator`, tests only unless the cause is in code

Reproduce first. Run the test 50 times under CPU contention; a few busy processes beside pytest are enough. Read the Temporal SDK's handling of a cancel request while an activity is held. Fix what the evidence shows. If nothing reproduces, raise the deadline only to a measured CI time (CI's job log has the test's duration) and say so. If the evidence is not conclusive, leave #333 open.

## Constitution Check

- TDD: every slice writes its tests first and sees them fail.
- Spec-driven: this spec, plan and tasks exist before any code.
- No new dependencies. No lab access overnight: **Needs the lab** tasks are deferred to the maintainer.
