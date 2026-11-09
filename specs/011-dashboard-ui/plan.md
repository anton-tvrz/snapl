# Implementation Plan: Designed Dashboard UI

**Branch**: `011-dashboard-ui` | **Date**: 2026-11-09 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/011-dashboard-ui/spec.md`

## Summary

Put a designed UI on 010's read API, and make the loop something that can be watched, replayed and handed to someone else.

- **Contract** (Python, `snapl-presentation`):
  - Four new read routes: `/topology` (links from intent, R6), `/drift-timeline` (lanes and episodes, R10), `/runs/steps` (the loop strip, R15) and `/mode` (R13).
  - `at=` on `/devices` and `/runs` (R11).
  - All compatible additions to v1. No ABC changes, and no workflow or audit payload changes.
- **UI** (TypeScript, `packages/presentation/ui/`):
  - React, Vite, Tailwind, shadcn/ui components and Recharts (R1).
  - The topology and the lanes are hand-written SVG (R7).
  - Types are generated from the OpenAPI snapshot (R5).
- **Serving**: explicit GET routes for `/`, `/plain` and `/assets/…`, a Content-Security-Policy on every response, and the plain page as the fallback when the UI is not built (R2 to R4).
- **Offline**:
  - `snapl dashboard --archive <file>` is a server mode that runs the normal projections on the archived audit trail (R13).
  - `snapl dashboard --snapshot <file>` writes one self-contained HTML file with the data inlined (R14).
- **Build and CI**: `invoke dashboard.*` tasks, the built files as wheel artifacts, a path-filtered `ui` job (R16).

Research decisions are in [research.md](research.md), types in [data-model.md](data-model.md), interfaces in [contracts/](contracts/), and live evidence in [quickstart.md](quickstart.md). The toolchain is ADR-0005 Decision 2.

## Technical Context

**Language/Version**: Python 3.12+ for the server. TypeScript 7 on Node 22 for the UI, at build time only.

**Primary Dependencies**:
- **None new in Python.** FastAPI, uvicorn and httpx are 010's. The in-process client the snapshot command uses is httpx's ASGI transport.
- **New, build-time only, in `packages/presentation/ui/`**: the packages and versions in research R1, pinned exactly, with a committed lockfile.

**Storage**: none new. Archive mode reads a tarball into a temporary directory and removes it on exit. The built UI is git-ignored package data.

**Testing**:
- **Python**: pytest, marked `unit`.
  - Projections are tested as pure functions against `audit_demo_arc.sqlite` and hand-built events.
  - Routes go through `TestClient`.
  - Serving is tested with a stub `static/ui/` in a temp directory, and with none.
  - Archive mode is tested against an archive built from fixtures by `tasks/history.py`'s own functions.
- **UI**: Vitest with jsdom and Testing Library, with an 80% line threshold (R16).
- **Browser**: one Playwright spec, Chromium, against `dev/scripts/serve_dashboard_fixtures.py`.
- **Cross-check**: a JSON file of the server's `/devices?at=` answers at every scan boundary, written by a Python test and read by a Vitest test (R14, SC-004).

**Target Platform**: the maintainer's Mac for the server. Current Chromium, Firefox and Safari for the page. The snapshot file opens from disk in the same three.

**Project Type**: additions to `snapl-presentation` (routes, projections, CLI options), a new UI source directory inside it, invoke tasks, and CI.

**Performance Goals**:
- SC-013: each new route answers within 1 s on 100,000 audit events.
- SC-006: the page is usable within 2 s with any one source stopped.
- SC-008: an archive of 100,000 events opens within 10 s.

**Constraints**:
- GET only, and no route answers `HEAD` except the framework's OpenAPI route (010 R2).
- No request leaves the origin, in any mode (FR-005).
- No Node at run time (FR-003).
- The UI never computes a fact the server could state. The two exceptions are drawing logic (layout, lane segments, activity marks, which link a path marks) and the snapshot's `InlineProvider`, which is cross-checked against the server.

**Scale/Scope**: 6 devices and 8 links today, drawn for up to 32. About 25 UI modules. Nine PR slices.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. NAF Alignment | PASS | All of it is Presentation. The UI reads Presentation's own API. Presentation already reads the Source of Truth for names (ADR-0004), and the topology reuses that same read. |
| II. Spec-Driven Development | PASS | A new capability, specified first. Scope settled in #146 on 2026-11-09. |
| III. TDD | PASS, with a wording gap | Each module's test is listed first, in Python and in TypeScript, with the same 80% rule (FR-046). The constitution states Principle III in pytest terms. The amendment that covers UI code is a draft PR for the maintainer (R18). |
| IV. Modularity | PASS | The UI is one directory inside `snapl-presentation`. No other package changes. |
| V. Contract-First | PASS | `contracts/ui-api.md` and `contracts/cli-and-build.md` come before code. The OpenAPI snapshot gains each route in the PR that adds it, and the UI's types are generated from that snapshot. No ABC changes. |
| VI. Single Vendor, Named Seams | PASS | The UI names no vendor. `interfaceOf` reads snapl's own normalised paths (R8). |
| VII. Simplicity | JUSTIFIED | A second language and toolchain. See Complexity Tracking. |
| VIII.2 Drift response | PASS | Read-only. A copy control copies text and runs nothing. |
| VIII.5 Undesired config | PASS | Undesired-only drift shows no command and says removal is not automatic (#65). |
| ADR-0004 (claim only what is true) | PASS | The loop strip marks only recorded steps (R15). A past time labels present-only views (R11). The headline names what it measures (R10). |

**Gate result (pre-research)**: PASS. No `[NEEDS CLARIFICATION]` remain.

## Project Structure

### Documentation (this feature)

```text
specs/011-dashboard-ui/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── ui-api.md
│   └── cli-and-build.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
packages/presentation/snapl_presentation/
├── cli.py                        # --archive, --snapshot, --days, --force
└── dashboard/
    ├── models.py                 # Topology*, ScanPoint, DeviceLane, DriftEpisode, DriftTimeline, RunSteps*, ModeResponse
    ├── projections.py            # project_topology, project_timeline, project_steps, events_until
    ├── app.py                    # the four routes, `at`, /plain, /assets, the CSP middleware
    ├── pages.py                  # NEW: locating the built UI, the fallback notice, safe asset paths
    ├── archive.py                # NEW: open_archive, ArchivedIntent, Archived, ArchivedMetrics
    ├── snapshot.py               # NEW: collect the bundle in-process, escape it, write the file
    └── static/
        ├── index.html            # 010's plain page, unchanged
        └── ui/                   # BUILT, git-ignored: index.html, assets/, snapshot.html

