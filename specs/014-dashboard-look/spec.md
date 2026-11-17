# Feature Specification: Dashboard Look Refresh

**Feature Branch**: `overnight/014-spec-dashboard-look`
**Created**: 2026-11-15
**Status**: Draft
**Input**: Issue #246 (part of #101), spec 011's designed dashboard, and a review of the live page on 2026-11-15
**Scope settled**: 2026-11-15 with the maintainer, who accepted the review's seven look items. The points this spec decided without asking are listed under Assumptions.

## Context

Spec 011 built the designed dashboard. It is correct and complete, and it is long and flat to look at. The review on 2026-11-15 measured the live page at 1280 px wide:

- The page is 4083 px tall. The first metrics chart starts about 2650 px down.
- 18 lanes for devices no longer in intent take about 650 px and show nothing but hatching.
- The headline lists 11 open drifts by device id.
- The hourly history is a 25-row table, and most rows say "no scans".
- Almost all text is one of two sizes. No number on the page is larger than the headline's.
- When the latest run is a scheduled scan, four of the loop strip's six blocks read "Not recorded".

This feature changes how the page looks and how it is ordered. It adds no fact the API doesn't already serve, and it removes none.

### What this feature must never do

- Change the v1 API, the worker, the audit trail or anything in the lab.
- Remove a fact from the designed page. Spec 011 FR-029 and its parity test still hold.
- Let colour carry state on its own, or weaken any rule in `dev/skills/dashboard/SKILL.md`.
- Change the panel surface colours that the contrast tests are measured against.
- Load anything from another origin, or add a file the single-file snapshot can't carry.
- Call the repair duration anything but scan to scan. It is never time to detect.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Read the Fabric's State in One Glance (Priority: P1) 🎯 MVP

The operator opens the dashboard and, before scrolling, reads four numbers: how many devices are in sync, how many drifts are open, how long the latest repair took, and how many deploys and repairs ran in the last 24 hours. Each number says where it came from and what it is compared with.

**Why this priority**: This is the largest single change to what the page says at first sight, and it is what a screenshot of the page shows.

**Independent Test**: Render the number row from fixtures and check each card's number, label, comparison line and link target. Check the cases where a number isn't known.

**Acceptance Scenarios**:

1. **Given** six devices in intent and five in sync, **When** the page loads, **Then** the first card reads "5 of 6" with the label "Devices in sync".
2. **Given** two drifts open on devices in intent and three on devices no longer in intent, **When** the page loads, **Then** the second card reads "2", and a line below says three more are on devices no longer in intent.
3. **Given** the latest repaired drift lasted 13 s and closed three days ago, **When** the page loads, **Then** the third card reads "13 s", says "scan to scan", names the device and gives the repair's age.
4. **Given** no scan ran in the 24 hours before the last 24 hours, **When** the page loads, **Then** a card's comparison line says there is nothing to compare with. It does not show a change.
5. **Given** the audit trail is unavailable, **When** the page loads, **Then** the cards that read it say so in words. They do not show zero.
6. **Given** the page shows a past time, **When** the operator reads the row, **Then** every card is as of that time or says it shows only the present.

---

### User Story 2 — Every Panel Reads the Same Way (Priority: P1)

Every panel has the same frame: its title on the left, at most one action on the right, and its source and age in a quiet footer. Spacing and type sizes follow one scale, so headings, numbers and metadata are told apart by size and not only by weight.

**Why this priority**: The frame is used by every panel. Changing it first means each later panel lands in its final form.

**Independent Test**: Render the frame in each of its states (loading, available, stale, unavailable, not in the archive, present only) and check the header, the action and the footer. A test reads the UI's class names and fails on spacing or type sizes outside the scale.

**Acceptance Scenarios**:

1. **Given** any panel with data, **When** it renders, **Then** its source and age are in its footer and its header holds the title and at most one action.
2. **Given** a panel whose source is down, **When** it renders, **Then** the reason and the remedy command are shown as before (spec 011 FR-030).
3. **Given** the light theme, **When** a panel renders, **Then** it is lifted from the page by a soft shadow. **Given** the dark theme, **Then** it keeps its border.
4. **Given** the viewer asks for reduced motion, **When** anything on the page changes, **Then** nothing animates (spec 011 FR-043).

---

### User Story 3 — Nothing on the Page Is Dead Height (Priority: P2)

Devices no longer in intent fold into one lane row that the operator can open. The headline gives open drifts as a count with the devices as chips. The hourly history is a chart, and its table is one click away.

