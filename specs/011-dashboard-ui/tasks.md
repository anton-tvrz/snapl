# Tasks: Designed Dashboard UI

**Input**: Design documents from `/specs/011-dashboard-ui/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ui-api.md, contracts/cli-and-build.md, quickstart.md

**Tests**: TDD is mandatory per CLAUDE.md and the constitution, for Python and for the UI alike (spec FR-046). Every phase lists its tests first, and each test is run and seen to fail before the code it covers is written.

**Organization**: Tasks are grouped by user story (US1 to US6). US1 is P1, US2 and US3 are P2, US4 is P3, US5 and US6 are P4. Server work comes first, because it needs no Node and gives the UI real answers to render.

**PR slicing**:
- **PR A**: Phase 1 (contract, part one: topology and `at`)
- **PR B**: Phase 2 (contract, part two: timeline, steps, mode)
- **PR C**: Phase 3 (UI foundation: toolchain, serving, CI)
- **PR D**: Phase 4 (topology)
- **PR E**: Phase 5 (the story: lanes, "as of", headline, loop strip). Split in two at US3 to keep each under the size limit: **E1** is US2 (T047 to T049, T051's replay steps, T052 to T054), **E2** is the loop strip (T050, T055, T051's deploy step).
- **PR F**: Phase 6 (panels). Split by data source: **F1** Temporal and audit panels (T058, T059 undesired, T063, T064 undesired), **F2** CI, lab evidence and links, **F3** metrics and the range picker, **F4** parity, layout, T062 and T067. F4 also adds a history panel: 010's per-hour history table is on the plain page and FR-029 needs every plain-page fact on the designed page, but no task named it.
- **PR G**: Phase 7 (archive mode)
- **PR H**: Phase 8 (snapshot)
- **PR I**: Phase 9 (docs and governance)

The Python slices (A, B, G) keep to the usual size (< 400 lines of non-test code). The UI slices are larger, because a component and its test land together. When one passes about 800 lines of non-test, non-generated code, split it at a component boundary and say so in the PR.

Each PR says `Closes` a task issue under #146 and carries `changelog/146.added.md` (or appends to it).

**Lab rule for unattended runs**: a task marked **Needs the lab** runs the Compose stack, the maintainer's worker or a device. A task marked **Needs the maintainer** needs a person's judgement or a governance decision. Leave both unticked and defer them (dev/guides/overnight-runs.md). Tests may start their own servers on free ports, a headless browser, and `npm ci` from the lockfile. That is not the lab.

**New major versions**: research R1 lists tool versions looked up on 2026-11-09. Before configuring Vite, Vitest, Tailwind, TypeScript, ESLint or Playwright, read that tool's current documentation for the pinned version. Do not rely on configuration remembered from older majors.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 to US6 from spec.md
- Exact file paths in every description

---

## Phase 1: Contract, Part One (US1, US2) 🎯 MVP server

**Goal**: the API can describe the fabric's links and any past state.

### Tests (write first, verify they fail)

- [x] T001 [P] [US1] tests/unit/test_presentation/test_dashboard_topology.py, for `project_topology(states)`:
  - dcfabric's seed (`packages/intent/snapl_intent/seed/dcfabric/topology.yml`, loaded into `DesiredState`s by a fixture) gives 6 nodes and 8 links, every link `declared_by: both` and `agreed: true`, and no `unlinked`.
  - Nodes sort spines first, then leaves, then other roles alphabetically, and by name with numbers compared as numbers.
  - A link's ends are ordered by (device, interface), and a link declared from both ends appears once.
  - A link declared at one end only has `declared_by` naming that end and `agreed: false`.
  - Two ends that name different interfaces of each other give `agreed: false`.
  - A peer that is not a device of the use case, and an interface naming its own device, go in `unlinked`.
  - Use `test_edge`'s seed for a second, two-device case.
- [x] T002 [P] [US1] Extend tests/unit/test_presentation/test_dashboard_app.py:
  - `GET /api/v1/topology` returns `TopologyResponse` from a fake SoT source.
  - With the SoT unavailable or foreign it returns `available: false` with the reason and remedy and `data: null`.
  - A wrong `use_case` is `422`.
  - The read-only route walk finds the new route, and the OpenAPI snapshot includes it.
- [x] T003 [P] [US2] tests/unit/test_presentation/test_dashboard_at.py:
  - `events_until(events, at)` returns the events with `timestamp <= at`, including when several share a timestamp. *As built*: it is a filter, not a bisect, because rows arrive out of time order (010 research R3).
  - Against `audit_demo_arc.sqlite`: for an `at` just after each of the three scans, `/api/v1/devices?at=` gives the states the fixtures README records (spine-01 `missing`, `missing`, `in_sync`; leaf-01 `undesired` all three). Before the first scan, every device is `never_scanned`.
  - `/api/v1/runs?at=`: a run that started before `at` and ended after it is `running`. One with no end in the trail is `unfinished`. The `running` view is unavailable with the reason in contracts/ui-api.md.
  - A naive `at` and an `at` after the server's clock are `422`.
  - Without `at`, both routes answer exactly as before (compare with the existing tests' expectations).
- [x] T004 [P] [US5] Extend the existing device and run projection tests: `compose_devices` and the name lookup give the same answers when fed `IntentDevice` records as they did with `DesiredState` (research R6).

### Implementation

- [x] T005 [US1] packages/presentation/snapl_presentation/dashboard/models.py: `TopologyNode`, `LinkEnd`, `TopologyLink`, `UnlinkedInterface`, `Topology`, `TopologyResponse` (data-model.md), with the two validators under "Rules".
- [x] T006 [US1] packages/presentation/snapl_presentation/dashboard/projections.py: `project_topology`, and the role-tier and natural-name sort as one helper the timeline reuses.
- [x] T007 [US5] packages/presentation/snapl_presentation/dashboard/sources.py and app.py: an internal `IntentDevice` record, with `compose_devices` and `_device_names` reading it. The live path maps `DesiredState` to it. No behaviour change. *As built*: `IntentDevice` and `intent_devices()` live in projections.py, and `project_devices` takes either kind of record, so sources.py did not change.
- [x] T008 [US1] packages/presentation/snapl_presentation/dashboard/app.py: `GET /api/v1/topology`.
- [x] T009 [US2] packages/presentation/snapl_presentation/dashboard/projections.py and app.py: `events_until`, and `at` on `/devices` and `/runs` (contracts/ui-api.md).
- [x] T010 [US1] Update tests/unit/test_presentation/fixtures/openapi_v1.json through the snapshot test's own update path, and add `changelog/146.added.md`.

**Checkpoint**: `curl 'localhost:18030/api/v1/topology'` and `…/devices?at=…` answer on fixtures. No UI yet.

---

## Phase 2: Contract, Part Two (US2, US3)

**Goal**: the API can tell the story: lanes, episodes, and what each block did.

### Tests (write first, verify they fail)

- [x] T011 [P] [US2] tests/unit/test_presentation/test_dashboard_timeline.py, for `project_timeline(events, names, start, end)`:
  - Against `audit_demo_arc.sqlite`: six lanes of three points each. spine-01 has one episode, opened at scan 1, closed at scan 3, `scans: 3`, `closed_by.kind: reconcile` naming the reconcile's child deploy. leaf-01 has one open episode with `closed_at`, `duration_s` and `closed_by` all null. `latest_closed` is spine-01's and `open_count` is 1.
  - Hand-built events for each rule in data-model.md: repaired then drifted again gives two episodes; an error scan inside an episode is counted and leaves it open; an error scan with no episode opens none; a clean scan with no deploy in between gives `closed_by: null`; a deploy that failed is not a closer; an operator's direct deploy gives `kind: deploy`; a scheduled one gives `kind: remediation`.
  - A deploy that ended before the last drifted scan is not the closer.
  - An episode that opened before `start` and overlaps the range is included, and `latest_closed` can be one that closed before `start`.
  - A device no longer in intent keeps its lane with `in_intent: false`.
  - A scan recorded before FR-012a (no `undesired_paths`) gives a `drifted` point with `unclassified_paths`.
  - More than 20,000 points raises the error the route turns into `422`.
- [x] T012 [P] [US3] tests/unit/test_presentation/test_dashboard_steps.py, for `project_steps(run, run_events)`:
  - A fixture deploy: `recorded: true`, four steps in order with their blocks and times, and blocks `done, done, done, done, done, surface`.
  - Hand-built failed deploys, one per failure reason (`intent_unavailable`, `device_not_found`, `apply_failed`, `collect_failed`, `verification_failed`): the mapped block is `failed` with the reason, earlier blocks are `done`, later ones `not_reached`. Mark the test module's docstring: these shapes are read from `deploy_intent.py`, not captured (research R15).
  - A deploy in progress with two step records: two blocks `done`, two `not_reached`, none in progress.
  - A `ScanDrift` and a `ReconcileDevices` run: `recorded: false`, the four blocks `not_recorded`, Orchestrator `done`.
  - An unknown activity name is returned as a step with `block: null`.
  - `STEP_BLOCKS` covers exactly the activity names `deploy_intent.py` records. Assert against names collected from the fixture, so a new recorded step fails the test.
- [x] T013 [P] [US2] Extend tests/unit/test_presentation/test_dashboard_app.py:
  - `GET /api/v1/drift-timeline` with defaults, with `start >= end` (`422`), and with too many points (`422`, message as in contracts/ui-api.md).
  - `GET /api/v1/runs/steps` for a known run, for an unknown one (`200`, `data: null`), and with a naive `started_at` (`422`).
  - `GET /api/v1/mode` returns `live`, the injected clock's time and no archive.
  - The read-only walk and the OpenAPI snapshot include all three.
  - With 100,000 generated events, each new route answers within 1 s (SC-013). Mark it `slow`, as 010's SC-007 test is. *As built*: measured on the projections in tests/unit/test_presentation/test_dashboard_performance.py, where 010's SC-007 tests already are.

### Implementation

- [x] T014 [US2] models.py: `ScanPoint`, `DeviceLane`, `EpisodeClose`, `DriftEpisode`, `DriftTimeline`, `DriftTimelineResponse`, with their validators.
- [x] T015 [US2] projections.py: `project_timeline`, reusing 010's per-device scan classification and trigger rules without copying them.
- [x] T016 [US3] models.py: `NafBlock`, `BlockStatus`, `RunStep`, `BlockState`, `RunSteps`, `RunStepsResponse`. projections.py: `STEP_BLOCKS`, `run_events`, `project_steps`. *As built*: no `FAILURE_BLOCKS` table. The failed block is the first with no record (research R15, amended).
- [x] T017 [US5] models.py: `ArchivePart`, `ArchiveInfo`, `ModeResponse`. app.py: `create_app` takes an optional `archive: ArchiveInfo`, and serves `GET /api/v1/mode`.
- [x] T018 [US2] app.py: `GET /api/v1/drift-timeline` and `GET /api/v1/runs/steps`.
- [x] T019 [US2] Amend specs/010-dashboard-read-api/contracts/dashboard-api.md "Versioning": `NafBlock` and `BlockStatus` join the open enums, and the 011 routes are listed with a link to this spec's contract. Update the OpenAPI snapshot.

**Checkpoint**: every fact the UI will show is available from the API on fixtures.

---

## Phase 3: UI Foundation (US1)

**Goal**: a built UI is served safely, falls back to the plain page, ships in the wheel and is checked in CI. It shows only a shell.

### Tests (write first, verify they fail)

- [x] T020 [P] [US1] tests/unit/test_presentation/test_dashboard_pages.py (Python), with the UI directory injected so no build is needed:
  - With a stub `ui/index.html` and `ui/assets/app-abc123.js` in a temp directory: `/` serves the stub, `/plain` serves 010's page, and the asset is served with the immutable cache header.
  - With no UI directory: `/` serves the plain page containing the notice and `uv run invoke dashboard.build`.
  - `/assets/../index.html`, an encoded traversal, an absolute path and a missing file are each `404`.
  - `HEAD` on `/`, `/plain` and an asset is `405`.
  - Every response carries the policy of contracts/ui-api.md: the strict one on `/` (built), an asset and an API route, and the plain variant on `/plain` and on `/` when it falls back.
  - The read-only walk finds `/plain` and `/assets/{path}` and no route that answers anything but `GET`.
- [x] T021 [P] [US1] tests/unit/test_infrastructure/test_packaging.py:
  - `packages/presentation/pyproject.toml` lists `snapl_presentation/dashboard/static/ui/**` as artifacts for the wheel and the sdist.
  - `.gitignore` ignores the built directory and `node_modules`.
  - `.pre-commit-config.yaml`'s detect-secrets hook excludes `package-lock.json`.
  - `.github/file-filters.yml` has a `ui` filter with the four paths in contracts/cli-and-build.md, and `quality.yml`'s gate lists the `ui` job.
- [x] T022 [P] [US1] tests/unit/test_tasks/test_dashboard_tasks.py, with a fake `ctx`:
  - `build` runs `npm ci` then the build in the UI directory, and fails with the contract's message when `node` is missing or older than 22.
  - `test`, `types`, `e2e` and `dev` run the commands in contracts/cli-and-build.md.
- [x] T023 [P] [US1] packages/presentation/ui/src/api/open.test.ts: the lookup helper returns the known entry for each known value, and a neutral entry labelled with the raw text for an unknown value, for each of the open enums (spec FR-008).
- [x] T024 [P] [US1] packages/presentation/ui/src/data/http.test.ts and usePolling.test.ts:
  - `HttpProvider` requests the paths and query strings of the contract for each method (a stubbed `fetch`), sends times with an offset, and turns a non-200 into a rejected promise carrying the status and detail.
  - `usePolling` calls at its interval, stops while `document.hidden`, and calls at once on `visibilitychange` back to visible (fake timers).
- [x] T025 [P] [US1] packages/presentation/ui/src/components/ViewFrame.test.tsx, SourceStrip.test.tsx and Command.test.tsx: *As built*: also `Shell.test.tsx`, `App.test.tsx`, `src/lib/time.test.ts` and `src/lib/theme.test.ts`. `ageText` came forward from T047 because `ViewFrame` needs it.
  - `ViewFrame` shows its children for an available view with its source and age. For an unavailable one it shows the reason and remedy. For a stale one it shows the data labelled with its age.
  - `SourceStrip` shows one entry per source, by raw name for one it does not know.
  - `Command` shows the full command as text and copies it on activation (a stubbed clipboard). It has no other effect.
- [x] T026 [P] [US1] packages/presentation/ui/src/noDanger.test.ts: no file under `src/` contains `dangerouslySetInnerHTML` or an ESLint disable comment for `react/no-danger`.

### Implementation

- [x] T027 [US1] packages/presentation/ui/: `package.json` with the pinned versions of research R1, `package-lock.json`, `.nvmrc`, `tsconfig.json`, `vite.config.ts` (output to `../snapl_presentation/dashboard/static/ui`, dev proxy for `/api`), `vitest.config.ts` (jsdom, coverage threshold 80% lines, excluding `src/api/schema.d.ts` and `src/components/ui/`), `eslint.config.js` (`react/no-danger` as an error), `index.html`, `src/main.tsx`, `src/index.css` with the Tailwind entry and the bundled font. *As built*: TypeScript 5.9.3 and no `eslint-plugin-react` (research R1, amended). `vitest.config.ts` also excludes `src/main.tsx` and the test support files from coverage.
- [x] T028 [US1] packages/presentation/ui/src/api/schema.d.ts, generated from tests/unit/test_presentation/fixtures/openapi_v1.json, with `types` and `types:check` scripts in `package.json`. `src/api/open.ts`.
- [x] T029 [US1] packages/presentation/ui/src/data/provider.ts (`DataProvider`, data-model.md), `http.ts`, `usePolling.ts`.
- [x] T030 [US1] packages/presentation/ui/src/components/ui/ (the copied shadcn components in research R1), `ViewFrame.tsx`, `SourceStrip.tsx`, `Command.tsx`, `Shell.tsx`, `App.tsx`. The shell shows the use case, the mode and the source strip. Light and dark follow the system setting, with a toggle remembered in `localStorage` inside try/catch. *As built*: no shadcn components were copied, because the shell uses none (research R1, amended). The theme toggle is a select.
- [x] T031 [US1] packages/presentation/snapl_presentation/dashboard/pages.py: locate the built UI, build the fallback page with its notice, and resolve an asset path safely. app.py: `/`, `/plain`, `/assets/{path}` and the policy middleware. cli.py: the extra start line when the UI is not built.
- [x] T032 [US1] tasks/dashboard.py with `build`, `dev`, `test`, `types` and `e2e`, registered in tasks/__init__.py. packages/presentation/pyproject.toml artifacts. `.gitignore`. `.pre-commit-config.yaml`.
- [x] T033 [US1] `.github/file-filters.yml` `ui` filter. `.github/workflows/quality.yml`: the `ui` job (setup-node from `.nvmrc`, `npm ci`, `tsc --noEmit`, ESLint, `types:check`, Vitest with coverage, build) and the gate. `.github/workflows/build-artifacts.yml`: build the UI before the presentation wheel and check the wheel holds `static/ui/index.html`. *As built*: the job also runs the browser check, brought forward from T041 with `@playwright/test`, `packages/presentation/ui/e2e/smoke.spec.ts` and dev/scripts/serve_dashboard_fixtures.py.
- [x] T034 [US1] Run `uv run invoke dashboard.build` and `uv run invoke dashboard.test` locally, and `uv build --package snapl-presentation` then `unzip -l` for `static/ui/index.html` (quickstart item 9). Paste the results into the PR.

**Checkpoint**: `snapl dashboard` serves a built shell at `/`, the plain page at `/plain`, and the plain page at `/` on a machine without the build.

---

## Phase 4: Topology (US1) 🎯 MVP

**Goal**: the fabric is drawn, with state, activity, marked links and the device drawer.

### Tests (write first, verify they fail)

- [x] T035 [P] [US1] packages/presentation/ui/src/lib/encoding.test.ts (research R17, SC-010): *As built*: a `color.ts` module (WCAG contrast and OKLab distance under simulated deficiency) is tested on its own. The colour-vision check is stated as: every state differs from every other by shape and glyph, because the status green and red are about 4 apart under red-green deficiency (dev/skills/dashboard).
  - Each of the seven drift states has a colour token, a shape, an icon and a label, and no two states share both shape and icon.
  - Each state's mark colour has at least 3:1 contrast, and its text 4.5:1, against the light and the dark background.
  - Under simulated protanopia, deuteranopia and tritanopia, and in greyscale, any two states with the same shape differ by at least the colour distance set in the module.
  - An unknown state gets the neutral encoding, with its raw text as the label.
  - The same checks for the run-state and source-status tables.
- [x] T036 [P] [US1] packages/presentation/ui/src/lib/layout.test.ts, for `layoutTiers(nodes, links, size)`:
  - dcfabric: two tiers, spines above leaves, evenly spaced, in name order, with eight link paths between the right nodes.
  - The same input gives the same output, and a shuffled input gives the same output.
  - A role it does not know gets its own tier after the known ones. No two nodes share a position.
  - 33 nodes returns the "too many" result, with the count and the limit.
  - One node, and nodes with no links, are placed without error.
- [x] T037 [P] [US1] packages/presentation/ui/src/lib/paths.test.ts and activity.test.ts:
  - `interfaceOf` on the fixture's two paths, on a path with no interface segment, and on an interface name containing `]`-free special characters such as `ethernet-1/1` and `lag1.0`.
  - `markedLinks(devices, links)`: spine-01's `ethernet-1/1` marks its link to leaf-01, and leaf-01's `ethernet-1/7` marks no link (research R8).
  - `activityFor(overview)`: a running deploy marks its device `active`. A run that ended 9 s before `generated_at` marks it `recent` with the outcome, and one that ended 11 s before does not. A fabric-wide run marks the fabric and no device.
- [x] T038 [P] [US1] packages/presentation/ui/src/lib/urlState.test.ts: parse and write `device`, `at`, `from`, `to` and `run`; ignore a malformed value without throwing; and write with `replaceState`, never `pushState`.
- [x] T039 [P] [US1] packages/presentation/ui/src/components/topology/Topology.test.tsx, on fixture responses saved from the Phase 1 routes: *As built*: the component tests render API responses captured from the real app on the demo arc (`src/test/fixtures/`), pinned by `tests/unit/test_presentation/test_ui_fixtures.py`.
  - Six device buttons and eight links, each device labelled with its name, role and state label.
  - The marked link carries its mark, and the legend lists only the states shown.
  - Activity renders a pulse class, and a static ring when reduced motion is requested (a stubbed `matchMedia`).
  - A device flagged for repeated failed repairs carries that mark.
  - With the topology view unavailable, the device list is shown with the reason and remedy. With 33 devices, the list is shown with the "too many" reason.
  - Devices are reachable in tier order with Tab, and Enter selects.
- [x] T040 [P] [US1] packages/presentation/ui/src/components/DeviceDrawer.test.tsx:
  - A `missing` device lists its paths under "missing", and shows `snapl reconcile <name>` with the copy control.
  - An `undesired` device lists them under "undesired", shows the note that removal is not automatic, and shows no command.
  - An `error` device shows its error as text. A string such as `<img src=x onerror=alert(1)>` in an error or a path is rendered as text (assert no `img` element exists).
  - Last scan, last deploy outcome and reason, recent runs for the device, and any link disagreement from the topology are shown.
  - Escape closes it, and focus returns to the device's button.
- [x] T041 [US1] packages/presentation/ui/e2e/smoke.spec.ts and dev/scripts/serve_dashboard_fixtures.py (research R16):
  - *Partly done in PR C*: the fixture server, the Playwright config, the CI step and a first spec (the policy, off-origin requests, the theme, `/plain`) exist. This task adds the topology assertions.
  - The script serves the real app on a free port: audit from `audit_demo_arc.sqlite`, intent from dcfabric's seed, the other sources unavailable, the clock fixed at the fixture's end.
  - The spec checks six devices in two tiers and eight links, leaf-01 labelled undesired, selecting leaf-01 opens the drawer with its path, and reloading the copied address reopens it.
  - It records every request and fails on any whose origin is not the server's (SC-007).
  - Add a `ui-e2e` step to the `ui` job: install Chromium only, run the spec, upload the trace on failure.

### Implementation

- [x] T042 [US1] packages/presentation/ui/src/lib/encoding.ts, and dev/skills/dashboard/SKILL.md recording the rules of research R17 and pointing at the module.
- [x] T043 [US1] packages/presentation/ui/src/lib/layout.ts, paths.ts, activity.ts, urlState.ts.
- [x] T044 [US1] packages/presentation/ui/src/components/topology/Topology.tsx, Node.tsx, Link.tsx, Legend.tsx, DeviceList.tsx.
- [x] T045 [US1] packages/presentation/ui/src/components/DeviceDrawer.tsx, and App.tsx wiring: the overview every 5 s, the topology every 30 s, selection in the URL. *As built*: also `src/lib/linkNotes.ts` (link disagreements in words, for the drawer). The page reads device states from `/overview`, not `/devices`, to save a request. Its test and code were written in the same step and not seen failing first.
- [ ] T046 [US1] **Needs the lab**: quickstart item 1 (the demo arc on the topology, SC-001 and SC-002) and item 5 (a source down, SC-006).

**Checkpoint**: the designed page replaces the device table. US1 is complete and demoable live.

---

## Phase 5: The Story (US2, US3)

**Goal**: drift can be replayed, and the loop is shown as a loop.

### Tests (write first, verify they fail)

- [x] T047 [P] [US2] packages/presentation/ui/src/lib/lanes.test.ts and time.test.ts:
  - `segments(points, range, now)`: a state is drawn from its scan to the next scan; a gap longer than 15 minutes draws 15 minutes of the state and then no data; the last point is held to `now` for at most 15 minutes; an error point draws an error segment.
  - `stateAt(points, t)`: the last point at or before `t`, `never_scanned` before the first, and no data when the last point is more than 15 minutes before `t`.
  - Time helpers: ages relative to a given now (never the wall clock directly), UTC and local labels, and a duration in words ("47 s", "3 min 12 s").
- [x] T048 [P] [US2] packages/presentation/ui/src/components/timeline/Lanes.test.tsx, Scrubber.test.tsx and Headline.test.tsx, on the fixture's timeline response:
  - Six lanes in tier order, spine-01 showing drifted then in sync, leaf-01 undesired throughout, and the reconcile marker between scans 2 and 3.
  - The scrubber moves with the pointer and with the arrow keys (one scan per press, Home and End for the range's ends), reports its value as text for assistive technology, and writes `at` to the URL.
  - "Return to now" clears `at`.
  - The headline shows spine-01's duration and "repaired by an operator's reconcile", never the words "detect" or "detection". With `latest_closed: null` it says no drift has been repaired yet. It shows the open count.
  - An episode with `closed_by: null` reads "repaired outside snapl, or intent changed (unknown)".
  - A `422` for too long a range shows the server's message and keeps the previous lanes.
- [x] T049 [P] [US2] packages/presentation/ui/src/App.past.test.tsx:
  - With `at` set, the provider is asked for `devices(at)` and `runs({at})`, the topology shows that state, and a banner states the past time with the return control.
  - The schedule, worker, CI and running views show "shows only the present", and the topology says its devices and links are today's.
  - *PR E1*: the schedule, worker, CI and running panels arrive in Phase 6, so E1 checks the sources strip and the topology note. Phase 6's panel tests carry the rest (T058 below).
  - The past views are not polled every 5 s. They refetch when `at` changes and once a minute.
- [x] T050 [P] [US3] packages/presentation/ui/src/components/LoopStrip.test.tsx:
  - Six blocks in loop order. For a successful fixture deploy, four `done` with times, Orchestrator `done`, Presentation as "you are here".
  - For a failed apply: Executor `failed` with its reason as text, Collector and Observability `not reached`.
  - For `recorded: false`: the strip says this run type's steps are not recorded one by one, and shows the outcome.
  - No block is ever rendered as in progress.
  - An unknown block or status from the server is shown by its raw text.
  - Each status differs by icon as well as colour.
- [x] T051 [US2] Extend packages/presentation/ui/e2e/smoke.spec.ts: move the scrubber to between scans 1 and 3 and check spine-01 is drawn as missing with the past-time banner; return to now and check it is in sync; select the reconcile's deploy and check four blocks done.
  - *PR E1*: the replay and return steps. *PR E2*: the deploy step, chosen from spine-01's drawer.

### Implementation

- [x] T052 [US2] packages/presentation/ui/src/lib/lanes.ts, time.ts, episodes.ts (headline wording from an episode).
- [x] T053 [US2] packages/presentation/ui/src/components/timeline/Lanes.tsx, Scrubber.tsx, Headline.tsx.
- [x] T054 [US2] App.tsx: the "as of" state, the past-time banner, present-only labels through `ViewFrame`, and the refetch rules of research R11.
- [x] T055 [US3] packages/presentation/ui/src/components/LoopStrip.tsx, shown for the latest run and for a run selected in the drawer or the lanes' markers.
- [ ] T056 [US2] **Needs the lab**: quickstart items 2 and 3 (replay and the headline against `snapl audit`).
- [ ] T057 [US3] **Needs the lab**: quickstart item 4. Capture a failed deploy, replace the hand-built failed-deploy events in tests/unit/test_presentation/test_dashboard_steps.py with the captured rows, and update tests/unit/test_presentation/fixtures/README.md.

**Checkpoint**: the demo arc can be replayed on the page. US1 to US3 are enough for the demo.

---

## Phase 6: Panels (US4)

**Goal**: every fact on the plain page is on the designed page.

### Tests (write first, verify they fail)

- [x] T058 [P] [US4] packages/presentation/ui/src/components/panels/Schedule.test.tsx and Runs.test.tsx, on 010's fixtures:
  - Schedule: mode, interval, last and next run; paused with reason and actor and no next run; no schedule with the create command; repeated failed repairs with the device, the count and the report-mode command; worker pollers, and "no worker" when zero.
  - Runs: type, target, start, duration, outcome, reason, trigger, actor when recorded, and the Temporal link opening in a new tab with `rel="noreferrer"`. Running runs first with elapsed time. A device filter from the URL. An unknown run state by raw text.
  - *Moved from T049 (PR E1)*: with `at` set, the schedule, worker, CI and running panels say they show only the present (`ViewFrame presentOnly`), and the runs panel shows `runs({at})`.
- [x] T059 [P] [US4] packages/presentation/ui/src/components/panels/Undesired.test.tsx, Ci.test.tsx, LabEvidence.test.tsx and Links.test.tsx:
  - Undesired: every undesired path across the fabric grouped by device, the note that removal is not automatic (#65), and no command. Empty state when there is none. *Done in PR F1.*
  - CI: `main` first, each PR's gate and jobs with links, the failed job named, coverage or "none reported", `omitted_prs` with the token remedy.
  - Lab evidence: latest run per suite with result and age, "N commits on main since", how it was matched, "unknown" with its reason, incomplete and unreadable runs.
  - Links: grouped, with the reason shown when some are left out.
- [ ] T060 [P] [US4] packages/presentation/ui/src/components/panels/Metrics.test.tsx and RangePicker.test.tsx, on 012's fixtures:
  - One panel per view, with its unit, the latest value as text, and the step the server used.
  - `worker-up` and `source-health` as up/down strips, the others as lines, with gaps where a value is null.
  - An unavailable view shows its reason and remedy in place of the chart.
  - The picker offers 1 h, 24 h, 7 d and a custom start and end, writes `from` and `to` to the URL, and rejects an end before its start without a request.
  - A `422` from the server shows its message.
  - Each chart has a text alternative: a table of the latest value per series.
- [ ] T061 [P] [US4] packages/presentation/ui/src/parity.test.ts: for each fact rendered by 010's plain page (list them from `static/index.html`'s render functions in the test's table), name the designed component that shows it. The test fails when a row has no component. This is SC-011's checklist as a test.
- [ ] T062 [US4] Extend packages/presentation/ui/e2e/smoke.spec.ts: the runs panel lists the fixture's 12 runs, and the unavailable sources' panels each show a reason and a remedy.

### Implementation

- [x] T063 [P] [US4] packages/presentation/ui/src/components/panels/Schedule.tsx and Runs.tsx.
- [x] T064 [P] [US4] packages/presentation/ui/src/components/panels/Undesired.tsx, Ci.tsx, LabEvidence.tsx, Links.tsx.
  - *PR F1*: Undesired.tsx. *PR F2*: Ci.tsx, LabEvidence.tsx, Links.tsx.
- [ ] T065 [US4] packages/presentation/ui/src/components/panels/Metrics.tsx and RangePicker.tsx, with Recharts, polled every 30 s.
- [ ] T066 [US4] App.tsx and Shell.tsx: the page layout for 1280 pixels and wider, stacking down to 1024 with no page-level horizontal scroll (a Playwright viewport assertion at both widths).
- [ ] T067 [US4] Extend tests/unit/test_presentation/test_dashboard_app.py: for the same fixtures, the designed page's sources (`/overview`, `/devices`) and `snapl status --json` agree on every device's state and the schedule (SC-011, extending 010's SC-002 test).
- [ ] T068 [US4] **Needs the maintainer**: quickstart item 11 (the three questions, SC-014).

**Checkpoint**: an operator needs only the designed page.

---

## Phase 7: Archive Mode (US5)

**Goal**: an archive opens with nothing running.

### Tests (write first, verify they fail)

- [ ] T069 [P] [US5] tests/unit/test_presentation/test_dashboard_archive.py, on an archive built in a temp directory by tasks/history.py's own packing functions from the fixtures:
  - `open_archive(path)` returns the manifest and an extraction directory, and removes the directory on close, also when the body raises.
  - Refusals, each with the contract's message: a missing file, a file that is not a tarball, a tarball with no manifest, and a manifest with another format.
  - A member with `..` or an absolute name is not written outside the directory.
  - The archive file's checksum is the same before and after.
  - `ArchivedIntent` gives `IntentDevice`s and the topology from `export/topology.json`. Without that file, the device view is the degraded list and the topology is unavailable with the "predates" reason.
  - `Archived` returns the schedule, worker and CI views from `export/overview.json` with their original `fetched_at`. With the file missing, each is unavailable with "not in this archive" and the remedy `uv run snapl dashboard`.
  - `ArchivedMetrics` answers the exported range and refuses another with the exported range in its reason.
  - Lab runs come from the archived `test-runs/`, with commits-since null and the reason.
- [ ] T070 [P] [US5] Extend tests/unit/test_presentation/test_dashboard_archive.py for the app in archive mode:
  - `/mode` is `archive` with the manifest's fields, and `now` is the manifest's `created_at`.
  - `/devices`, `/drift-timeline`, `/runs` and `/runs/steps` answer from the archived trail, and default ranges end at the archive's time.
  - With Temporal, SoT, GitHub, Codecov and Prometheus clients each replaced by one that fails on use, every route still answers (contracts/cli-and-build.md "Network").
  - An archive with a missing part opens, and the views that need it say so.
  - An archive of 100,000 events is ready within 10 s (SC-008), marked `slow`.
- [ ] T071 [P] [US5] Extend tests/unit/test_presentation/test_cli.py (or the file that holds the dashboard command's tests):
  - `--archive` prints the contract's start line and missing-part lines, takes the use case from the manifest, and errors on a conflicting `--use-case`.
  - Each refusal exits `ExitCode.ERROR` before the port check.
  - Connection settings and tokens in the environment are not read.
- [ ] T072 [P] [US5] Extend tests/unit/test_tasks/test_history.py: the export holds `topology.json`, `devices.json`, `runs.json` and `drift-timeline.json`, the manifest format is unchanged, and a dashboard that answers `404` for one of them (an older server) is reported as a failed part, as today.
- [ ] T073 [P] [US5] packages/presentation/ui/src/components/ModeBanner.test.tsx and an archive case in ViewFrame.test.tsx:
  - In archive mode the banner names the archive, its creation time and its commit, and lists missing parts.
  - Ages are relative to the mode's `now`.
  - A view unavailable with "not in this archive" is shown neutrally, with no remedy styled as an error.
  - The metrics range picker is fixed to the exported range, with the reason.
  - Nothing is labelled live, and polling is off.

### Implementation

- [ ] T074 [US5] packages/presentation/snapl_presentation/dashboard/archive.py: `open_archive`, `ArchivedIntent`, `Archived`, `ArchivedMetrics`.
- [ ] T075 [US5] packages/presentation/snapl_presentation/cli.py: `--archive`, building `Sources` from the archive, the fixed clock, and the start lines. No settings are read from the environment in this mode.
- [ ] T076 [US5] tasks/history.py: `_EXPORTS` gains the four routes. Amend specs/012-measured-metrics/contracts/history-tasks.md "Contents" to list them, with a link to this spec.
- [ ] T077 [US5] packages/presentation/ui/src/components/ModeBanner.tsx, and the mode read once at load and passed to `ViewFrame`, the range picker and `usePolling`.
- [ ] T078 [US5] **Needs the lab**: quickstart item 6 (a real archive, the stack stopped, Wi-Fi off, the checksum).

**Checkpoint**: history can be read months later on a machine with nothing running.

---

## Phase 8: Snapshot (US6)

**Goal**: one file shows the loop to someone without the lab.

### Tests (write first, verify they fail)

- [ ] T079 [P] [US6] tests/unit/test_presentation/test_dashboard_snapshot.py, with a stub template in a temp directory:
  - `collect_bundle(app, days)` calls the routes in-process and returns `SnapshotBundle` (data-model.md), with one `steps` entry per deploy run and the manifest's range ending at the app's clock.
  - `render(template, bundle)` puts the JSON in the `snapl-bundle` element. A payload containing `</script>`, `<!--`, `&` and U+2028 leaves exactly one script element of that id, and it parses back to the same bundle.
  - With fake `GITHUB_TOKEN` and `INFRAHUB_API_TOKEN` set, neither value is in the output.
  - A source that did not answer is in the bundle as its unavailable view, and is named in the printed summary.
  - Writing goes through a `.partial` file; an existing target is refused without `--force`.
  - No template: the contract's error and exit code.
- [ ] T080 [P] [US6] Extend tests/unit/test_presentation/test_dashboard_at.py with a generator, run by a pytest that also asserts the file is current: tests/unit/test_presentation/fixtures/devices_at.json holds the timeline response and the `/devices?at=` and `/runs?at=` answers for a time just before and just after every scan boundary in `audit_demo_arc.sqlite`.
- [ ] T081 [P] [US6] packages/presentation/ui/src/data/inline.test.ts:
  - For every entry in `devices_at.json`, `InlineProvider.devices(at)` equals the server's answer for drift state, paths, error, last scan time and last deploy (SC-004). The same for `runs({at})` states.
  - Without `at`, each method returns the bundled response.
  - `metrics` returns the bundled view, and an unavailable view for a range that is not the bundled one.
  - A bundle with an unknown `format` is refused with a message.
  - `mode()` is `snapshot`, with `now` the bundle's `end`.
- [ ] T082 [P] [US6] packages/presentation/ui/src/App.snapshot.test.tsx:
  - The recording banner shows when it was made, the commit and the range.
  - No view is labelled live, nothing polls, and suggested commands appear under "the operator could run" with no copy control.
  - The scrubber works over the bundle's range.
- [ ] T083 [US6] packages/presentation/ui/e2e/snapshot.spec.ts: build a snapshot from the fixture server's app with the CLI, open it as a `file://` URL with the browser context offline, and check the topology, the scrubber replaying spine-01's drift, the loop strip, and that the page made zero requests (SC-009). Assert the file is under 3 MB, and print its size.

### Implementation

- [ ] T084 [US6] packages/presentation/ui/snapshot.html and vite.snapshot.config.ts (`vite-plugin-singlefile`, the `<meta>` policy of research R4, the empty bundle element), built by the same `npm run build` into `static/ui/snapshot.html`.
- [ ] T085 [US6] packages/presentation/ui/src/data/inline.ts, and `main.tsx` choosing `InlineProvider` when the bundle element is present and not empty.
- [ ] T086 [US6] packages/presentation/snapl_presentation/dashboard/snapshot.py: `collect_bundle`, `render`, `write`. cli.py: `--snapshot`, `--days`, `--force`, alone or with `--archive`, printing the contract's summary.
- [ ] T087 [US6] **Needs the lab**: quickstart item 7 in Chromium, Firefox and Safari, with the file's size recorded in the PR.
- [ ] T088 [US6] **Needs the maintainer**: quickstart items 10 (a first-time viewer, SC-003) and 12 (read a snapshot before publishing one).

**Checkpoint**: `demo.html` can be attached to a post.

---

## Phase 9: Docs and Governance

- [ ] T089 [P] README.md: the dashboard section describes the designed UI, `dashboard.build`, `/plain`, `--archive` and `--snapshot`, with one screenshot taken from the fixture server (dark mode). Known limitations gain: the loop strip shows completed steps only; a past time shows today's devices and links; an archive's metrics are the exported range only.
- [ ] T090 [P] AGENTS.md and CLAUDE.md: the UI directory, the `dashboard.*` tasks, and the TypeScript test rule (spec FR-046). Add CLAUDE.md's "Recent Changes" line for 011 by hand: in planning, `update-agent-context.sh` named the entry after the working branch and dropped older entries, so its output was discarded.
- [ ] T091 [P] docs/demo-scenarios.md: a scenario that runs the arc with the dashboard open, replays it, and writes a snapshot. Plain claims only (ADR-0004).
- [ ] T092 [P] dev/adr/0005-dashboard-and-metrics-stack.md: Decision 2 to Accepted, with the PR numbers, and its diagram's browser node updated. `dev/guides/running-tests.md`: the UI tests and the browser check.
- [ ] T093 Run the humanizer skill over the UI's visible strings (empty states, reasons the UI words itself, banners) and the README section, and apply what it finds. Device- and server-supplied text is not touched.
- [ ] T094 Open a **draft** PR, from a branch that is not `overnight/` (`docs/146-constitution-ui-stack`), amending `.specify/memory/constitution.md` (research R18): the Technology Stack gains FastAPI and uvicorn, Prometheus and the UI toolchain; Principle III says it applies to UI code; minor version bump. Tick this task when the draft is open. Merging it is the maintainer's decision, and an unattended run never merges it.
- [ ] T095 **Needs the maintainer**: quickstart item 13 (try the design skill locally), and decide whether a snapshot is published for the demo post.
- [ ] T096 **Needs the lab**: quickstart item 8 (a fresh clone with no Node serves the plain page).

---

## Dependencies & Execution Order

- **Phase 1 → 2**: the timeline reuses the sort helper and `IntentDevice`.
- **Phase 3** needs Phases 1 and 2 only for the generated types. It can start once their OpenAPI snapshot has merged.
- **Phase 4 → 5 → 6**: each builds on the shell and `ViewFrame`. Phase 6 does not depend on Phase 5.
- **Phase 7** needs Phases 1, 2 and 3. Its UI task (T077) needs Phase 4's shell.
- **Phase 8** needs Phase 5 (the scrubber) and Phase 7 (to snapshot an archive).
- **Phase 9** last.

Within a phase, tasks marked [P] touch different files and can be done in any order. Tests come before the code they cover, always.

## Deferred to the lab or the maintainer (summary)

| task | what | who |
| --- | --- | --- |
| T046 | the demo arc on the topology; a source down | lab |
| T056 | replay and the headline against `snapl audit` | lab |
| T057 | capture a real failed deploy as a fixture | lab |
| T068 | time the three questions | maintainer |
| T078 | a real archive, offline | lab |
| T087 | the snapshot in three browsers | lab |
| T088 | a first-time viewer; read a snapshot before publishing | maintainer |
| T094 | merging the constitution amendment (the draft PR is opened by the run) | maintainer |
| T095 | the design skill; publishing a snapshot | maintainer |
| T096 | a fresh clone with no Node | lab |
