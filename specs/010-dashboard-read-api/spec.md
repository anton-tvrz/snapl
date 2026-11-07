# Feature Specification: Dashboard Read API and MVP Page

**Feature Branch**: `010-dashboard-read-api`
**Created**: 2026-11-07
**Status**: Draft
**Input**: Issue #101 ("drift and audit have no operator-visible surface"), README known limitation "No metrics or dashboards (#101)", constitution 2.0.0 Principles III, V and VIII.2
**Scope settled**: 2026-11-07 with the maintainer, in a design session that split #101 into three specs (see the table below).

## Context

snapl runs a closed loop (intent → deploy → verify → drift → reconcile), and every step lands in the durable audit trail. Since spec 006 an operator can read that trail through `snapl status`, `snapl scan`, `snapl audit` and `snapl schedule show`. Since spec 009 the loop also runs on its own, every five minutes.

That is the problem this feature solves. The loop now does work nobody watches, and the only views of it are one command at a time:

- **No single place shows the state of the fabric.** To answer "which devices are in sync, and when was each last deployed and scanned?" an operator runs several commands and joins the answers in their head.
- **Nothing shows history.** The audit trail holds every scan since the database was created, but nothing turns it into "drift over the last week". The demo moment #101 describes (the induced-drift spike and the return to zero after reconcile, shown as a line) cannot be shown.
- **The schedule works in the dark.** A report-mode schedule finds drift at 02:00 and records it, and nobody sees it until they ask. A remediate-mode schedule whose repair fails is retried every run (#136), and nothing surfaces that it has failed ten times in a row.
- **Live lab evidence leaves no trace.** Principle III requires boundary changes to carry evidence from a live lab run. The integration and end-to-end suites never run in CI, because they need the lab. When they run on the maintainer's machine, the result is a terminal scrollback. Nobody can tell how long ago the loop last passed for real, or how many commits on `main` have landed since.
- **CI is only visible through GitHub's own pages**, one run at a time.

Option 1 of #101 (CLI rendering) shipped with spec 006. This feature delivers the rest of #101 in three specs, each usable on its own:

| spec | delivers |
| --- | --- |
| **010 (this spec)** | A local, read-only dashboard server: a typed JSON read API over the audit trail, the workflow engine, the Source of Truth, CI and recorded live lab runs, and one plain page that renders it. History derived from the audit trail. Recording of live lab runs. |
| 011 | The designed UI on top of 010's API: the spine-leaf topology view, charts, snapshot export, and opening an archive read-only. |
| 012 | Measured metrics: Prometheus, the workflow engine's built-in metrics, gNMI timing, size-capped long-lived retention, and `history.archive`. |

### Why this needs a specification rather than a patch

Principle II requires a spec for a new capability. A server that brings five sources together, with a contract other surfaces (011's UI, 011's snapshot export) will build on, is one. The contract is the point of this spec. The page in front of it is deliberately plain so the data can be checked before any design work starts.

### What this feature must never do

The dashboard is read-only (decided 2026-11-07). It never writes to a device, the Source of Truth, the workflow engine or the audit trail. Principle VIII.2 says remediation is operator-triggered or explicitly automated. A button on a page is a softer trigger than a typed command, and the CLI already records the actor. Where the dashboard suggests an action, it shows the `snapl` command to run.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — See the State of the Fabric in One Place (Priority: P1)

An operator opens the dashboard and sees every device in the use case at once. For each device: its role (spine or leaf), whether it matches intent, how it differs if it does not (config missing from the device, config on the device that intent never asked for, or both), when it was last scanned, and how its last deploy ended. Nothing needs to be typed.

**Why this priority**: This is the question the operator asks first and the one the CLI answers worst. It also sets up the contract every later panel reuses: a typed view, what source it came from, and how old it is.

**Independent Test**: Against an audit trail fixture captured from a live lab run (one deploy per device, one hand-made change, one scan, one reconcile) and a mocked Source of Truth, request the device view and verify each device's role, drift state, last scan time and last deploy outcome match the fixture.

**Acceptance Scenarios**:

1. **Given** a use case with six devices in the Source of Truth and a completed scan in which one device drifted, **When** the operator opens the dashboard, **Then** six devices are shown, five as in sync and one as drifted, and the drifted device names whether config is missing, undesired, or both.
2. **Given** a device that has never been scanned, **When** the dashboard is opened, **Then** that device is shown as "never scanned", not as in sync.
3. **Given** a scan in which a device could not be collected, **When** the dashboard is opened, **Then** that device is shown as errored with the reason, not as in sync or drifted.
4. **Given** a device that carries undesired config, **When** the operator looks at it, **Then** the undesired paths from its latest scan are listed, and the dashboard says this config is not removed automatically (#65).
5. **Given** a drifted device, **When** the operator looks at it, **Then** the dashboard shows the command that would reconcile it (`snapl reconcile <device>`) and offers no way to run it from the page.

---

### User Story 2 — See What the Loop Has Been Doing (Priority: P1)

An operator wants to know what ran while they were not looking. The dashboard lists recent workflow runs (deploys, scans, reconciles and scheduled drift responses) with what each targeted, how long it took, how it ended and why, and who or what started it. It also shows the drift schedule as it is right now: its response mode, interval, whether it is paused and why, and when it last ran and runs next. It shows any workflow still running.

**Why this priority**: Since spec 009 the loop acts on its own. An operator who cannot see what it did cannot trust remediate mode, so this is as important as US1.

**Independent Test**: Against the same audit fixture plus a mocked workflow engine holding one schedule and one running workflow, request the runs view and the schedule view and verify every run, its outcome and trigger, the schedule's state, and the running workflow.

**Acceptance Scenarios**:

1. **Given** an audit trail with operator-started and schedule-started runs, **When** the operator opens the runs view, **Then** each run shows its type, target, start time, duration, outcome and reason. It also shows whether the schedule or an operator started it, naming the operator where the audit trail records one.
2. **Given** a run, **When** the operator wants its full history, **Then** the dashboard links to that run in the Temporal Web UI.
3. **Given** a workflow still in progress, **When** the dashboard is opened, **Then** it is shown as running, with how long it has been running.
4. **Given** a remediate-mode schedule whose repair of a device has failed on three or more consecutive runs, **When** the dashboard is opened, **Then** the schedule view says so, names the device and the count, and shows the command to switch back to report mode.
5. **Given** a paused schedule, **When** the dashboard is opened, **Then** the schedule is shown as paused with its reason and who paused it, and no next run time is shown.
6. **Given** no schedule exists for the use case, **When** the dashboard is opened, **Then** the schedule view says so and shows the command that creates one.

---

### User Story 3 — See Drift History Over Time (Priority: P2)

An operator wants to see how the fabric has behaved over hours, days or months: how many devices were drifted at each scan, deploy outcomes per period, repair attempts, and how much undesired config has built up. They pick a time range. History reaches back as far as the audit trail does, including after a long break from the project.

**Why this priority**: This is the demo moment #101 describes. It is P2 because the dashboard is useful without it, and its data already exists, so it needs no new recording.

**Independent Test**: Against an audit fixture whose events span several months, request each series for a range and a bucket size and verify every point. Include a range that starts before the first event and one that covers a gap of several weeks with no events.

**Acceptance Scenarios**:

1. **Given** an induced drift, a scan, a reconcile and a second scan, **When** the operator views the drift series for that hour, **Then** it shows the drifted count rise to one and return to zero, with the reconcile marked between them.
2. **Given** an audit trail whose oldest events are six months old, **When** the operator requests a range covering them, **Then** those events are included. Nothing in this feature expires or drops audit history.
3. **Given** a range with weeks of no scans (the lab was off), **When** the series is shown, **Then** the gap is shown as no data, not as zero drift.
4. **Given** a range, **When** the operator views deploy outcomes, **Then** deploys are counted per bucket by outcome.

---

### User Story 4 — Know Whether the Live Lab Evidence Is Current (Priority: P2)

The maintainer runs the live integration or end-to-end suite. Each run is recorded with its result, the commit it ran against, whether the working tree had uncommitted changes, and the lab it ran on. The dashboard shows the latest live run per suite, how old it is, and how many commits `main` has gained since. Next to it, the dashboard shows CI: the quality gate and each of its jobs for `main` and for each open pull request, and the latest unit-test coverage.

**Why this priority**: Principle III makes live evidence mandatory for boundary changes, and today nothing records it. "Main has 12 commits since the last live run" is the warning that tells the maintainer to run the lab before claiming the loop still works. P2 because it adds new recording, unlike US1 to US3.

**Independent Test**: Run a recorded suite against a stub test session (no lab needed) and verify the run record. Then, against a fixture of recorded runs, a local git history and a mocked CI service, verify the lab-evidence view and the CI view.

**Acceptance Scenarios**:

1. **Given** the maintainer runs the live end-to-end suite, **When** it finishes, **Then** a run record exists with pass, fail and skip counts, start and end times, the commit, the branch, whether the tree was dirty, and the lab's device image version.
2. **Given** a run that is interrupted, **When** the dashboard is opened, **Then** that run is shown as incomplete, not as passed.
3. **Given** the latest live run is at commit A and `main` has gained twelve commits since, **When** the dashboard is opened, **Then** it shows "12 commits on main since the last live run", with the run's age.
4. **Given** a recorded run's commit is not in the local history (for example a rebased branch), **When** the dashboard is opened, **Then** the commit count is shown as unknown, with the reason.
5. **Given** an open pull request whose unit-test job failed, **When** the dashboard is opened, **Then** the pull request's quality gate shows as failed and names the failed job, with a link to the run.
6. **Given** `invoke demo.reset` runs, **When** the dashboard is opened afterwards, **Then** every recorded live run is still there.

---

### User Story 5 — Tell Me When a Source Is Down, and How Old the Data Is (Priority: P3)

The dashboard draws on several sources that each run separately and fail separately: the audit trail, the workflow engine, the Source of Truth, CI, and the recorded lab runs. When one is unavailable, the parts of the dashboard that depend on it say so, say why, and say what to run to fix it. Everything else keeps working. Every part shows how old its data is.

**Why this priority**: ADR-0004 (claim only what is true) applied to a screen. A dashboard that shows stale numbers as current is worse than none. P3 only because US1 to US4 must exist before they can fail well.

**Independent Test**: For each source in turn, make it unavailable in a test and verify that the views depending on it report "unavailable" with a reason and a remedy, and that every other view is unchanged.

**Acceptance Scenarios**:

1. **Given** the workflow engine is stopped, **When** the dashboard is opened, **Then** the schedule and running-workflow views say the engine is unreachable and show the command that starts the stack, and the device, runs and history views still show audit data.
2. **Given** CI is unreachable or its rate limit is spent, **When** the dashboard is opened, **Then** the CI view shows its last successful answer with its age, or "unavailable" if it has none, and the rest of the dashboard is unaffected.
3. **Given** the Source of Truth answering on the configured address is not snapl's (spec 009 FR-031), **When** the dashboard is opened, **Then** the device view refuses to show that device list and says why.
4. **Given** any view, **When** it is shown, **Then** it states when its data was fetched.

---

### Edge Cases

- **Empty audit trail** (fresh checkout): every view is shown, empty, with the command that would fill it (`invoke demo.up`, `snapl deploy`, `snapl scan`).
- **Audit trail file missing or unreadable**: the audit-backed views say so and give the configured path. The dashboard does not create an empty file.
- **A device in the audit trail that is no longer in the Source of Truth**: it is left out of the device view and kept in the runs view and history, labelled as no longer in intent.
- **A device added to the Source of Truth and never deployed**: shown, as never deployed and never scanned.
- **No worker polling**: runs started from the CLI sit queued. The dashboard says no worker is running (same check as the CLI) rather than showing those runs as running.
- **Very long audit trail**: views stay responsive. Lists are bounded, history is aggregated on the server, and the page never receives the whole trail.
- **Clock skew between the audit trail and the workflow engine**: times are shown as recorded by each source, in UTC on the wire, and the dashboard does not reconcile them.
- **Port already in use**: the dashboard refuses to start and names the port and the variable that changes it. It never falls back to another port (#107: "it responds" is not "it is ours").
- **Several dashboard tabs open**: allowed. Each tab refreshing does not multiply load on CI beyond the cache (FR-024).
- **A recorded lab run file that is corrupt or from a future format**: that run is listed as unreadable. Other runs still show.

## Requirements *(mandatory)*

### Functional Requirements

#### Server and contract

- **FR-001**: `snapl dashboard` MUST start a local server for one use case (default `dcfabric`) on `DASHBOARD_PORT`, default **18030**, recorded in the host resource registry.
- **FR-002**: The server MUST bind to the loopback interface by default. Binding elsewhere MUST need an explicit option, and the server MUST print a warning that the dashboard has no authentication.
- **FR-003**: The server MUST be read-only. No request may write to a device, the Source of Truth, the workflow engine, the audit trail or the lab run records. Only the GET method is served.
- **FR-004**: Every response MUST follow a typed, versioned contract documented in this spec's `contracts/`. A breaking change MUST change the version.
- **FR-005**: Every view in a response MUST carry its source, whether that source was available, the time the data was fetched and, when unavailable, a reason and a remedy command.
- **FR-006**: The server MUST read the audit trail only through the Orchestrator's `AuditLog` contract. It MUST NOT query the audit trail's storage directly.
- **FR-007**: The server MUST answer every request even when every source is unavailable.

#### MVP page

- **FR-008**: The same command MUST serve one plain page that renders every view in the contract as tables, with no build step and no external network requests from the browser.
- **FR-009**: The page MUST refresh by polling every 5 seconds, and MUST show each view's data age.
- **FR-010**: Wherever the dashboard suggests an action, the page MUST show the `snapl` or `invoke` command to run. It MUST NOT offer a control that performs it.

#### Devices (US1)

- **FR-011**: The device view MUST list every device of the use case in the Source of Truth, with its name and role.
- **FR-012**: Each device MUST show one drift state from its most recent scan: `in_sync`, `missing` (intent not on the device), `undesired` (on the device, not in intent), `missing_and_undesired`, `error` (with reason), or `never_scanned`. A scan recorded before this feature carries no direction for its paths. It MUST be shown as `drifted`, with its paths and no breakdown, instead of guessing.
- **FR-012a**: The scan workflow's per-device audit record MUST also record which of its paths are undesired. This is an additive payload field. Existing fields and the workflow's signature are unchanged.
- **FR-013**: Each device MUST show the time of its most recent scan and the outcome, reason and time of its most recent deploy, or that there has been none.
- **FR-014**: Each drifted device MUST list the differing paths from its most recent scan, separating missing from undesired paths.
- **FR-015**: Before showing the device view, the server MUST check the Source of Truth's identity the same way spec 009 FR-031 does, and refuse a foreign one.

#### Runs and schedule (US2)

- **FR-016**: The runs view MUST list the most recent workflow runs (default 50, bounded at 500), newest first. Each run shows workflow type, target device or use case, start time, end time or "running", duration, outcome, reason, and trigger: the schedule, or an operator. The operator is named where the audit trail records one. Schedule changes record their actor today. Operator-started deploys, scans and reconciles do not, and naming them is out of scope.
- **FR-017**: Each run MUST carry a link to the run in the Temporal Web UI, built from the configured Web UI address.
- **FR-018**: Runs still in progress MUST come from the workflow engine. Completed runs MUST come from the audit trail.
- **FR-019**: The schedule view MUST show, for the use case: whether a schedule exists, its response mode, interval, paused state with reason and actor, last run time and outcome, and next run time.
- **FR-020**: The schedule view MUST flag any device whose scheduled repair has failed on **three or more** consecutive scheduled runs, with the count and the command that switches the schedule to report mode.
- **FR-021**: The dashboard MUST report whether a worker is polling the task queue, using the same check as the CLI.

#### History (US3)

- **FR-022**: The history view MUST provide, for a requested time range and bucket size, series derived only from the audit trail:
  - drifted devices per scan
  - deploys per bucket by outcome
  - repair attempts per bucket by outcome
  - undesired path count per scan
  It MUST mark reconcile and remediation runs as events on the timeline.
- **FR-023**: A range with no scans MUST be returned as no data, not as zero. The history view MUST accept any range the audit trail covers and MUST NOT drop events by age.

#### CI and live lab evidence (US4)

- **FR-024**: The CI view MUST show the quality gate result and each of its jobs for `main` and for each open pull request, with links to the runs. It MUST also show the latest unit-test coverage CI reported. CI answers MUST be cached on the server for at least 120 seconds, so polling never exceeds the unauthenticated rate limit.
- **FR-025**: The CI view MUST work without credentials for the public repository, and MUST use a token from the environment when one is set.
- **FR-026**: `invoke test-e2e`, and a new `invoke test-integration` that runs `tests/integration/` against the live stack, MUST record each run in a git-ignored folder under `.snapl/test-runs/`:
  - the test results in JUnit XML
  - a metadata file with: commit, branch, dirty-tree flag, device image version, start and end time, suites run, and exit status
- **FR-027**: A run whose metadata says it started but never finished MUST be shown as incomplete.
- **FR-028**: The lab evidence view MUST show the latest run per suite, its result and age, and the number of commits on `main` since that run's commit, or "unknown" with a reason.
- **FR-029**: `invoke demo.reset` MUST NOT remove recorded lab runs.

#### Failure and freshness (US5)

- **FR-030**: When a source is unavailable, only the views that depend on it MUST report it. Each MUST give a reason and a remedy command.
- **FR-031**: The dashboard MUST NOT show cached data as current. Cached data MUST be labelled with its age.

### Key Entities

- **Source status**: one data source (audit trail, workflow engine, Source of Truth, CI, lab run records): whether it answered, when it was asked, and the reason and remedy if it did not.
- **Device view**: one device: name, role, drift state, missing and undesired paths from its latest scan, latest scan time, latest deploy outcome, reason and time.
- **Workflow run view**: one workflow run: type, target, start, end, duration, outcome, reason, trigger, Web UI link.
- **Schedule view**: the use case's drift schedule now, plus any repeated failed repairs. It reuses the Orchestrator's existing schedule view model.
- **History series**: a named series over a range and bucket: points with a time and a value, or no data, plus event markers.
- **CI status**: for `main` or one pull request: quality gate result, each job's result, run link, coverage, fetched time.
- **Lab run**: one recorded live run: suite, result counts, start and end, commit, branch, dirty flag, device image version, completeness.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: During the full demo arc (deploy, induced drift, scan, reconcile, scan), every step appears on the dashboard within 10 seconds of the audit trail recording it, without the operator typing a command.
- **SC-002**: At any moment, the device and schedule views agree with `snapl status --json` and `snapl schedule show --json` run at the same time. A test checks the two against the same fixture.
- **SC-003**: With each source stopped in turn, the page still loads within 2 seconds, every other view is unchanged, and the stopped source's views name it and give a remedy.
- **SC-004**: An audit trail spanning six months, with a month-long gap, produces correct history series for the full range, and the gap shows as no data.
- **SC-005**: After a recorded live run and three further commits on `main`, the lab evidence view shows the run's result and "3 commits on main since the last live run".
- **SC-006**: A test drives every endpoint with every method and verifies that no write reaches any source and that every method except GET is refused.
- **SC-007**: With an audit trail of 100,000 events, every view answers within 1 second on the maintainer's laptop.
- **SC-008**: An operator can answer "is the fabric in sync, what did the schedule do overnight, and is the lab evidence current?" from the dashboard alone in under 30 seconds.

## Assumptions

- One viewer on the maintainer's own machine. No authentication, no remote access (FR-002), no multi-user concerns.
- `dcfabric` is the only implemented use case. The use case is a parameter so the site edge (#117) needs no contract change.
- The repository is public, so CI status can be read without credentials at a low rate limit.
- `git` is available where the dashboard runs, for the commits-since count.
- The failed-repair threshold (3) and the runs list default (50) are assumptions taken from the design session, not clarifications.
- Port 18030 was reserved for Grafana (#101). Grafana is dropped (decided 2026-11-07), so the dashboard takes 18030.
- The audit trail file already outlives `demo.reset`, which removes only Docker volumes. This feature relies on that and does not change it.

## Out of Scope

- **The designed UI**: topology view, charts, visual design, snapshot export, opening an archive read-only. These are spec 011, built on this contract.
- **Measured metrics**: Prometheus, workflow engine metrics, gNMI timing, size-capped retention, `history.archive`, `history.purge`. These are spec 012.
- **Any action from the dashboard**: deploy, reconcile, schedule changes.
- **Authentication, remote access and alerting.**
- **Operational expectations** (BGP sessions up, interfaces oper-up). They are not checked by snapl yet, so the dashboard does not show them.

## Dependencies

- Spec 005: the `AuditLog` contract and the audit event model.
- Spec 006: CLI conventions, the worker-polling check, settings and exit codes.
- Spec 007: undesired-config drift.
- Spec 009: schedules, the schedule view model, the Source of Truth identity check (FR-031) and the repeated failed repair behaviour (#136).
