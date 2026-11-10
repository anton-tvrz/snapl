# Research: Designed Dashboard UI

**Feature**: 011-dashboard-ui | **Date**: 2026-11-09

Facts marked **read** were read from the tree at `main` (6baff39). Facts marked **measured** were computed from `tests/unit/test_presentation/fixtures/audit_demo_arc.sqlite`, the audit trail captured from a live demo arc on 2026-11-07. Everything else is a decision, with its reason and what was rejected.

---

## R1 — Stack and versions (ADR-0005 Decision 2)

**Decision**: React, Vite, TypeScript, Tailwind with shadcn/ui components copied in, and Recharts for the metrics panels. No graph-canvas library (R7). Tests use Vitest with jsdom and Testing Library, and Playwright for the one browser check.

Latest versions on npm on 2026-11-09, looked up, not assumed:

| package | version | used for |
| --- | --- | --- |
| react, react-dom | 19.3.1 | the UI |
| vite | 8.3.7 (Node `^20.19.0 \|\| >=22.12.0`) | build and dev server |
| @vitejs/plugin-react | 6.3.0 | |
| typescript | 7.0.2 | |
| tailwindcss, @tailwindcss/vite | 4.3.5 | styling |
| recharts | 3.10.1 (peer: React 16.8 to 19) | metrics line charts |
| vitest, @vitest/coverage-v8 | 5.1.0 | unit tests and coverage |
| @testing-library/react | 16.3.3 | component tests |
| jsdom | 31.0.0 | |
| @playwright/test | 1.65.0 | browser check |
| openapi-typescript | 7.13.0 | contract types (R5) |
| vite-plugin-singlefile | 2.3.4 | the snapshot template (R14) |
| eslint | 10.14.0 | the markup ban (R4) |
| @fontsource-variable/inter | 5.3.0 | bundled font, no CDN |

- **Pinning**: `package.json` pins exact versions, and `package-lock.json` is committed. `npm ci` is the only install command in CI and in `invoke dashboard.build`.
- **Node**: 22 LTS, named in `packages/presentation/ui/.nvmrc`. The maintainer's machine has Node 26.8.1, which Vite's range also accepts.
- **Several of these are new major versions.** The first implementation slice reads each tool's current documentation before configuring it, and does not rely on remembered configuration from older majors.
- **shadcn/ui** is not a dependency. Its CLI copies component source into the tree. Only the components in use are copied (button, card, badge, tooltip, sheet, tabs, popover), into `src/components/ui/`, and they are excluded from the coverage count as vendored code.

