# Implementation Plan: Dashboard Look Refresh

**Branch**: `overnight/014-*` (one branch per PR slice) | **Date**: 2026-11-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/014-dashboard-look/spec.md`

## Summary

Restyle and reorder spec 011's designed dashboard without changing what it knows. The work is all in `packages/presentation/ui/`, plus a "Look" section in the dashboard skill and the docs. It adds a number row, one panel frame, a spacing and type scale that a test enforces, a folded lane row, a history chart, lighter metrics charts with a large latest value, a fabric summary, a header clock and a compact loop strip.

## Technical Context

**Language/Version**: TypeScript 5.9, React 19, built with Vite 8 on Node 22.12 or later (spec 011). No Python changes apart from none: the server is untouched.
**Primary Dependencies**: Tailwind 4 and Recharts 3.10, both already in the UI. No dependency is added (FR-036).
**Storage**: none. The page reads the v1 API as before.
**Testing**: Vitest with Testing Library for units, Playwright for the browser checks, both from spec 011. The look check (R6) renders the page from fixtures and saves pictures.
**Target Platform**: a desktop browser, 1280 px wide as the reference, down to 360 px with no sideways scroll.
**Project Type**: a UI inside the `presentation` package.
**Performance Goals**: no new request per page load. The number row and the fabric summary are derived from answers the page already has.
**Constraints**: no change to the v1 contract, to `SURFACE` in `lib/encoding.ts`, or to any rule in `dev/skills/dashboard/SKILL.md`. No JavaScript animation. The snapshot stays one file and grows by less than 10% (SC-008).
**Scale/Scope**: about 9 PR slices, each a component and its test.

## Constitution Check

*GATE: checked before research and again after design.*

| Principle | How this feature meets it |
| --- | --- |
| I. NAF blocks | All code is in `presentation`. No other block changes. |
| II. Spec-driven | This spec, plan and tasks exist before any code. |
| III. Test-first (applies to UI code, constitution 2.2.0) | Every component and helper has its test written and seen failing first. New logic keeps 80% line coverage (SC-007). |
| Boundary evidence | The feature touches no boundary: no gNMI, Infrahub, Temporal or Prometheus call changes. The only live work is retaking screenshots, marked **Needs the lab**. |
| Honest claims | The repair duration stays "scan to scan". A card with no data says so. No number is shown that the API doesn't serve. |

No violation, so the Complexity Tracking table is empty.

## Project Structure

### Documentation (this feature)

```text
specs/014-dashboard-look/
├── spec.md
├── plan.md
├── research.md          # R1 to R9: what was measured and decided
├── data-model.md        # the view models the UI derives
├── quickstart.md        # how to check the result by hand
├── checklists/requirements.md
└── tasks.md
```

There is no `contracts/` folder: the feature changes no contract.

### Source Code (repository root)

```text
packages/presentation/ui/
├── e2e/
│   ├── screenshot.mjs            # waits for the loaded page (R6)
│   ├── look.mjs                  # new: the look check's pictures and measurements
│   └── smoke.spec.ts             # updated where the page's structure changes
└── src/
    ├── index.css                 # scale tokens, shadow token
    ├── look.test.ts              # new: fails on spacing or type outside the scale
    ├── parity.test.ts            # rows repointed to the components that now show each fact
    ├── App.tsx                   # the new order, the row, the summary, the toolbar
    ├── lib/
    │   ├── numbers.ts            # new: the four cards' view models
    │   ├── summary.ts            # new: the fabric summary's view model
    │   ├── historyBars.ts        # new: history rows as chart bars
    │   ├── episodes.ts           # the headline's open drifts as a count and chips
    │   └── metrics.ts            # the lead value, per-minute wording
    └── components/
        ├── ViewFrame.tsx         # header, one action, footer
        ├── NumberRow.tsx         # new
        ├── GroupLabel.tsx        # new
        ├── Clock.tsx             # new
        ├── Shell.tsx             # the clock
        ├── LoopStrip.tsx         # compact and tiled forms
        ├── topology/FabricSummary.tsx   # new: ring and ranked bars
        ├── timeline/Lanes.tsx    # the folded row
        ├── timeline/Headline.tsx # count and chips
        └── panels/
            ├── History.tsx       # chart, table behind a toggle
            ├── Metrics.tsx       # lead value, legend, lighter chart
            └── RangePicker.tsx   # a sticky toolbar

dev/skills/dashboard/SKILL.md     # the "Look" section
docs/, README.md                  # words and pictures, last
```

**Structure Decision**: every new unit sits beside the component it serves, with its test next to it, as spec 011 laid the UI out. Logic that picks numbers lives in `lib/` so it is tested without rendering.

## Design

### Order of work

1. **Tools and rules first.** The screenshot script must capture a loaded page, and the look check must exist, before any slice can look at itself. The scale tokens and the test that enforces them come with it, with today's violations listed as a shrinking allowlist (R3).
2. **The frame.** Every later panel lands in it.
3. **The number row**, the largest visible change.
4. **Dead height**: lanes, headline, history.
5. **Metrics** and the range toolbar.
6. **Fabric summary, clock and the page order.**
7. **Loop strip and the last spacing pass**, which empties the allowlist.
8. **Docs.**

### The scale (R2, R3)

- Spacing: Tailwind steps 2, 4, 6, 8 (8, 16, 24, 32 px) for gaps, padding and margins between blocks. Steps 0.5, 1 and 1.5 are allowed only inside chips, badges, marks and table cells' inline padding, by an explicit marker class.
- Type: 12 (metadata), 14 (body), 17.5 (panel titles), 22 (section headings), 27 and 34 (large numbers). They are defined once as theme tokens, so the classes read `text-meta`, `text-body`, `text-title`, `text-heading`, `text-number`, `text-number-lg`.
- Shadow: one token, `0 8px 16px` tinted with the page colour at 8% in light, none in dark, where the border stays.

### The number row (R4)

`lib/numbers.ts` takes the overview's devices, the drift timeline and the history, and returns four card models. It makes no request. Each model has a `state`: `known`, `nothing_to_compare`, `unavailable` or `present_only`. The component only renders models.

### The look check (R6)

`e2e/look.mjs <base url> <out dir>` captures the full page and the first screen in light and dark, with no device selected and with one selected, and prints the page height and the offset of the first metrics chart. It waits until no panel says "Loading…". The overnight run serves fixtures with `--sources up` on a free port, runs the check, reads the pictures, and copies them to `.snapl/overnight/look/<slice>/` in the main checkout.

### What stays the same

- The data providers, polling intervals and URL state.
- The topology drawing and every drift-state mark.
- The archive and snapshot behaviour of each view.
- The plain page.

## Complexity Tracking

No constitution violation to justify.
