# Tasks: Dashboard Look Refresh

**Input**: Design documents from `/specs/014-dashboard-look/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md

**Tests**: TDD is mandatory per CLAUDE.md and the constitution, for UI code as for Python (spec 011 FR-046). Every phase lists its tests first, and each test is run and seen to fail for the expected reason before the code it covers is written.

**Organization**: Tasks are grouped by user story (US1 to US6). The tools and the rules come first, because every later slice uses them to look at itself.

**PR slicing**:
- **PR A**: Phase 1 (tools and rules: the loaded-page wait, the look check, the scale tokens and their test, the skill's Look section)
- **PR B**: Phase 2 (the frame)
- **PR C**: Phase 3 (the number row)
- **PR D**: Phase 4 (lanes and headline)
- **PR E**: Phase 5 (history chart)
- **PR F**: Phase 6 (metrics and the range toolbar)
- **PR G**: Phase 7 (fabric summary, clock, page order)
- **PR H**: Phase 8 (loop strip and the last spacing pass)
- **PR I**: Phase 9 (docs)

A UI slice is a component and its test together, so it may be larger than a Python slice. When one passes about 800 lines of non-test code, split it at a component boundary and say so in the PR.

Each PR says `Closes` a task issue under #246 and carries `changelog/246.changed.md` (or appends to it).

**Lab rule for unattended runs**: a task marked **Needs the lab** runs the Compose stack, the maintainer's worker, the live dashboard or a device. Leave it unticked and defer it (dev/guides/overnight-runs.md). Tests may start their own servers on free ports, a headless browser, the fixture server (`dev/scripts/serve_dashboard_fixtures.py`) and `npm ci` from the lockfile. That is not the lab.

**The gate for every slice** (the runbook's gate is Python only, so add the UI's):

```bash
uv run invoke lint && uv run mypy packages/ && uv run bandit -r packages/ -c pyproject.toml -q && uv run pytest tests/unit -q
uv run invoke dashboard.test && uv run invoke dashboard.e2e
```

**The look check for every slice from PR B on** (FR-037). Tests cannot say whether the page looks right, so look at it:

1. `uv run invoke dashboard.build`
2. Start `uv run python dev/scripts/serve_dashboard_fixtures.py --port 18932 --sources up` in the background, and stop it afterwards. Use another free port if 18932 is taken. Never use the live dashboard on 18030.
3. `node packages/presentation/ui/e2e/look.mjs http://127.0.0.1:18932 <out dir>`
4. Read every picture. Fix what is wrong before opening the PR: text cut off or overlapping, a panel that is empty or still loading, a mark with no outline, uneven gaps, anything unreadable in dark, sideways scroll.
5. Copy the pictures to `/Users/anton/Documents/PYPROJECTS/snapl/.snapl/overnight/look/<slice>/` and note the page height and the first chart's offset in the report and the PR body.

**Rules that hold in every task**:
- No change under `packages/presentation/snapl_presentation/`, to the v1 contract, to `SURFACE` in `lib/encoding.ts`, or to what `encoding.test.ts` and `parity.test.ts` require. A parity row may be repointed to the component that now shows its fact.
- No new runtime dependency. Icons are inline SVG. Transitions are CSS only.
- Every new panel is checked in live, archive and snapshot modes and at a past time (`App.archive.test.tsx`, `App.snapshot.test.tsx`, `App.past.test.tsx`).
- The repair duration is "scan to scan". Never "time to detect".
- PR #241 touches `lib/episodes.ts`. If it has merged, build on it. If a conflict appears later, resolve it by keeping both changes.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 to US6 from spec.md
- Exact file paths in every description

---

## Phase 1: Tools and Rules (US2) — PR A

**Goal**: a slice can look at a loaded page, and the scale exists and is enforced on new code.

