# Implementation Plan: Overnight Backlog

**Branch**: `018-overnight-backlog` | **Date**: 2026-11-21 | **Spec**: [spec.md](spec.md)

## Summary

There are six independent slices, each one PR. There are no new dependencies and no new API routes. There are two deliberate contract changes, both chosen by the maintainer:
- a schedule in `remediate` can stand itself down before repairing (#136);
- a snapshot file's bundle element is gzipped (#235).

## Technical Context

- **Python side**: Python 3.12, FastAPI, pydantic 2, and Temporal SDK 1.25.0, with time-skipping test servers.
- **UI side**: React 19 with Vitest, and Playwright for the e2e tests and `e2e/look.mjs`.
- **Tools**: the fixture server is `dev/scripts/serve_dashboard_fixtures.py`. The real archive for SC-002 is `.snapl/archives/snapl-history-20261120T202656Z.tar.gz` in the main checkout. Reading it is offline: no stack, no lab.

## Design

### A. #371: `presentation` UI, test only

In `App.test.tsx`, "shows the loop for the latest run" wraps `expect(steps).toHaveBeenCalledWith(...)` in `await waitFor(...)`. Do this after awaiting the "The loop, block by block" list, which needs the steps. To see it red first, delay the provider's `runs` answer in a throwaway run of the test. Record that in the PR; don't commit the delay. The label is `skip-changelog`.

### B. #375: `orchestrator` tests only

List every `handle.cancel()` under `tests/unit/test_orchestrator/`. Today these are `test_workflow_cancellation.py` and four tests in `test_workflow_drift_response.py`:
- `a_cancelled_run_records_cancellation`
- `a_scan_cancelled_on_its_own_fails_the_run`
- `an_operator_cancel_during_the_repair_does_not_stand_down`
- `a_run_timeout_mid_repair_lets_the_repair_finish_and_record`

For each, write down what it waits for before it cancels. If that wait is a side effect that an activity makes before it completes, it can race.

Move #373's quiet wait (`spine_01_checked`'s second half: pending activities, then the last history event) into a shared helper in `tests/unit/test_orchestrator/`, and use it in every racing test. Prove each changed test against the race with #373's `_SlowAfterCancel` executor: red with the old wait, green with the new one. If a test can't be made to race, say why in the PR and leave it. The label is `skip-changelog`.

### C. #136, the blast-radius guard: `orchestrator`

In `workflows/drift_response.py`, after the scan and before the reconcile child, in `REMEDIATE` only, behind a new patch id `drift-response-blast-radius-guard`:

```python
if scan.drifted > 1 and scan.drifted * 2 > scan.total and workflow.patched(_GUARD_PATCH):
    stood = await _stand_down(request, f"{wf_id}: {scan.drifted} of {scan.total} devices drifted, more than half; repaired nothing")
    # record the outcome as the report path does, with remediation "guarded"
```

- **Which count**: use whichever count is forward drift only. Check `DriftScanResult.drifted` against the observer: undesired-only devices must not count (spec edge case). If `drifted` includes them, count the reports with forward drift.
- **What it records**: the run's result and audit payload record that the guard held. Use a new `remediation` value or reason, and add it to the models and the dashboard's run reason if the dashboard lists reasons. The result's `stood_down` is set.
- **Contract**: amend `specs/009-scheduled-drift-response/contracts/orchestrator-schedules.md` ("Activity: stand_down_schedule") and research R8, which says the guard was deferred.
- **Tests**: use stand-in blocks in `test_workflow_drift_response.py`:
  - 3 of 4 devices drifted: no reconcile child, stood down, the note's text;
  - 2 of 4: repairs;
  - 1 of 1: repairs;
  - `report` mode: no guard;
  - a replay of a pre-guard history (the file's existing replay pattern, if it has one; otherwise `Replayer` over a history captured from the current code) is unchanged.
- **Changelog**: `changelog/136.added.md`. The PR says `Refs #136`, because the Temporal identity check stays open.

### D. #305, the page's height: `presentation` UI

- **`Runs.tsx`**: add `const CAP = 10`. Show `ordered.slice(0, CAP)` until "Show all N runs" is pressed. The button is separate from "Show every device's runs". Running runs stay first, so a running run is never hidden.
- **`App.tsx`**: `DEFAULT_OPEN = []`. Check `sameSet` and the `?open=` round-trip with an empty default. Update the tests that assume `metrics-timing` is open by default: `App.test.tsx` lines ~424 and ~467, and `e2e/smoke.spec.ts` ~207.
- **Spec 014 SC-003** ("the first metrics chart within 2000 px") has no chart to measure once everything is folded. Amend it to the Metrics section's first group heading, and change `e2e/layout.spec.ts` and `e2e/look.mjs`'s measure to match. List this under "Spec amendments".
- **Measure**: run `e2e/look.mjs` on the fixture server before and after. Put both page heights and the Metrics section's top in the PR.
- **Changelog**: `changelog/305.changed.md`. Use `Refs #305`, since SC-001 is checked on the lab.

### E. #235, a gzipped snapshot: `presentation` and its UI

**Writer** (`snapshot.py`): `render` gzips the compact JSON at level 9, base64-encodes it, and puts it in the element with a marker, for example `<script id="snapl-bundle" type="application/json" data-encoding="gzip+base64">`. Keep the element's id and its single-occurrence check. The template can stay as it is if the marker is an attribute that `render` adds.

**Reader** (`inline.ts`):
- `readBundle` reads `data-encoding`. With none, it parses JSON as today (FR-007).
- With `gzip+base64`, it decodes the base64 and pipes it through `new DecompressionStream("gzip")`.
- That makes `readBundle` async, so `providerFor` and `main.tsx`'s start become async, and `main.tsx` keeps its refusal path for a bundle it can't read.
- Without `DecompressionStream`, the page says "This snapshot needs a newer browser".

**CLI** (`snapl dashboard --snapshot`):
- `--bundle-json FILE` writes `json.dumps(bundle, indent=2)`.
- The summary prints the file's size and the unpacked bundle's size. It points at `--bundle-json` in its "Read it before publishing" note.
- Update `contracts/cli-and-build.md` in spec 011 (the bundle element, the flag), and amend it under "Spec amendments".

**Tests**:
- `test_dashboard_snapshot.py`:
  - round-trips a packed bundle;
  - checks the marker;
  - checks that `--bundle-json` writes equal JSON;
  - checks the summary's two sizes.
  - Its existing tests parse the element. Give them one helper that unpacks it.
- `inline.test.ts`:
  - reads a packed bundle;
  - still reads a plain one;
  - refuses a corrupt one with a message.
  - jsdom may lack `DecompressionStream`. Use Node's global one, or polyfill it in the test setup from `node:stream/web`; this adds no dependency.
- `e2e/snapshot.spec.ts` keeps its 3 MB budget and opens the packed file from disk.

**Measure** (SC-002): build the UI (`uv run invoke dashboard.build`). Make 7-day and 30-day snapshots from the 2026-11-20 archive into a git-ignored path, and record the sizes in the PR. That archive gave 3.50 MB and 5.40 MB before this change.

**Changelog**: `changelog/235.changed.md`. Use `Closes #235` if both sizes are under 3 MB.

### F. #376, housekeeping: docs only

For each open box in specs 001, 003 and 005, find the file, test or behaviour it names on `origin/main`, and tick it when it exists. Where the code took another shape, tick it with a short "*Shipped as …*" note. Leave open, and list in the PR, any task that isn't done. Don't touch specs 010, 011, 014 or 017: their open boxes are the maintainer's.

In the run's final report, propose which umbrella issues could close, with one line of evidence each, for example #146, #101 and #246. Don't close them. The label is `skip-changelog`.

## Constitution Check

- **TDD**: each slice's tests come first and are seen failing. For A and B, "failing" means red under the forced race or delay.
- **Spec-driven**: this spec, plan and tasks exist before any code.
- **No new dependencies.** No lab access overnight: **Needs the lab** tasks are deferred to the maintainer.
- **VIII (safe automation)**: the guard only makes a remediate schedule do less. It never repairs more than today.
