# Tasks: Overnight Fixes

**Input**: [spec.md](spec.md), [plan.md](plan.md). **Umbrella**: #366.

**PR slicing** (each slice one PR, in this order; each is independent of the others):
- **PR A**: Phase 1 (#308, counting views)
- **PR B**: Phase 2 (#306, an export keeps its newest step)
- **PR C**: Phase 3 (#62, the seed)
- **PR D**: Phase 4 (#333, the flaky cancellation test)

**Issues**: each slice's issue already exists. Don't create a new one. Its PR says `Closes #308`, `Closes #306` or `Closes #62`, and `Refs` the umbrella. PR D says `Closes #333` only if T019's "done when" holds; otherwise `Refs #333`, and the report says what is still open. Each PR carries `changelog/<issue>.fixed.md`.

**Lab rule for unattended runs**: a task marked **Needs the lab** uses the Compose stack, the real Prometheus, the maintainer's worker, the live dashboard or a device. Leave it unticked and defer it (`dev/guides/overnight-runs.md`). Tests may start their own `WorkflowEnvironment`, stub HTTP servers, a headless browser and the fixture server. That is not the lab.

---

## Phase 1: Counting Views (US1, #308) — PR A

- [x] T001 [US1] Tests first in `tests/unit/test_presentation/test_dashboard_prometheus.py`:
  - each of the four counting views renders `_step_increase` with `$step` replaced by the plan's step, at 15 s, 60 s and 2520 s;
  - its query has no `$w`, `rate(` or `increase(`;
  - `window_note` is `None` for the four, and unchanged for every `rate()` view.
- [x] T002 [US1] Implement `_step_increase` and `$step` in `prometheus.py` as plan.md A describes. Use it for `WORKFLOWS_COMPLETED`, `SCAN_RESULTS`, `DRIFT_PATHS` and `DEPLOY_OUTCOMES`. Keep `_increase` only if another view still uses it. Keep `counts_events`.
- [x] T003 [US1] Amend `specs/015-more-metrics/contracts/metrics-views.md` ("The window note" and the counting views' query lines) and the "Look"/metrics notes in `dev/skills/dashboard/` if they mention the 60 s window. List each change under "Spec amendments" in the PR.
- [x] T004 [US1] Regenerate the UI fixtures (`SNAPL_WRITE_UI_FIXTURES=1 uv run pytest tests/unit/test_presentation/test_ui_fixtures.py`) and the Prometheus fixtures' README if it names the window. Update the UI tests that read "events in the last 60 s"; the page itself needs no change unless a test shows it does. `cd packages/presentation/ui && npx vitest run && npm run lint`.
- [x] T005 [US1] `changelog/308.fixed.md`.
- [ ] T006 [US1] **Needs the lab**: SC-001. Over the last hour, compare `scan-results` (`/api/v1/metrics/scan-results?start=…`) with the ScanDrift runs in the audit trail. Every point must be whole and the sum equal.

## Phase 2: An Export Keeps Its Newest Step (US2, #306) — PR B

- [x] T007 [US2] Tests first in `test_dashboard_prometheus.py` (or wherever `plan_range` is tested):
  - with `align_end=True`, the end is kept and the start is a whole number of steps before it, at 15 s, 30 s, 2520 s and 10800 s;
  - without it, both ends are floored as today.
- [x] T008 [US2] Tests first in `test_dashboard_app.py`: `/metrics/{view}` with an explicit `end` answers a range ending at it; without `end` it is floored.
- [x] T009 [US2] Tests first in `tests/unit/test_tasks/test_history.py`: every ranged export carries `end` equal to the archive's time. Tests first in `test_dashboard_archive.py`:
  - an archive whose exported ranges end at its time opens and answers its default view;
  - one made before this spec (floored ranges, the existing fixtures) still does;
  - #351's detail windows still serve a sub-range.
- [x] T010 [US2] Implement in `prometheus.py`, `app.py` and `tasks/history.py`. Check `snapshot.py` passes `end` for every ranged view, and add a test if one doesn't.
- [x] T011 [US2] `changelog/306.fixed.md`.
- [ ] T012 [US2] **Needs the lab**: SC-002. Make an archive and a snapshot, and check that every metrics view's exported `end` equals the archive's or snapshot's time.

## Phase 3: The Seed (US3, #62) — PR C

- [x] T013 [US3] Tests first in `tests/unit/test_intent/` (the seed's tests and fake client):
  - with `branch="b"`, every lookup is made on `b`, including the default namespace and VRF, relationship peers and both BGP methods;
  - an `OSError`, `TimeoutError` or Infrahub SDK error from any lookup, including `_get_default_namespace_id`, surfaces as the store's translated exception;
  - a session whose `local_ip` or `remote_ip` lookup misses raises `IntentValidationError` naming the session and the address;
  - an unreferenced peer group logs a warning naming it;
  - the dcfabric seed gives each leaf its sessions (#62's related gap, closed by #316 and #318).
- [x] T014 [US3] Implement in `packages/intent/snapl_intent/infrahub/seed.py` as plan.md C describes. Reuse the store's SDK-error translation; don't copy it.
- [x] T015 [US3] `changelog/62.fixed.md`.

## Phase 4: The Flaky Cancellation Test (US4, #333) — PR D

- [ ] T016 [US4] Reproduce:
  - run `test_a_cancelled_scan_keeps_the_reports_that_completed` 50 times under CPU contention (a few `python -c "while True: pass"` beside it, stopped afterwards) and record the failures and durations in the report;
  - read the test's CI durations from recent PR Validation logs (`gh run view --log`).
- [ ] T017 [US4] Find the cause from T016 and the SDK's handling of a cancel while an activity is held (the issue's leads). Write it in the PR body with the evidence.
- [ ] T018 [US4] Fix what the evidence supports, tests first where code changes. A deadline may be raised only to a measured CI time with margin, said in the PR.
- [ ] T019 [US4] Done when the test passes 50 runs in a row under the same contention and the cause is written down. If T017 found no cause, ship only what is supported, keep #333 open (`Refs`), and write what was ruled out in the report and on #333.
- [ ] T020 [US4] `changelog/333.fixed.md` only if code or the test changed.

---

## Dependencies

None between slices. Run them in order A to D. Each starts from a fresh `origin/main`.

## Deferred to the maintainer

T006 and T012 (the lab). #305, #235 and #136's ideas are out of scope (spec.md Input).
