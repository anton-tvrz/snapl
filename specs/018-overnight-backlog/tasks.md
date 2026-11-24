# Tasks: Overnight Backlog

**Input**: [spec.md](spec.md), [plan.md](plan.md). **Umbrella**: #374.

**PR slicing** (each slice is one PR, in this order; the slices are independent):
- **PR A**: Phase 1 (#371, the flaky App.test)
- **PR B**: Phase 2 (#375, cancel only when quiet)
- **PR C**: Phase 3 (#136, the blast-radius guard)
- **PR D**: Phase 4 (#305, the page's height)
- **PR E**: Phase 5 (#235, a gzipped snapshot)
- **PR F**: Phase 6 (#376, task-box housekeeping)

**Issues**: each slice's issue already exists, so don't create a new one. Every PR `Refs #374`. What each PR closes or refs, and its changelog:

| PR | Issue line | Changelog |
| --- | --- | --- |
| A | `Closes #371` | `skip-changelog` label |
| B | `Closes #375` | `skip-changelog` label |
| C | `Refs #136` (the identity check stays open) | `changelog/136.added.md` |
| D | `Refs #305` (SC-001 is a lab check) | `changelog/305.changed.md` |
| E | `Closes #235` if T026 holds, otherwise `Refs #235` | `changelog/235.changed.md` |
| F | `Closes #376` | `skip-changelog` label |

**Lab rule for unattended runs**: a task marked **Needs the lab** uses the Compose stack, the real Prometheus, the maintainer's worker or schedules, the live dashboard or a device. Leave it unticked and defer it (`dev/guides/overnight-runs.md`). Tests may start their own `WorkflowEnvironment`, stub HTTP servers, a headless browser and the fixture server; that is not the lab. Reading the archive file named in plan.md E and making snapshots from it offline is not the lab either.

---

## Phase 1: A UI Test That Waits (US1, #371) — PR A

- [x] T001 [US1] Make the failure show: delay the `runs` answer in `App.test.tsx > shows the loop for the latest run` in a throwaway edit, and see it fail with CI's message ("Number of calls: 0"). Record this in the PR, and don't commit the delay.
- [x] T002 [US1] Wait for the steps call (`await waitFor(...)`, after the "The loop, block by block" list). The test passes with the delay and without it. `cd packages/presentation/ui && npx vitest run && npm run lint`.

## Phase 2: Cancel Only When Quiet (US2, #375) — PR B

- [x] T003 [US2] List every `handle.cancel()` in `tests/unit/test_orchestrator/`, with what each waits for before cancelling. Put the list in the PR.
- [x] T004 [US2] Move #373's quiet wait into one shared test helper. `test_workflow_cancellation.py` uses it, and its race-widened variant stays green.
- [x] T005 [US2] For each test from T003 that can race:
  - show it red under `_SlowAfterCancel`, or under a late-returning fake, with its old wait;
  - switch it to the helper;
  - show it green.
  - If one can't race, say why in the PR.
- [x] T006 [US2] Run each changed file 20 times under coverage and 16 busy processes, with `--cov-fail-under=0`, and record the passes and the slowest run.

## Phase 3: The Blast-Radius Guard (US3, #136) — PR C

- [x] T007 [US3] Find which count is forward drift only (`DriftScanResult.drifted` or the reports). Write the answer in the PR.
- [x] T008 [US3] Tests first in `tests/unit/test_orchestrator/test_workflow_drift_response.py`:
  - remediate with 3 of 4 drifted: no reconcile child, stood down, and the note names the run, "3 of 4" and "more than half";
  - remediate with 2 of 4: repairs;
  - remediate with 1 of 1: repairs;
  - report with 4 of 4: no guard and no stand-down;
  - an undesired-only device doesn't count;
  - the result and the audit payload say the guard held.
- [x] T009 [US3] A replay test: a history from before the guard replays unchanged (patch `drift-response-blast-radius-guard`).
- [x] T010 [US3] Implement in `workflows/drift_response.py` and `models.py`, and in the dashboard's run reason if it lists reasons.
- [x] T011 [US3] Amend `specs/009-scheduled-drift-response/contracts/orchestrator-schedules.md` and research R8. List them under "Spec amendments".
- [x] T012 [US3] `changelog/136.added.md`.
- [x] T013 [US3] **Needs the lab**: SC-003. Give a remediate schedule an intent edit that drifts the whole fabric, on a branch or with a revert the maintainer chooses. It repairs nothing, and `snapl schedule show` reads `report` with the guard's note. *Run on 2026-11-24 with the maintainer's go-ahead. The drift was made on the devices, not by an intent edit, since the guard counts forward drift whatever its source: `ethernet-1/1` disabled on both spines and `ethernet-1/49` on leaf-01 and leaf-02, 4 of 6 devices. The triggered remediate run (12:36:53Z) wrote nothing, excluded the four as `blast_radius` in the audit trail and stood the schedule down to `report`; `snapl reconcile --drifted` then repaired them. Its note was on the schedule but `snapl schedule show` printed a note only when paused; #389 shows it whenever it is set.*

## Phase 4: The Page's Height (US4, #305) — PR D

- [x] T014 [US4] Measure the fixture page with `e2e/look.mjs` before any change: its height and the Metrics section's top.
- [x] T015 [US4] Tests first in `components/panels/Runs.test.tsx`:
  - 25 runs show 10 and "Show all 25 runs";
  - pressing it shows 25;
  - with 10 or fewer, there's no button;
  - a running run is never hidden;
  - the cap applies after the device filter.
- [x] T016 [US4] Tests first in `App.test.tsx`: with no `?open=`, every metrics group is folded. `?open=metrics-timing` and the number row's links still open groups. Update the tests that assumed `metrics-timing` was open by default, including `e2e/smoke.spec.ts`.
- [x] T017 [US4] Implement in `Runs.tsx` and `App.tsx` (`DEFAULT_OPEN = []`).
- [x] T018 [US4] Amend spec 014 SC-003 to measure the Metrics section's first group heading, with `e2e/layout.spec.ts` and `e2e/look.mjs` to match, and list it under "Spec amendments". Run `npx vitest run`, `npm run lint` and the e2e tests (`npx playwright test`).
- [x] T019 [US4] Measure again as in T014, and put both measures in the PR.
- [x] T020 [US4] `changelog/305.changed.md`.
- [x] T021 [US4] **Needs the lab**: SC-001. Measure the live page at 1280 px with `e2e/look.mjs`. If it is over 3062 px, the maintainer decides whether to amend spec 014 SC-002. *Measured on 2026-11-24: 3661 px (7086 before), `firstMetricsGroupTop` 1906 px. The maintainer amended spec 014 SC-002 to 4000 px.*

## Phase 5: A Gzipped Snapshot (US5, #235) — PR E

- [x] T022 [US5] Tests first in `tests/unit/test_presentation/test_dashboard_snapshot.py`:
  - a written snapshot's element carries `data-encoding="gzip+base64"` and unpacks to the bundle;
  - `--bundle-json` writes equal JSON;
  - the summary prints the file's size and the unpacked size, and names `--bundle-json`.
  - Give the existing tests one unpacking helper.
- [x] T023 [US5] Tests first in `packages/presentation/ui/src/data/inline.test.ts`:
  - a packed bundle reads;
  - a plain one still reads;
  - a corrupt one is refused with a message;
  - with no `DecompressionStream`, the page says it needs a newer browser.
  - No new dependency.
- [x] T024 [US5] Implement in `snapshot.py`, the CLI's `--bundle-json`, `inline.ts` and `main.tsx`. Keep `e2e/snapshot.spec.ts` green with its 3 MB budget.
- [x] T025 [US5] Amend `specs/011-dashboard-ui/contracts/cli-and-build.md` (the element and the flag), and list it under "Spec amendments".
- [x] T026 [US5] SC-002, offline:
  - build the UI;
  - make 7-day and 30-day snapshots from the 2026-11-20 archive into a git-ignored path;
  - both must be under 3 MB;
  - record the sizes in the PR.
- [x] T027 [US5] `changelog/235.changed.md`.

## Phase 6: Task-Box Housekeeping (US6, #376) — PR F

- [x] T028 [US6] Specs 001, 003 and 005: tick each open task whose file, test or behaviour exists on `origin/main`. Add "*Shipped as …*" where its shape changed. List the rest in the PR.
- [x] T029 [US6] In the run's final report, propose which umbrella issues could close (for example #146, #101, #246), with one line of evidence each. Close nothing.

---

## Dependencies

There are none between slices. Run them in order, A to F, each from a fresh `origin/main`.

## Deferred to the maintainer

- None from the lab: T013, T021 and spec 017's T006 and T012 were run on 2026-11-24.
- #136's Temporal identity check.
- #65, which waits for spec 013's captures.