**Why this priority**: These three places are most of the page's wasted height, and each hides the live devices' story behind rows that say nothing.

**Independent Test**: Render the lanes with devices in and out of intent and check the folded row, its count and its expansion. Render the history from fixtures and check the chart's bars, its text alternative and the table behind the toggle.

**Acceptance Scenarios**:

1. **Given** 8 devices in intent and 18 no longer in intent, **When** the lanes render, **Then** 8 lanes are drawn and one row says "18 devices no longer in intent", closed by default.
2. **Given** the folded row, **When** the operator opens it, **Then** every one of the 18 lanes is shown as before.
3. **Given** a device no longer in intent is the selected device, **When** the lanes render, **Then** the folded row is open.
4. **Given** 24 hourly buckets with scans in three of them, **When** the history renders, **Then** it is a chart with one position per bucket, and the buckets with no scan are visibly empty, not zero.
5. **Given** the history chart, **When** the operator opens "Show the table", **Then** the table of spec 011 is shown with every column it had.

---

### User Story 4 — The Charts Come First and Read at a Distance (Priority: P2)

The metrics sit above the run, CI and lab panels. Each metrics panel leads with its latest value in large type and draws a lighter chart: faint gridlines, no axis lines, a soft fill under each line. The range picker is a toolbar that stays in reach, not a panel of its own.

**Why this priority**: The measured metrics are the newest thing snapl has, and they are at the bottom of the page.

**Independent Test**: Render each metrics panel from fixtures and check the large value, its unit, the legend and that the latest-values table is still reachable. Check the page's section order.

**Acceptance Scenarios**:

1. **Given** a duration view with one series, **When** it renders, **Then** the latest value is the largest text in the panel, with its unit.
2. **Given** a view with several series, **When** it renders, **Then** the legend under the title names each series with its latest value, and the full table of latest values is one click away.
3. **Given** the gNMI errors view, **When** it renders, **Then** values read per minute, not per second.
4. **Given** the operator scrolls to the metrics, **When** they want another range, **Then** the range control is on screen.
5. **Given** an archive or a recording, **When** the page renders, **Then** the metrics behave as spec 011 says for that mode.

---

### User Story 5 — The Fabric Panel Carries Its Own Summary (Priority: P3)

When no device is selected, the space beside the fabric shows the share of devices in sync as a ring with the count written in it, and the drifted devices ranked by how many paths differ. The page header shows the time the page is showing as a clock: running when live, still in an archive, a recording or at a past time.

**Why this priority**: It fills the emptiest part of the first screen with the two facts an operator asks next. It depends on the frame and the number row.

**Independent Test**: Render the summary from fixtures with all in sync, some drifted, and none scanned. Render the clock in each mode.

**Acceptance Scenarios**:

1. **Given** five of six devices in sync and no device selected, **When** the page renders, **Then** the ring shows five sixths filled and "5 of 6" in words inside it.
2. **Given** two drifted devices, **When** the page renders, **Then** they are listed with a bar each, the device with more differing paths first, each with its count and its state's shape.
3. **Given** the operator selects a device, **When** the drawer opens, **Then** it takes the summary's place, and closing it brings the summary back.
4. **Given** an archive, **When** the page renders, **Then** the clock shows the archive's time and does not tick.

---

### User Story 6 — A Run With No Recorded Steps Doesn't Read as Broken (Priority: P3)

When a run's steps are not recorded one by one, the loop strip shows the run's outcome on one compact row and names the six blocks without drawing six empty boxes. A run with recorded steps draws the six blocks as rows with an icon tile each.

**Why this priority**: The strip carries the "six NAF blocks" claim, and today it most often shows four blocks as "Not recorded". Recording scan steps is a later spec; this story only stops the empty case from dominating.

**Independent Test**: Render the strip from the fixtures for a recorded deploy and an unrecorded scan and check both forms.

**Acceptance Scenarios**:

1. **Given** a scheduled scan whose steps are not recorded, **When** the strip renders, **Then** it shows the run, its outcome and the sentence that its steps are not recorded one by one (spec 011 FR-028), in one row.
2. **Given** a deploy with recorded steps, **When** the strip renders, **Then** all six blocks are drawn in loop order with their status, glyph, word and time.

---

### Edge Cases

- No device in intent: the first card says so, and the ring is not drawn.
- More than 32 devices: the fabric drawing is already replaced by the device list (spec 011). The summary still renders.
- A number wider than its card (a long duration, a four-digit count): it wraps or shrinks. It is never cut off.
- A narrow window: the number row wraps to two columns, then one. Nothing scrolls sideways.
- The history range has no scan at all: the chart area says so in words.
- The snapshot file: every new panel works from the inlined data, with no request.
- An unknown drift state or metrics unit from a newer server: shown by its raw text, as before.