**Rejected**:
- **React Flow** for the topology: see R7.
- **A chart library for the lanes and the topology**: both are custom SVG, because their encodings are specific to this UI.
- **No Recharts** (hand-drawn metrics lines, as 010's page has): axes, tooltips and a brush for a free range are what a chart library is for. #146 settled it.

## R2 — Where the UI lives, and how it ships (FR-001 to FR-003)

**Read**: `packages/presentation/pyproject.toml` builds with hatchling and `packages = ["snapl_presentation"]`. The plain page is package data at `snapl_presentation/dashboard/static/index.html`, read with `importlib.resources`. `.gitignore` has `dist/`.

**Decision**:
- **Source**: `packages/presentation/ui/`. The whole Node toolchain is inside it (ADR-0005: bounded to one directory).
- **Build output**: `packages/presentation/snapl_presentation/dashboard/static/ui/`, git-ignored. It holds `index.html`, `assets/*` and `snapshot.html` (R14).
- **Wheel and sdist**: hatchling leaves git-ignored files out by default. `[tool.hatch.build.targets.wheel] artifacts = ["snapl_presentation/dashboard/static/ui/**"]` puts them back, and the same for the sdist target. A unit test reads `pyproject.toml` and fails if the line is gone.
- **`invoke dashboard.build`**: `npm ci && npm run build` in the UI directory. It fails with a clear message when `node` is missing.
- **`build-artifacts.yml`**: gains `actions/setup-node` and `uv run invoke dashboard.build` before `uv build --package snapl-presentation`, then a step that fails if the wheel has no `static/ui/index.html`.
- **The plain page** keeps its file. It is served at `/plain` always, and at `/` when `static/ui/index.html` does not exist, with a one-line notice inserted by the server naming `uv run invoke dashboard.build`.

**Rejected**: committing the built files. They would be several hundred kilobytes of minified diff on every UI change, and #146 settled it.

## R3 — Serving the built files without widening the read-only surface (FR-004, 010 R2)

**Read**: 010's read-only test walks every route and fails on any method outside `GET`, with `HEAD` allowed only on the framework's OpenAPI route. Starlette's `StaticFiles` mount answers `GET` and `HEAD`, and appears as a mount, not as routes the test can enumerate.

**Decision**: no `StaticFiles` mount. Two explicit `GET` routes:
- `GET /` returns the UI's `index.html`, or the plain page with the notice.
- `GET /assets/{path:path}` returns a file from `static/ui/assets/`. The path is resolved and must stay inside that directory, or the answer is `404`. Unknown files are `404`.

Assets carry a content hash in their name (Vite's default), so they are served with a long `Cache-Control`. `index.html` is served `no-cache`.

The UI keeps its state in the query string (R12), so there is no client-side path routing and no catch-all route is needed.

## R4 — No external requests, and device text as text (FR-005, FR-006)

**Decision**:
- **Header**: a middleware sets `Content-Security-Policy` on every response.
  - For the UI and the API: `default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`. `style-src-attr 'unsafe-inline'` is needed because React and Recharts position SVG and tooltips with `style` attributes. It allows no script and no request.
  - For `/plain` (and `/` when it falls back): the same, with `script-src 'unsafe-inline'` and `style-src 'unsafe-inline'`, because 010's page is one file with inline script and style. `connect-src 'self'` still holds, so it cannot reach another origin either.
- **Snapshot file**: it has no server to send a header, so it carries `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:">`. The browser then refuses every request the page could make (SC-007, SC-009).
- **Fonts**: bundled through `@fontsource-variable/inter`. Nothing loads from a CDN.
- **Device text**: React renders strings as text. The one way around that is `dangerouslySetInnerHTML`, so ESLint's `react/no-danger` is an error, with no disable comments allowed in `src/` (a unit test greps for them). This replaces 010 R11's `textContent` rule.
- **Tests**: a Python test asserts the header on `/`, `/plain`, an asset and an API route. The Playwright check records every request and fails on any whose origin is not the server's.

**Rejected**: a nonce or hashes for the plain page's inline blocks. They add moving parts to protect a page whose `connect-src` already confines it.

## R5 — Contract types (FR-007, FR-008)

**Read**: `tests/unit/test_presentation/fixtures/openapi_v1.json` is the snapshot of the server's OpenAPI document, and a unit test fails when the app's document differs from it.

**Decision**:
- `openapi-typescript` generates `packages/presentation/ui/src/api/schema.d.ts` from that snapshot file. No server has to run. The generated file is committed.
- `npm run types:check` regenerates to a temp file and diffs. CI runs it, so a contract change that forgets the types fails.
- The chain is then: Pydantic models → OpenAPI snapshot (pytest) → TypeScript types (CI). One source.
- **Open enums**: `src/api/open.ts` defines `type Open<T extends string> = T | (string & {})` and re-exports the five enums the contract marks as open (`DriftState`, `RunState`, `Trigger`, `JobResult`, `SourceName`) wrapped in it. Every lookup table keyed by one of them goes through one helper that returns a neutral entry, with the raw text as its label, for an unknown value. A test feeds each an unknown value.

## R6 — Topology in the contract (FR-009, FR-010)

**Read**: `SotSource.refresh()` already returns `View[list[DesiredState]]`, and `DesiredState.interfaces` carries `peer_device` and `peer_interface`. dcfabric's seed declares all 16 link ends (8 links, both ends each).

**Decision**: `GET /api/v1/topology`, built by a pure function `project_topology(states)` in `projections.py`. No new read from the Source of Truth: it reuses the 30 s cache and the identity check of the device view.
- A **link** is keyed by its two ends, ordered by device name then interface name, so the same link declared from both ends is one link.
- `declared_by` is `both`, or the one end that declared it. `agreed` is true only when both ends name each other's device and interface.
- An interface whose peer is not a device of the use case goes in `unlinked`, with the peer's name as declared.
- When the Source of Truth is unavailable or foreign, the view is unavailable with its reason and remedy, and `data` is null. The UI then falls back to the device list (FR-017). There is no audit-derived topology: links exist only in intent.

**Archive mode** needs device names and roles without the Source of Truth (R13). So `compose_devices` and `_device_names` are changed to read a small `IntentDevice` record (`id`, `name`, `role`) instead of `DesiredState`. The live source maps `DesiredState` to it, and the archive source builds it from the exported topology. Management addresses never enter an archive or snapshot this way.

## R7 — Drawing the topology (FR-011 to FR-018)

**Decision**: a hand-written SVG component, with layout as a pure function `layoutTiers(nodes, links, size)` returning positions.
- **Tiers**: `spine` first, then `leaf`, then any other role in alphabetical order. Inside a tier, devices sort by name with numbers compared as numbers (`leaf-2` before `leaf-10`).
- **Deterministic**: the same intent gives the same positions on every load, which also makes the browser check and screenshots stable.
- **Limit**: 32 devices (spec Assumptions). Above it the device list is shown with the reason.
- **Devices are buttons** in the accessibility tree, in tier order, so keyboard use needs no extra work (FR-044).

**Rejected**: React Flow (`@xyflow/react`), which the first scope named. It brings pan, zoom, drag and a minimap, none of which a fixed six-node drawing uses. A custom component is about 150 lines, gives direct control over the activity mark and link marks, and keeps the snapshot file small. Recorded in #146 and ADR-0005.

## R8 — Which link a drifted path marks (FR-014)

**Measured**: the fixture's paths are `/interface[name=ethernet-1/1]/enabled` and `/interface[name=ethernet-1/7]/description`. These are snapl's own normalised paths from the drift field map, keyed gNMI-style. They are not vendor paths.

**Decision**: one function, `interfaceOf(path)`, in the UI: it returns the name from a leading `/interface[name=…]` segment, or null. A link is marked when either end's device has a differing path whose interface is that end's interface. A path that names no interface, or an interface with no link, marks the device only. In the fixture, spine-01's `ethernet-1/1` marks the link to leaf-01, and leaf-01's `ethernet-1/7` marks nothing but leaf-01.

**Rejected**: doing this on the server by adding a field to `DeviceView`. It is presentation logic over data the UI already has, and it would repeat every path in every response.

## R9 — Activity on a device (FR-013, SC-002)

**Measured**: the eight deploys in the fixture took 0.90 s to 1.59 s. The three scans took 1.06 s to 1.27 s, and the reconcile 1.97 s. Every one is shorter than the 5 s refresh. A marker fed only by Temporal's running list would have shown none of them.

**Decision**: the UI derives activity from two views it already polls:
- `running` (Temporal): a run whose `target` is a device name marks that device as active.
- `runs` (audit): a run whose `ended_at` is within 10 s of the response's `generated_at` marks its target as recently active, with its outcome.

A run whose target is the use case marks the fabric frame, not each device (FR-013). Reduced motion swaps the pulse for a static ring (FR-043).

Server-sent events stay out. Polling plus the audit-derived window meets SC-002 by construction: a run that ended less than 10 s before a poll is always inside two polls.

## R10 — Lanes and episodes (FR-019 to FR-021, FR-025)

**Read**: `ScanDrift._audit_device` writes one `detect_drift` record per device per scan, `ACTIVITY_COMPLETED` or `ACTIVITY_FAILED`, with `paths` and `undesired_paths`. 010's `project_devices` already classifies one such record into a `DriftState`.

**Decision**: one route, `GET /api/v1/drift-timeline?start&end`, built by a pure function `project_timeline(events, names, start, end)`.
- **Lanes**: per device, its scan points in the range. A point carries the scan time, the state, the paths, the error and the scan's workflow id. The paths are included so that a snapshot can show a device's detail at a past time without the server (R14).
- **Episodes**: every episode that overlaps the range.
  - It opens at the first scan point that is not `in_sync`, `error` or `never_scanned`.
  - It closes at the first later point that is `in_sync`.
  - An `error` point in between is kept in the episode's states and changes nothing else.
- **What closed it** (FR-020): the latest successful `DeployIntent` run for that device that ended after the episode's last drifted scan and before its closing scan. Its kind follows 010's trigger rules (R5 there): `remediation` when the trigger is the schedule, `reconcile` when its parent is an operator's `ReconcileDevices`, `deploy` when an operator started it directly. With no such run, `closed_by` is null and the UI says "unknown".
  - **Spec amendment**: FR-020 said "the reconcile or remediation run". A direct `snapl deploy <device>` repairs drift too, and the audit trail records it as a run that targeted the device. The requirement now says "the deploy", with its three kinds. The rule that timing alone proves nothing is unchanged: the run must have targeted the device.
- **Headline**: the response carries `latest_closed` (the most recently closed episode in the whole trail up to `end`, even if it opened before `start`) and `open_count` as of `end`.
- **Bound**: more than 20,000 scan points in the range returns `422`, like 010's 2,000-bucket rule. At a 5-minute schedule and 6 devices that is about 11 days. The UI then asks for a shorter range. Aggregating lanes for longer ranges is left out until someone needs it (Principle VII).
- **Hold**: the 15-minute hold (spec Assumptions) is drawing logic, in the UI. The server returns points, not segments.

**Measured against the fixture**: spine-01 has one episode, opened by scan 1, still drifted in scan 2 (the reconcile's own scan), closed by scan 3, with `closed_by` the reconcile's child deploy. leaf-01 has one open episode, `undesired` at all three scans. The other four devices have none.

## R11 — State at a past time (FR-022 to FR-024)

**Decision**: `/api/v1/devices` and `/api/v1/runs` gain an optional `at` (an aware datetime, not in the future, else `422`).
- The projection runs over the events with `timestamp <= at`. The projections are already pure functions of the events, so this is a slice, found by bisecting the timestamp-ordered read model.
- **Runs at a past time**: a run that had started but not ended by `at` is `running` if the full trail holds its terminal event after `at`, and `unfinished` otherwise. Temporal is not asked.
- **Names and roles** come from today's Source of Truth (spec Assumptions).
- `/overview` does not take `at`. The schedule, worker, CI and running views have no history, and the UI labels them present-only when a past time is shown (FR-024).
- The UI does not poll the past views every 5 s. It refetches them when `at` changes, and once a minute to pick up late audit rows (010 R3).

## R12 — URL state and refresh (FR-032, FR-033)

**Decision**:
- Query parameters: `device` (a name), `at`, `from`, `to`, `run` (a workflow id and start time). They are written with `history.replaceState`, so scrubbing does not fill the back button.
- One hook, `usePolling(fn, ms)`, owns every interval: 5 s for the overview, 30 s for metrics, 60 s for the timeline. It listens for `visibilitychange`, stops while hidden and fires at once when shown.

## R13 — Archive mode (FR-035 to FR-039)

**Read**:
- `tasks/history.py` writes `manifest.json` (`format: "snapl-history/1"`, `created_at`, `snapl_commit`, `use_case`, `parts`), `audit/snapl-audit.sqlite`, `prometheus/<snapshot>/`, `test-runs/`, and `export/{overview,history,links,sources}.json` plus `export/metrics/<view>.json`, each an API response for the last 7 days.
- `create_app` already takes its `Sources` and a `clock`. The audit source opens SQLite read-only.

**Decision**: archive mode is a server mode. The browser talks to the same API and needs no archive-specific data code.
- **Open**: `snapl dashboard --archive <file>` reads `manifest.json` first. A file that is not a tarball, has no manifest, or has a format other than `snapl-history/1` is refused before any port is bound, with a message that says which.
- **Extract**: into a `TemporaryDirectory`, with `tarfile`'s `data` filter so no member can escape it. The directory is removed when the server stops. The archive file is opened read-only and never written.
- **Sources**:
  - `audit`: the normal `AuditSource` on the extracted database. Lanes, episodes, `at`, runs and steps therefore work over the whole archived trail.
  - `lab_runs`: the normal `LabRunSource` on the extracted `test-runs/`, with no checkout. Commits-since is "unknown", with the reason that an archive has no git history.
  - `sot`: an `ArchivedIntent` source that reads `export/topology.json`. An archive without it (made before this feature) gives the degraded device list, and the reason says the archive predates the topology export.
  - `temporal`, `ci`: `Archived` sources that return the views inside `export/overview.json` as exported, with their original `fetched_at`.
  - `prometheus`: an `ArchivedMetrics` source that answers a view from `export/metrics/<view>.json` when the requested range is the exported one, and otherwise is unavailable with the reason "an archive holds only the exported range".
- **A view the archive does not hold** is `available: false` with the reason "not in this archive" and the remedy `uv run snapl dashboard`. The contract requires a remedy on every unavailable view, and that command is how to see the view live. The UI renders these neutrally in archive mode, not as failures (FR-037).
- **Clock**: `create_app(clock=...)` is fixed to the manifest's `created_at`, so every age and default range is relative to the archive.
- **Mode**: a new `GET /api/v1/mode` tells the UI which mode it is in, and in archive mode gives the archive's name, creation time, commit and parts, for the banner.
- **Archive additions** (compatible, format unchanged): `_EXPORTS` in `tasks/history.py` gains `topology`, `devices`, `runs` and `drift-timeline`. Old archives lack them and still open.
- **Port and bind** rules are 010's, unchanged.

**Rejected**:
- **Serving only the `export/` JSON**: simpler, but ranges and time travel would stop at what was exported. The archive holds the full audit trail, so the projections can run on it.
- **Starting Prometheus on the archived snapshot**: it needs Docker, which is "the stack" the feature says it works without. 012's quickstart item 6 already documents doing that by hand.

## R14 — Snapshot (FR-040 to FR-042)

**Fact**: a page opened from `file://` cannot `fetch()` a file next to it. Browsers treat each local file as its own origin.

**Decision**:
- **Template**: the UI build produces a second output, `static/ui/snapshot.html`, with `vite-plugin-singlefile`: every script, style and font inlined, the CSP `<meta>` of R4, and an empty `<script id="snapl-bundle" type="application/json">` element.
- **Command**: `snapl dashboard --snapshot <out.html> [--days 7]`, alone or with `--archive <file>`. It builds the app in-process exactly as serving would, calls its own routes through an in-process ASGI client, puts the answers into the template, writes the file and exits. No port is bound.
- **Bundle**: `{manifest, mode, overview, devices, runs (limit 500), topology, timeline, steps (one per deploy run in runs), links, sources, metrics}` for the range.
- **Escaping**: the JSON is written with `<`, `>`, `&` and U+2028/2029 escaped, so no audit string can end the script element.
- **In the browser**: `InlineProvider` implements the same `DataProvider` interface as `HttpProvider`. `devices(at)` and `runs(at)` are computed from the bundle's lanes and runs by small TypeScript functions.
  - That is a second implementation of "state at a time". To keep the two from drifting, a Python test writes the server's `/devices?at=` answer for every scan boundary of the fixture to a JSON file, and a Vitest test checks `InlineProvider` gives the same for each (SC-004).
- **What it says about itself**: the command prints the path, size, range, and the kinds of data included, and that device-supplied text is included as recorded.
- **No secrets**: the bundle is made only of API responses, and no response carries a credential. A test runs the command with a fake `GITHUB_TOKEN` and `INFRAHUB_API_TOKEN` set and asserts neither value is in the file.

**Rejected**: a snapshot as a folder with a JSON file and a tiny server. It could not be attached or hosted as one file, and "run this server" is what the snapshot exists to avoid.

## R15 — The loop strip (FR-026 to FR-028)

**Read**:
- `DeployIntent` writes `ACTIVITY_COMPLETED` for `fetch_desired_state`, `apply_config`, `collect_drift_state` and `detect_drift`, in that order, each after its step succeeds.
- It writes no `ACTIVITY_STARTED` and no `ACTIVITY_FAILED`. A failed deploy ends with `WORKFLOW_TERMINATED`, `outcome: failure`, and a `reason` that names the step: `intent_unavailable` or `device_not_found`, `apply_failed`, `collect_failed`, `verification_failed`.
- `ScanDrift` writes one `detect_drift` record per device. `ReconcileDevices` and `DriftResponse` write no step records.

**Measured**: the fixture's eight deploys each have all four step records, with `outcome: success`. It has no failed deploy.

**Decision**: `GET /api/v1/runs/steps?workflow_id=&started_at=`, built by a pure function `project_steps(run_events)`.
- One table on the server, `STEP_BLOCKS`, maps the four activity names to `intent`, `executor`, `collector` and `observability`.
- For a `DeployIntent` run, `recorded` is true. Each block is `done` (its step record exists), `failed`, or `not_reached`.
- **Which block failed** (*amended in PR B*): the first plan mapped each failure reason to a block. Reading `deploy_intent.py` again showed that is wrong for one case: `collect_failed` ends the run both when the collect fails and when the drift check itself fails, after the collect step was recorded. So the reason does not name the block. The rule is now:
  - a run that ended with `intent_unavailable`, `device_not_found`, `apply_failed` or `collect_failed` failed at the first block with no step record;
  - `verification_failed` fails Observability, whose check ran and found drift left on the device;
  - `cancelled` and `audit_failed` blame no block. Blocks with no record are `not_reached`.
- For any other run type, `recorded` is false, the four blocks are `not_recorded`, and the UI shows the outcome only (FR-028).
- The Orchestrator block is `done` for any run with a start record. Presentation is `surface` (spec Assumptions).
- An unknown activity name is returned as a step with `block: null`, never dropped.

**Fixture gap**: a failed deploy has not been captured from the lab. The unit tests build one from the event shapes read above, and capturing a real one (pull a link's config so apply fails, or stop a node) is a **Needs the lab** task. Principle III: a hand-built fixture is an assumption until then, and the PR says so.

## R16 — Tests and CI (FR-046, FR-047)

**Decision**:
- **Unit**: Vitest. Logic lives in plain modules (`src/lib/`, `src/data/`) so most tests need no DOM. Components are tested through Testing Library by what they render for a fixture.
- **Coverage**: 80% of lines over `src/`, excluding the generated `schema.d.ts` and the copied `components/ui/`. Enforced by Vitest's threshold, so CI fails below it.
- **Test first**: each task lists its test file before its source file, as the Python tasks do. "Never a source file without its test file" applies to every module under `src/lib/`, `src/data/` and `src/components/` except the copied ones.
- **Browser check**: one Playwright spec. It starts `dev/scripts/serve_dashboard_fixtures.py`, which serves the real app with the audit source on `audit_demo_arc.sqlite`, an intent source built from dcfabric's seed `topology.yml`, the other sources unavailable, and the clock fixed at the fixture's end. It checks six devices, eight links, leaf-01 as undesired, the marked link, the detail of spine-01 at a past time, and that no request left the origin.
- **CI**: a `ui` job in `quality.yml`, run when `.github/file-filters.yml`'s new `ui` filter matches (`packages/presentation/ui/**`, the dashboard package, the OpenAPI snapshot). It runs `npm ci`, `tsc --noEmit`, ESLint, `npm run types:check`, Vitest with coverage, the build, then Playwright with Chromium only. The quality gate adds it to `needs`, and a skipped job passes, as for the others.
- **Python tests never need the built UI.** They cover the fallback, and they cover serving the UI with a stub `static/ui/` in a temp directory.
- **Secrets hooks**: `package-lock.json` holds integrity hashes that detect-secrets flags. It is added to the hook's `exclude`, next to `uv.lock`.

## R17 — Colour, encoding and the written rules (FR-012, FR-045, SC-010)

**Decision**:
- `dev/skills/dashboard/SKILL.md` records the rules: one encoding table for the seven drift states (colour token, shape, icon, label), the run-state and source-status tables, when colour may be used, chart rules for the lanes and metrics, and motion rules.
- The encoding table itself is code, `src/lib/encoding.ts`, and the skill points at it.
- A Vitest test checks the table: each state's mark has at least 3:1 contrast against both backgrounds, text has 4.5:1 (WCAG 2.1 AA), every pair of states differs in shape or icon, and under simulated protanopia, deuteranopia and tritanopia no two states that share a shape are closer than a set colour distance.
- **Skills from #146**: the design skill (`pbakaus/impeccable`) is tried by the maintainer locally and is not installed by an unattended run. A third-party skill is instructions an agent follows, and #146's rule is to read it in full first. The browser-testing skill is not needed to write one Playwright spec. Neither blocks any task.

## R18 — Governance

- **ADR-0005 Decision 2** is updated in the planning PR: React Flow removed, the facts of R1 to R4 added. It becomes Accepted when the UI slices have merged.
- **Constitution**: the Technology Stack gains FastAPI and uvicorn (010), Prometheus (012) and the UI toolchain (011), and Principle III gains one sentence saying it applies to UI code with Vitest. That is a minor version bump. It goes in its own PR, opened as a **draft** for the maintainer: an unattended run does not merge a constitution change.
- **Human-timed criteria** (SC-003, SC-014) and **lab evidence** are listed in quickstart.md and marked **Needs the lab** or **Needs the maintainer** in tasks.md.