- [x] T001 [US2] In `packages/presentation/ui/e2e/screenshot.mjs`, wait until no element with the text "Loading…" is left before each screenshot, and fail with a message naming the panel if one remains after 30 s (research R6). Check it by running it against the fixture server with `--sources up` and reading one picture.
- [x] T002 [US2] Add `packages/presentation/ui/e2e/look.mjs <base url> <out dir>`: full page and first screen (1280 × 900) in light and dark with no device selected, the full page in light with `?device=leaf-01`, and the full page at 360 px wide. It uses the same wait as T001 and prints, as JSON, the page height and the offset from the top of the first element inside the "Metrics" section. Files: `full-light.png`, `full-dark.png`, `first-screen-light.png`, `first-screen-dark.png`, `device-light.png`, `narrow-light.png`.
- [x] T003 [US2] Run the look check on the page as it is now and record the baseline here: page height 4169 px, first metrics chart at 2877 px (fixtures, `--sources up`, 1280 px wide, light). SC-002 and SC-003 are measured against these. Copy the pictures to `.snapl/overnight/look/baseline/`.
- [x] T004 [US2] Test first: `packages/presentation/ui/src/look.test.ts` scans `src/**/*.tsx` (not tests) and fails on a spacing or type class outside the rules in data-model.md "Look rules", except in files on its allowlist. It also fails when a file on the allowlist has no violation left, so the list can only shrink. See it fail on a deliberate violation in a scratch string, then list today's violating files.
- [x] T005 [US2] In `packages/presentation/ui/src/index.css`, add the type tokens (`text-meta` 12, `text-body` 14, `text-title` 17.5, `text-heading` 22, `text-number` 27, `text-number-lg` 34, each with its line height: 1.5 for the first two, 1.3 for titles and headings, 1.15 for numbers), the `--shadow-panel` token (light: `0 8px 16px` of the page's ink at 8%; dark: none) and the `chip` and `cell` marker classes. No colour token changes.
- [x] T006 [US2] Add the "Look" section to `dev/skills/dashboard/SKILL.md`: the spacing rule, the type scale and what each size is for, the shadow rule, line length and line height, the 60/30/10 colour split and that amber covering much of the screen must only ever mean much drift, the frame (title, one action, footer), and step 4 of "Before shipping": run the look check and read the pictures. Keep the frontmatter description accurate.
- [x] T007 [US2] `changelog/246.changed.md`: start the fragment with one sentence for this slice.

**Checkpoint**: `look.mjs` produces six loaded pictures and two numbers, and `look.test.ts` passes with the allowlist in place.

---

## Phase 2: The Frame (US2) — PR B

**Goal**: one frame for every panel.

- [x] T008 [US2] Tests first in `packages/presentation/ui/src/components/ViewFrame.test.tsx`: the header holds the title and, when given, exactly one action; the source and age are in a `footer`; the unavailable, stale, not-in-archive, present-only, loading and error states still say what they said; the title uses `text-title`.
- [x] T009 [US2] Change `ViewFrame.tsx`: an optional `action` prop (one node), the footer, `p-6` padding with `gap-4` between header, body and footer, `shadow-panel` in light and the border in dark, an optional `id` for the number row's links. Running text inside warnings gets `max-w-prose`.
- [x] T010 [P] [US2] Tests first, then `components/GroupLabel.tsx`: a small uppercase, letter-spaced label above a group of panels, as a heading of the right level.
- [x] T011 [US2] Make `panels/History.tsx` use `ViewFrame` (it draws its own section today). Its tests keep passing; the parity row stays.
- [x] T012 [US2] In `App.tsx`, replace the bare "Metrics" and "Sources" headings with `GroupLabel`, and give the unreachable and past-time banners the scale's spacing and type.
- [x] T013 [US2] Remove `ViewFrame.tsx`, `History.tsx` and any file now clean from the allowlist in `look.test.ts`. Update `e2e/smoke.spec.ts` where it relied on the old header.
- [x] T014 [US2] Run the look check. Append this slice's sentence to `changelog/246.changed.md`.

**Checkpoint**: every panel shows its source and age in a footer, in both themes.

---

## Phase 3: The Number Row (US1) 🎯 MVP — PR C

**Goal**: four numbers at the top of the page.

- [x] T015 [US1] Tests first in `packages/presentation/ui/src/lib/numbers.test.ts`, one per rule of data-model.md "NumberCard": the counts with devices in and out of intent; open drifts split by the lane's `in_intent`; the latest repair's context sentence with "scan to scan"; the 24-hour sum skipping `null` buckets; `nothing_to_compare` when the timeline has no scan point at the earlier time; `unavailable` when a view has no data; `present_only` for the fourth card at a past time when the history is not as of that time; no device in intent.
- [x] T016 [US1] Implement `lib/numbers.ts` (research R4). It takes the devices view, the timeline, the history, `now` and whether a past time is shown, and returns four `NumberCard`s. No request.
- [x] T017 [US1] Tests first in `components/NumberRow.test.tsx`: four cards in order; the value in `text-number-lg`; the label, the context line, and a link to the target panel's id; a card with no value shows its context in words and no "0"; each card has an inline icon tile that is hidden from screen readers; the row is a list with an accessible name.
- [x] T018 [US1] Implement `components/NumberRow.tsx`: a grid of four, wrapping to two and then one column. Each card is a `ViewFrame`-styled surface without the frame's states.
- [x] T019 [US1] In `App.tsx`, render the row above the fabric, and give the Fabric, Drift over time, and History panels the ids the cards link to. Add the row to `App.test.tsx`, `App.past.test.tsx`, `App.archive.test.tsx` and `App.snapshot.test.tsx`.
- [x] T020 [US1] Add a browser check to `e2e/smoke.spec.ts`: the four cards' values on the fixtures, and that following a card's link moves to its panel.
- [x] T021 [US1] Run the look check: the row and the whole fabric panel fit the first screen (SC-001). Append to the changelog.

**Checkpoint**: the first screen reads as numbers, then the fabric.

---

## Phase 4: Lanes and Headline (US3) — PR D

**Goal**: devices no longer in intent stop taking the page over.

- [ ] T022 [US3] Tests first in `components/timeline/Lanes.test.tsx`: with lanes in and out of intent, only the in-intent lanes are drawn and one row says "n devices no longer in intent"; opening it shows them all as before; it is open when `selected` is one of them; with none out of intent there is no such row; each lane's words for screen readers are unchanged.
- [ ] T023 [US3] Implement the folded row in `Lanes.tsx` with a `<details>` element, and pass `selected` from `App.tsx`.
- [ ] T024 [US3] Tests first in `lib/episodes.test.ts` and `components/timeline/Headline.test.tsx`: the open drifts are a count with one chip per device in intent (name and how long), and a separate count for devices no longer in intent; no device id appears in a sentence; "No drift open." is unchanged; the latest repair's sentence is unchanged.
- [ ] T025 [US3] Change `lib/episodes.ts` to return the open drifts as data (the count, the in-intent devices with durations, the count of others), and `Headline.tsx` to draw the chips. The duration uses `text-number`.
- [ ] T026 [US3] Remove the files now clean from the `look.test.ts` allowlist. Update `e2e/smoke.spec.ts` if the replay check relied on the old lanes.
- [ ] T027 [US3] Run the look check. Append to the changelog.

**Checkpoint**: the lanes panel is as tall as the devices in intent.

---

## Phase 5: History Chart (US3) — PR E

**Goal**: the history is a picture, with its table one click away.

- [ ] T028 [US3] Tests first in `lib/historyBars.test.ts`: bars are oldest first; a quiet bucket has `null` counts and `quiet: true`; counts by outcome are carried; an empty history gives no bars.
- [ ] T029 [US3] Implement `lib/historyBars.ts` over `historyRows`.
- [ ] T030 [US3] Tests first in `components/panels/History.test.tsx`: a chart with an `aria-label` that points to the table; the table inside a closed `<details>` named "Show the table" with every column it had; the "no scans in this range" sentence when every bucket is quiet; the repair-run list is unchanged; the audit trail's start sentence is unchanged.
- [ ] T031 [US3] Implement the chart in `History.tsx` (research R5): bars for scans, a stepped line for the most drifted devices, deploy and repair marks by outcome in the ok and fail tones with a legend that gives each its glyph and word. No animation (`isAnimationActive={false}`). Gridlines faint and horizontal, no axis lines.
- [ ] T032 [US3] Remove `History.tsx` from the allowlist if it is still there. Check `parity.test.ts` still passes without loosening it.
- [ ] T033 [US3] Run the look check. Append to the changelog.

**Checkpoint**: the history panel is a chart about 240 px tall.

---

## Phase 6: Metrics and the Range Toolbar (US4) — PR F

**Goal**: the metrics read at a distance, and the range is always in reach.

- [ ] T034 [US4] Tests first in `lib/metrics.test.ts`: `leadValue` picks the highest latest value for `ms` and `per_second`, the first series that is down for `up`, and `null` with no data; `formatValue` gives "1.4/min" for 0.024 per second and keeps "/s" at 1 or more; an unknown unit is shown raw.
- [ ] T035 [US4] Implement `leadValue` and the per-minute form in `lib/metrics.ts`.
- [ ] T036 [US4] Tests first in `components/panels/Metrics.test.tsx`: the lead value in `text-number` with its unit and, with several series, which series it is; a legend under the title with each drawn series' name and latest value; the latest-values table inside a closed `<details>` and still named as the chart's text alternative; the step and unit line in the footer area; the "first 6 series" note unchanged; up/down strips unchanged in what they say.
- [ ] T037 [US4] Change `Metrics.tsx`: the lead value, the legend, an area fill under each line at low opacity (Recharts `Area` or a gradient, gaps still gaps, no animation), faint horizontal gridlines, no axis lines, chart height 192 px.
- [ ] T038 [US4] Tests first in `components/panels/RangePicker.test.tsx`: the presets and the current range in words are always shown; the custom fields are behind a disclosure; every existing behaviour (apply, refused range, archive's exported range) is unchanged.
- [ ] T039 [US4] Change `RangePicker.tsx` into a toolbar and make it sticky in `App.tsx` over the lanes, history and metrics (research R9). It stays absent in a recording.
- [ ] T040 [US4] Remove the files now clean from the allowlist. Run the look check, scrolling is not captured, so also check the toolbar's stickiness in `e2e/smoke.spec.ts`. Append to the changelog.

**Checkpoint**: each metrics panel leads with a number.

---

## Phase 7: Fabric Summary, Clock and Page Order (US5, US4) — PR G

**Goal**: the first screen is complete and the page is in its final order.

- [ ] T041 [US5] Tests first in `lib/summary.test.ts`: the counts; drifted devices ranked by differing paths then by name; `share` relative to the largest; a device in `error` listed with no bar; none scanned; none in intent.
- [ ] T042 [US5] Implement `lib/summary.ts` (data-model.md "FabricSummary").
- [ ] T043 [US5] Tests first in `components/topology/FabricSummary.test.tsx`: the ring's accessible sentence ("5 of 6 devices in sync"); the count as text inside the ring; the dashed ring when none is scanned; each ranked device with its state mark (`StateMark`), its count and a bar; selecting one calls `onSelect`; "No drift open." when none is drifted.
- [ ] T044 [US5] Implement `FabricSummary.tsx` (research R7): an inline SVG ring with an ink outline, and the ranked list. No colour without a shape or a word.
- [ ] T045 [US5] In `App.tsx`, show the summary beside the fabric when no device is selected and the drawer when one is. Tests in `App.test.tsx`.
- [ ] T046 [P] [US5] Tests first, then `components/Clock.tsx` (research R8): the page's time in UTC, monospace; advancing in live mode at the present; still in archive and snapshot modes and at a past time; minutes only under reduced motion; it starts from the server's time, never the browser's.
- [ ] T047 [US5] Put the clock in `Shell.tsx`'s header. Update `Shell.test.tsx`.
- [ ] T048 [US4] Reorder `App.tsx` to FR-022: number row, fabric, the loop, the range toolbar, drift over time, history, metrics, schedule and running, runs, undesired config, CI and lab evidence, sources, links, each group under a `GroupLabel`. Update the App tests and `e2e/smoke.spec.ts` for the order.
- [ ] T049 [US5] Remove the files now clean from the allowlist. Run the look check and record SC-001, SC-002 and SC-003 against the baseline of T003 in the PR body. Append to the changelog.

**Checkpoint**: SC-001 to SC-003 hold on the fixtures.

---

## Phase 8: Loop Strip and the Last Spacing Pass (US6, US2) — PR H

**Goal**: the empty loop reads as one line, and no file is left on the allowlist.

- [ ] T050 [US6] Tests first in `components/LoopStrip.test.tsx`: a run whose steps are not recorded shows one row with the run, its outcome, the FR-028 sentence and the six block names as a plain list, and no block boxes; a recorded run shows six rows or tiles in loop order, each with an icon tile, status glyph and word, reason and time; an unknown block or status is shown raw.
- [ ] T051 [US6] Implement both forms in `LoopStrip.tsx`. The takeaway (run, outcome, started) is a pill-shaped callout.
- [ ] T052 [US2] Restyle the remaining files on the allowlist to the scale: `DeviceDrawer.tsx` (device facts as value chips), `panels/Runs.tsx`, `Schedule.tsx`, `Undesired.tsx`, `Ci.tsx`, `LabEvidence.tsx`, `Links.tsx`, `SourceStrip.tsx`, `ModeBanner.tsx`, `Command.tsx`, the topology components. Table rows use `py-2`. Failure reasons, error messages and schedule notes get `max-w-prose`. Each file's tests keep passing; change a test only where it asserted a class.
- [ ] T062 [US2] Found by slice C's look check: at 360 px wide the page is 388 px wide, so it scrolls sideways (it was 372 px before spec 014). Find what overflows (`narrow-light.png`), fix it, and add 360 to the widths `e2e/smoke.spec.ts` checks for sideways scroll, test first.
- [ ] T053 [US2] Empty the allowlist in `look.test.ts` and make the test fail if it is ever non-empty again (SC-006).
- [ ] T054 [US6] Run the look check on the default view and on the `loop` view of `screenshot.mjs`. Check the snapshot's size against SC-008 with `e2e/snapshot.spec.ts`'s fixtures and record it. Append to the changelog.

**Checkpoint**: `look.test.ts` passes with no allowlist.

---

## Phase 9: Docs (all stories) — PR I

**Goal**: the words match the page.

- [ ] T055 [P] Update the dashboard's description in `README.md` and `docs/demo-scenarios.md` where they describe the page's layout or name a panel that moved. Claim only what the page shows.
- [ ] T056 [P] Add a short "Look" note to ADR-0005 Decision 2 in `dev/adr/0005-dashboard-and-metrics-stack.md` pointing to the dashboard skill, and add spec 014 to `CLAUDE.md` "Recent Changes".
- [ ] T057 Finish `changelog/246.changed.md` as one readable paragraph.
- [ ] T061 Last task of this slice: point `.specify/feature.json` at `specs/015-more-metrics`, so the next overnight pass carries on with spec 015. Do it only when every task of this spec that doesn't need the lab is ticked.
- [ ] T058 **Needs the lab**: quickstart item 7, the four cards, the ring and the ranked bars against `snapl status` on the live fabric with one induced drift.
- [ ] T059 **Needs the lab**: quickstart item 8, retake `docs/images/dashboard-dark.png` and spec 011's screenshots from the live demo, after reseeding the Source of Truth so the integration tests' `query-*` devices are gone.
- [ ] T060 **Needs the lab**: decide from the morning's pictures whether the look is right, and file what isn't as issues under #246.

---

## Dependencies

- PR A before all others. PR B before C to H. PR G after C (the summary sits under the row). PR H last of the UI slices, because it empties the allowlist. PR I last.
- D, E and F touch different components and may land in any order after B.

## Deferred to spec 015 (more metrics)

- Recording scan steps so the loop strip has six blocks to show for a scheduled scan.
- A runs-in-24-hours count, more metrics views, new worker instruments and device telemetry.
