# Research: Dashboard Look Refresh

Each entry gives the decision, why, and what was set aside.

## R1. What the live page looks like today (measured 2026-11-15)

Measured on the live dcfabric dashboard at 1280 px wide, light theme, `main` at 872d143.

- Full page height: 4083 px. First metrics chart: about 2650 px down.
- Lanes: 26, of which 8 are in intent. The 18 others are hatched "no data" rows, about 650 px.
- Headline: 11 open drifts, each named by device id, all on devices no longer in intent (#236).
- History table: 25 hourly rows, one with scans.
- Class counts in the UI's source: `text-xs` 58, `text-sm` 46, `text-base` 2, `text-3xl` 1; `py-1` 31, `mt-1` 24, `mb-3` 15, `mt-3` 10, `p-3` 7.
- Latest run: a scheduled `ScanDrift`. All 50 recent runs were scheduled `ScanDrift` or `DriftResponse`, and a `DriftResponse` that found no drift records no steps, so the loop strip shows four blocks as not recorded.
- `GET /api/v1/overview` answered in 0.32 s. The existing screenshot script still captured seven panels as "Loading…", because it waits only for the fabric and the lanes (R6).

## R2. Type scale

**Decision**: a 14 px base stepping by 1.25: 12, 14, 17.5, 22, 27, 34. Named theme tokens, not Tailwind's default names.

**Why**: the page has two sizes doing all the work. Named tokens let a test say which sizes exist. 12 px is kept for metadata because the source-and-age footers and table headers need a size below body.

**Set aside**: Tailwind's own `text-sm` to `text-3xl` (1.125 to 1.25 steps, uneven, and nothing stops a new size appearing).

## R3. Enforcing the scale without one huge PR

**Decision**: `src/look.test.ts` scans the UI's `.tsx` files for spacing and type classes. It fails on any outside the scale, except for files in an allowlist inside the test. The first slice adds the test with every file that breaks the rule today on the list. Each later slice removes the files it restyles. The last slice leaves the list empty, and the test then fails if the list is not empty.

**Why**: restyling every component at once would be one unreviewable PR and would collide with every other slice. A shrinking list keeps each PR small and stops new violations at once.

**Set aside**: an ESLint rule (class names are strings in template literals; a plain scan is simpler and as strict), and a one-shot restyle.

## R4. Where the four numbers come from

**Decision**: no new request.

| card | source already on the page |
| --- | --- |
| Devices in sync | `overview.devices` (or the devices at a past time): devices with `in_intent`, and those whose state is `in_sync` |
| Open drifts | the drift timeline's episodes with no `closed_at`, split by the lane's `in_intent` |
| Latest repair | the drift timeline's `latest_closed`: `duration_s`, `name`, `closed_by`, `closed_at` |
| Deploys and repairs, 24 h | the history's `deploys` and `repairs` series, `by_outcome`, summed over the buckets in the last 24 hours |

The comparison line for the first two cards uses the timeline's scan points: the state of each lane at 24 hours before the time shown, held for at most 15 minutes as the lanes rule says. With no scan point in that window the card says there is nothing to compare with.

**Why**: the spec changes no API. The timeline's default range is 24 hours, so the earlier point is at its left edge; when the operator picks a shorter range the comparison line is left out, not guessed.

**Set aside**: a runs-in-24-hours card (no such count in the API; `overview.runs` is capped at 50), and asking Prometheus (the cards must work in an archive and a snapshot with no metrics).

## R5. History as a chart

**Decision**: a Recharts composed chart over `historyRows`: a bar for scans per bucket, a stepped line for the most drifted devices, and small stacked marks for deploys and repairs by outcome using the reserved ok and fail tones with their glyphs in the legend. Quiet buckets draw nothing and the axis keeps their place. The table stays in a `<details>` below, closed by default, and is the chart's text alternative.

**Why**: the same rows, so the parity test's facts are unchanged. A `<details>` element needs no state and works in the snapshot.

**Set aside**: removing the table (FR-021, and the chart's `aria-label` would have nothing to point to).

## R6. Looking at the page without a lab

**Decision**: fix `e2e/screenshot.mjs` to wait until no element with the text "Loading…" remains (with a timeout that fails loudly), and add `e2e/look.mjs` for the look check. Both run against `dev/scripts/serve_dashboard_fixtures.py --sources up`, which serves the real app on captured data with every source answering and a fixed clock.

**Why**: the fixture server reaches no lab, stack or network, so an unattended run may use it. `--sources up` is needed because the default leaves most panels unavailable, which is the wrong page to judge.

**Set aside**: judging the look from tests alone (they can't see it), and using the live dashboard (it belongs to the maintainer during a run).

## R7. The ring

**Decision**: an inline SVG ring: a track in `--line`, an arc in the ok tone with an ink outline, the count as text in the middle, and a sentence for screen readers. With every device in sync the ring is whole; with none scanned it is the dashed "never scanned" ring.

**Why**: the dashboard skill requires that colour never carries state alone. The count in words and the outline do that. Recharts' pie adds animation hooks and weight for one arc.

## R8. The clock

**Decision**: `Clock.tsx` shows the page's `now` in UTC, `HH:MM:SS`, monospace. In live mode at the present it advances once a second from the server's last `generated_at` plus elapsed time, with a CSS-free text update. It stops in archive and snapshot modes and when a past time is shown.

**Why**: the page's rule is that nothing is dated by the browser's clock. The clock starts from the server's time and only counts elapsed seconds. A text change is not motion under spec 011 FR-043, but the seconds are dropped under reduced motion so nothing changes every second.

## R9. Sticky range toolbar

**Decision**: the range picker becomes a `position: sticky` bar below the fabric section, holding the presets, the current range in words, and the custom fields behind a disclosure. It sticks while the lanes, history and metrics are in view.

**Why**: one range governs three sections that no longer fit one screen. Putting the picker in each panel's header would give three controls for one value.

**Set aside**: a fixed bar across the whole page (it would cover the fabric, which the range does not govern).
