---
name: snapl-dashboard
description: The colour, shape, chart, motion and look rules (spacing, type, panels) for snapl's designed dashboard UI (specs 011 and 014). Read before adding or changing anything in packages/presentation/ui.
---

# Dashboard encoding rules

These rules are enforced in code. The one implementation is `packages/presentation/ui/src/lib/encoding.ts`, and `encoding.test.ts` fails when a rule below is broken. Change the code and its test together, then this file.

## Why colour never carries state on its own

The fills are the reserved status colours: green `#0ca30c`, amber `#fab219`, red `#d03b3b`, grey `#898781`. Under red-green colour-vision deficiency, green and red are about 4 apart (OKLab ×100, Machado 2009), against a target of 8. The dataviz palette validator reports the same. So:

- **Every drift state has its own shape, its own glyph and its own label.** Colour only groups states into four kinds: in sync (green), drifted (amber), failed (red), unknown (grey).
- **Every mark has an ink outline.** Amber on a white panel is under 2:1. The outline (≥ 3:1 on both surfaces) is what makes the shape readable there.
- **A drifted link is thicker and dashed**, not just a different colour.
- **Activity is a ring around the device, solid for running and dotted for just ended.** With reduced motion it does not pulse.

## The seven drift states

| state | shape | glyph | kind | label |
| --- | --- | --- | --- | --- |
| `in_sync` | circle | ✓ | ok | In sync |
| `missing` | triangle | − | drift | Missing intent |
| `undesired` | square | + | drift | Undesired config |
| `missing_and_undesired` | diamond | ± | drift | Missing and undesired |
| `drifted` | hexagon | ≠ | drift | Drifted |
| `error` | octagon | ! | fail | Scan error |
| `never_scanned` | dashed ring | · | unknown | Never scanned |
| anything else | dashed pill | ? | unknown | the raw value |

A state a newer server sends is shown by its raw text, never hidden (010 contract "Versioning").

## Contrast (WCAG 2.1 AA)

- Text and muted text: at least 4.5:1 on both panel surfaces (`#ffffff` light, `#1a1d21` dark).
- Shape outlines: at least 3:1 on both surfaces.
- The glyph inside a fill: at least 3:1 against that fill. Dark ink on amber, white on the others.

## Text

- Labels, values and legends use ink tokens, never a state colour.
- Anything recorded from a device (paths, error text) is rendered as text. The ways around React's escaping are an ESLint error, and a unit test fails on any disable comment for that rule.
- The legend lists only the states on screen.

## Layout

- Devices sit in tiers by role: spines, then leaves, then other roles by name. Within a tier, names sort with numbers as numbers (`leaf-2` before `leaf-10`).
- The top tier's names go above their marks, so no link crosses them.
- Above 32 devices the drawing is replaced by the device list, with the reason.

## Lanes and the "as of" time

- A lane draws each scan's state as its mark (shape and glyph) on a stretch in its kind's colour. A state is held until the next scan for at most 15 minutes; after that the lane is hatched as no data, never the last known state.
- Every lane also says what it shows in words, for screen readers.
- Times from the audit trail have microseconds. Compare them through `pointTime` in `lib/lanes.ts`, which rounds up to the millisecond, so the page and the server agree on which scans come before an "as of" time.
- At a past time, the banner says so, and every view whose source keeps no history says it shows only the present. Running workflows and the schedule's repeated failures are left off the drawing then.
- The headline's duration is scan to scan. Never call it time to detect.

## The loop strip

- Six blocks in loop order. A block is done, failed (with its reason), not reached, or not recorded. Presentation is "you are here" and is never a step.
- Nothing is drawn as in progress: the audit trail records steps that ended, not ones that started.
- Each status has its own glyph and word. A block or status the page does not know is shown by its raw text.

## Motion

- Nothing moves when the viewer asks for reduced motion (`prefers-reduced-motion`). The same ring is drawn still.

## Look

These rules are enforced in code too. `packages/presentation/ui/src/look.test.ts` fails on spacing or type outside them, and the tokens are in `src/index.css`.

### Spacing

- **Gaps, padding and margins are multiples of 8 px**: Tailwind steps 2, 4, 6 and 8.
- **2, 4 and 6 px are for the inside of a chip, a badge, a mark or a table cell only.** Add the `chip` or `cell` class to the same class string to say so. The class has no style; it is what the test reads.
- Panel padding is 24 px. Table rows are `py-2`.

### Type

One scale, a 14 px base stepping by 1.25. No other size exists.

| class | size | for |
| --- | --- | --- |
| `text-meta` | 12 px | metadata only: source and age, table headers, axis labels |
| `text-body` | 14 px | everything read as text |
| `text-title` | 17.5 px | panel titles |
| `text-heading` | 22 px | section headings |
| `text-number` | 27 px | a panel's lead value |
| `text-number-lg` | 34 px | the number row |

- Body text has a line height of 1.5, and large numbers 1.15. The tokens carry these, so don't set `leading-*` beside them.
- Running text (sentences, failure reasons, error messages, notes) is kept to 45 to 75 characters a line with `max-w-prose`.
- Tell headings, numbers and metadata apart by size first, weight second.

### Panels

- **One frame**: the title on the left of the header, at most one action on the right, and "source · age" in a muted footer.
- **Light theme**: a panel is lifted by `shadow-panel`, a shadow tinted with the page's colour, its blur twice its offset. **Dark theme**: no shadow; the panel keeps its border.
- The panel surface colours never change: the contrast tests are measured against them. Change the page colour if the page needs warming.
- A group of panels has a small uppercase, letter-spaced label above it.

### Colour

- About 60% neutral page, 30% panel surfaces, 10% accent. The state colours are the accent. Add no other accent that competes with them.
- **Amber covering much of the screen must only ever mean much drift.**

### Motion and assets

- Transitions are CSS only, so the reduced-motion rule stops them. No JavaScript animation library.
- Icons are inline SVG. Nothing is loaded from another origin, and everything must fit the single-file snapshot.

## Before shipping a change that shows state

1. Run `uv run invoke dashboard.test`.
2. Run `uv run invoke dashboard.e2e`.
3. Look at it in light and dark mode: `node packages/presentation/ui/e2e/screenshot.mjs <dashboard url> <out dir>`.
4. Run the look check and read every picture it writes. Tests cannot say whether the page looks right.
   ```bash
   uv run invoke dashboard.build
   uv run python dev/scripts/serve_dashboard_fixtures.py --port 18932 --sources up &
   node packages/presentation/ui/e2e/look.mjs http://127.0.0.1:18932 <out dir>
   ```
   It prints the page's height and how far down the metrics start. Look for text cut off or overlapping, a panel still loading, uneven gaps, anything unreadable in dark, and sideways scroll.