packages/presentation/ui/         # NEW: the UI's source and toolchain
├── package.json, package-lock.json, .nvmrc
├── vite.config.ts, vite.snapshot.config.ts, vitest.config.ts, playwright.config.ts
├── tsconfig.json, eslint.config.js
├── index.html, snapshot.html
├── e2e/smoke.spec.ts
└── src/
    ├── main.tsx, App.tsx, index.css
    ├── api/        schema.d.ts (generated), open.ts
    ├── data/       provider.ts, http.ts, inline.ts, usePolling.ts
    ├── lib/        encoding.ts, layout.ts, paths.ts, activity.ts, lanes.ts, episodes.ts, time.ts, urlState.ts
    └── components/
        ├── ui/                   # copied shadcn components (vendored)
        ├── Shell.tsx, ModeBanner.tsx, SourceStrip.tsx, ViewFrame.tsx, Command.tsx
        ├── topology/             Topology.tsx, Node.tsx, Link.tsx, Legend.tsx, DeviceList.tsx
        ├── DeviceDrawer.tsx
        ├── timeline/             Lanes.tsx, Scrubber.tsx, Headline.tsx
        ├── LoopStrip.tsx
        └── panels/               Schedule.tsx, Runs.tsx, Undesired.tsx, Ci.tsx, LabEvidence.tsx, Links.tsx, Metrics.tsx, RangePicker.tsx

tasks/dashboard.py                # NEW: build, dev, test, types, e2e
tasks/history.py                  # _EXPORTS gains topology, devices, runs, drift-timeline
dev/scripts/serve_dashboard_fixtures.py   # NEW: the app on fixtures, for Playwright
dev/skills/dashboard/SKILL.md     # NEW: encoding, colour, chart and motion rules (R17)

.github/workflows/quality.yml     # ui job, quality gate
.github/workflows/build-artifacts.yml   # build the UI before the presentation wheel
.github/file-filters.yml          # ui filter
.pre-commit-config.yaml           # detect-secrets excludes package-lock.json
.gitignore                        # static/ui/, node_modules/