---

## Requirements *(mandatory)*

### Functional Requirements

**The number row**

- **FR-001**: The page MUST show a row of four cards above the fabric: devices in sync, open drifts, latest repair, and deploys and repairs in the last 24 hours.
- **FR-002**: Each card MUST show a label, one large value, a line of context, and a link that moves to the panel holding its detail.
- **FR-003**: The devices card MUST count only devices in intent, as "x of y".
- **FR-004**: The open-drifts card MUST count open drifts on devices in intent. Open drifts on devices no longer in intent MUST be given as a separate count in the context line.
- **FR-005**: The latest-repair card MUST give the duration as scan to scan, the device, what repaired it and how long ago.
- **FR-006**: The fourth card MUST give deploys and repairs over the last 24 hours as succeeded and failed counts, from the history the API already serves.
- **FR-007**: A card whose comparison window holds no data MUST say there is nothing to compare with. A card whose source is unavailable MUST say so in words and MUST NOT show zero.
- **FR-008**: At a past time, each card MUST be as of that time or say it shows only the present.
- **FR-009**: A card's state MUST be readable without colour: the value and its words carry it.

**The frame and the scale**

- **FR-010**: Every panel MUST use one frame: the title on the left of its header, at most one action on the right, and its source and age in a footer. Spec 011 FR-030 still holds in full.
- **FR-011**: Spacing MUST be in multiples of 8 px. 4 px is allowed only inside chips, badges and marks.
- **FR-012**: Type sizes MUST come from one scale that steps by about 1.25 from a 14 px base. 12 px is for metadata only. Large numbers use a tight line height; body text uses 1.4 to 1.6.
- **FR-013**: Running text (headline sentences, failure reasons, error messages, notes) MUST be kept to a readable line length.
- **FR-014**: In the light theme a panel MUST be separated from the page by a shadow tinted with the page colour. In the dark theme it MUST keep its border.
- **FR-015**: The panel surface colours MUST NOT change. Any new colour MUST be a token with a light and a dark value, and MUST meet the contrast rules of the dashboard skill.
- **FR-016**: Groups of panels MUST have a small label above them.
- **FR-017**: The style rules MUST be written into `dev/skills/dashboard/SKILL.md` as a "Look" section, and a test MUST fail when the UI's source breaks FR-011 or FR-012.

**Dead height**

- **FR-018**: Lanes for devices no longer in intent MUST be folded into one row that gives their count and can be opened. It MUST be open when the selected device is one of them.
- **FR-019**: The headline MUST give open drifts as a count, with the devices in intent as chips. It MUST NOT list device ids in a sentence.
- **FR-020**: The history MUST be drawn as a chart with one position per bucket, showing scans, the most drifted devices, and deploys and repairs by outcome. A bucket with no scan MUST read as empty, not as zero.
- **FR-021**: The history table of spec 011 MUST remain available from the chart with every column, and the chart MUST have a text alternative.

**Metrics and order**

- **FR-022**: The page order MUST be: number row, fabric with its summary or drawer, the loop, drift over time, history, metrics, schedule and running, runs, undesired config, CI and lab evidence, sources, links.
- **FR-023**: Each metrics panel MUST lead with its latest value in large type with its unit. With several series, it MUST show a legend with each series' latest value.
- **FR-024**: The latest-values table of each metrics panel MUST remain reachable, because it is the chart's text alternative.
- **FR-025**: Line charts MUST use faint horizontal gridlines, no axis lines, and a light fill under each line. A gap in the data MUST stay a gap.
- **FR-026**: A per-second rate below one per second MUST be shown per minute, and the panel MUST say which.
- **FR-027**: The range control MUST stay on screen while the operator scrolls the views it governs. It MUST be absent in a recording, as now.

**The fabric summary and the clock**

- **FR-028**: When no device is selected, the space beside the fabric MUST show the share of devices in sync as a ring with the count in words, and the drifted devices ranked by differing paths.
- **FR-029**: Each ranked device MUST show its state's shape and its count, and selecting it MUST open its drawer.
- **FR-030**: The header MUST show the time the page is showing, in UTC, in a monospace face. It MUST advance only in live mode at the present.

**The loop strip**

