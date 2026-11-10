---
name: snapl-dashboard
description: The colour, shape, chart and motion rules for snapl's designed dashboard UI (spec 011). Read before adding or changing anything that shows state in packages/presentation/ui.
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

## Before shipping a change that shows state

1. Run `uv run invoke dashboard.test`.
2. Run `uv run invoke dashboard.e2e`.
3. Look at it in light and dark mode: `node packages/presentation/ui/e2e/screenshot.mjs <dashboard url> <out dir>`.