tests/unit/test_presentation/
├── test_dashboard_topology.py    # NEW
├── test_dashboard_timeline.py    # NEW
├── test_dashboard_steps.py       # NEW
├── test_dashboard_at.py          # NEW
├── test_dashboard_pages.py       # NEW: /, /plain, /assets, CSP, fallback
├── test_dashboard_archive.py     # NEW
├── test_dashboard_snapshot.py    # NEW
├── test_dashboard_app.py         # extended: read-only walk covers the new routes; OpenAPI snapshot
└── fixtures/devices_at.json      # written by test_dashboard_at.py's generator, read by Vitest
tests/unit/test_tasks/test_dashboard_tasks.py   # NEW
tests/unit/test_tasks/test_history.py           # extended
tests/unit/test_infrastructure/test_packaging.py   # NEW: the artifacts line, the gitignore lines
```

**Structure Decision**: the UI's source sits inside the presentation package directory, beside the Python package and not inside it, so the wheel's `packages = ["snapl_presentation"]` never sees `node_modules`. Only its built output lands in the Python package.

## Implementation Order (for /speckit-tasks)

Server work comes first. It needs no Node and no lab, and it gives the UI real answers to render. Every step is red → green → refactor.

1. **Contract, part one** (US1, US2): `IntentDevice`, `/topology`, `at` on `/devices` and `/runs`, the OpenAPI snapshot.
2. **Contract, part two** (US2, US3): `/drift-timeline`, `/runs/steps`, `/mode`.
3. **UI foundation** (US1): the toolchain, the invoke tasks, generated types, the providers and polling, serving and the fallback, the CSP, packaging, the CI job, and a shell that shows the mode and the source strip.
4. **Topology** (US1): encoding and its written rules, layout, the drawing, activity, the device drawer, the degraded list, URL state, and the Playwright check.
5. **The story** (US2, US3): lanes, the "as of" control, the headline, the loop strip.
6. **Panels** (US4): schedule, runs, undesired, CI, lab evidence, links, metrics with the range picker.
7. **Archive mode** (US5): the archive sources, the CLI option, the export additions, the banner.
8. **Snapshot** (US6): the single-file template, `InlineProvider`, the cross-check, the CLI option.
9. **Docs and governance**: README, AGENTS.md, CLAUDE.md, the demo scenario, the changelog, ADR-0005, and the constitution amendment as a draft.

## Post-Design Constitution Re-Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. NAF Alignment | PASS | The step-to-block table (R15) names the other blocks but imports nothing from them. It maps activity names the Orchestrator already records. |
| III. TDD | PASS | One assumption is flagged, not hidden: the failed-deploy shape is hand-built until captured in the lab (R15). |
| V. Contract-First | PASS | Two contracts. The UI cannot read a field the snapshot does not have, because its types come from it. |
| VII. Simplicity | JUSTIFIED | See below. Two things were removed in design: the graph-canvas library (R7), and a browser-side archive reader (R13). |

## Complexity Tracking

| Addition | Why needed | Simpler alternative rejected because |
|-----------|------------|-------------------------------------|
| A Node toolchain | The designed UI is the feature (#146). ADR-0005 bounds it to one directory and to build time. | Growing 010's single file of plain JavaScript: no types from the contract, no component tests, and the topology, lanes and scrubber would triple it. |
| Recharts | Axes, tooltips and a free range for six metrics views (R1) | Hand-drawn lines, as on the plain page, have none of those. |
| Explicit asset route, not `StaticFiles` | 010's read-only test must be able to see every route and method (R3) | A mount answers `HEAD` and is invisible to the route walk. |
| Two CSP variants | The plain page is one file with inline script (R4) | Rewriting the plain page to external files changes 010's "no build step, one file" page for no gain. |
| `InlineProvider` re-implements "state at a time" | A file opened from disk has no server (R14) | Pre-computing every past state in the bundle multiplies its size by the number of scans. The cross-check test keeps the two in step. |
| Archive mode as a server mode | Full-history ranges and time travel from the archived trail (R13) | Serving only the exported JSON stops at 7 days and loses the scrubber. |
| A second build output for the snapshot | One file with everything inlined (R14) | The normal build's separate assets cannot load from `file://` under a strict policy. |