- **FR-031**: A run whose steps are not recorded MUST be shown on one compact row with its outcome and the sentence spec 011 FR-028 requires.
- **FR-032**: A run with recorded steps MUST show the six blocks in loop order, each with an icon tile, its status glyph and word, its reason when it failed, and its time.

**Across the feature**

- **FR-033**: Every fact the designed page showed before this feature MUST still be on it. The parity test MUST pass unchanged in what it requires.
- **FR-034**: Every new panel MUST work in live, archive and snapshot modes, and at a past time.
- **FR-035**: Nothing new MUST animate with JavaScript. Any transition MUST be CSS and MUST stop under reduced motion.
- **FR-036**: The feature MUST add no runtime dependency to the UI and no image that is not inlined.
- **FR-037**: Each UI slice MUST be looked at before it ships: the page rendered from fixtures in light and dark, and the pictures read.
- **FR-038**: The README, the dashboard guide and spec 011's screenshots MUST be retaken once the feature is complete.

### Key Entities

- **Number card**: a label, a value, a context line, a link target and a state (known, nothing to compare, unavailable, present only).
- **Fabric summary**: the in-sync count, the total in intent, and the drifted devices with their differing-path counts.
- **History bar**: one bucket's scans, most drifted devices, and deploy and repair counts by outcome, or "no scans".
- **Look rules**: the spacing scale, the type scale and the shadow rule, as written in the dashboard skill.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: At 1280 × 900, the number row and the fabric are fully visible without scrolling.
- **SC-002**: On the live lab page that was measured at 4083 px on 2026-11-15 (26 lanes, 18 of them for devices no longer in intent), the full page at 1280 px wide is at least 25% shorter than before this feature. *Amended in slice G: this was first written against the captured fixtures. They have no device outside intent, so the height this criterion is about is not in them, and with the number row and the larger frame the fixture page is longer than before (5191 px against 4169 px). The maintainer decides whether the amended criterion is the right one.*
- **SC-003**: The top of the first metrics chart is within 2000 px of the top of the page on the same fixtures. *Met again in #290 (2026-11-17), after spec 015's panels had moved it to 3210 px: Drift and deploys by hour, Fabric history and Drift by device are folded behind their headings, and only the first metrics group, The loop's timing, is open by default. On the fixtures the first metrics chart is at 1974 px and the page is 5444 px (8423 before). A link from the number row opens the panel it points to, and the open panels are kept in the address (`?open=`).*
- **SC-004**: The designed page, the plain page and `snapl status --json` still agree on every device's state and on the schedule (spec 011 SC-011).
- **SC-005**: Every contrast and encoding test of spec 011 passes without a change to what it requires.
- **SC-006**: The UI's source contains no spacing or type size outside the scale, outside the listed exceptions.
- **SC-007**: New UI logic keeps at least 80% line coverage, and the UI's browser checks pass.
- **SC-008**: The snapshot file still opens from disk with no request, and its size on the test fixtures grows by less than 10%.

---

## Assumptions

- **The fourth card is deploys and repairs, not all runs.** The API has no count of runs over 24 hours, and this spec changes no API. A runs count is left to the metrics spec.
- **Open drifts are counted on devices in intent.** PR #241 fixes how the server counts them (#236). The card counts from the lanes' own "in intent" flag, so it is right with or without that fix.
- **The comparison line compares with the 24 hours before.** It is shown only for the devices and open-drifts cards, where the timeline gives both windows.
- **The latest-repair card is not a rate.** It is one measured repair, with its date.
- **Recording scan steps is out of scope.** The compact loop strip is the UI's share; the capture belongs to the metrics spec.
- **Icons are drawn inline.** No icon library is added.
- **Per minute is the readable unit for rare events.** The server's unit stays `per_second`; only the wording changes.
- **The reference designs are inspiration only.** Their treatment is recreated; none of their code, assets or branding is used.

## Out of Scope

- Any new metric, any new API route or field, and any worker change. These are the next spec.
- Device telemetry from the fabric (interfaces, BGP, CPU).
- The fixes in PR #241 (#191, #192, #236).
- Navigation, a sidebar or a greeting.
- New state colours or a change to the drift-state shapes.
- The plain page.

## Dependencies

- Spec 011 (the designed dashboard) and spec 012 (the metrics views), both merged.
- `dev/skills/dashboard/SKILL.md` and `lib/encoding.ts`, which this feature extends and must not weaken.
- `dev/scripts/serve_dashboard_fixtures.py`, which gives a page with no lab.
- PR #241 is not required. It touches `lib/episodes.ts`; whichever merges second resolves that file.
