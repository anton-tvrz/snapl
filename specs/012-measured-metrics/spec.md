# Feature Specification: Measured Metrics and Long-Lived History

**Feature Branch**: `012-measured-metrics`
**Created**: 2026-11-08
**Status**: Draft
**Input**: Issue #147 (part of #101), ADR-0005 Decision 3, spec 010's dashboard and contract
**Scope settled**: 2026-11-07 (#147). Four open points were settled with the maintainer on 2026-11-08:
- metric panels go on 010's page now;
- the archive carries a minimal JSON export of the dashboard API;
- the dashboard exports source health;
- test fixtures are real Prometheus captures.

## Context

Spec 010 made the loop visible. Every view it shows is derived from the audit trail, which records *what* happened and when. It doesn't record *how long* things took, or how the worker itself is doing:

- **Nobody can see how long the loop's work takes.** The audit trail gives a run's start and end, but not the gNMI call inside it. Is `leaf-03` slower to configure than the others? Did collection get slower after the SR Linux upgrade? Those questions have no answer today.
- **A struggling worker is invisible until it stops.** Temporal's SDK already measures task lag, activity latency and poller counts. snapl just doesn't expose them.
- **A source outage leaves no trace.** The dashboard shows that Temporal is down *now*. Once it is back up, nothing remembers that it was down for an hour last night.
- **History doesn't survive a reset or a long break.** `demo.reset` removes Docker volumes. A time-based retention limit would delete everything on the first start after a few weeks away from the project.

This spec adds measured metrics. It is the last of the three specs #101 was split into (010 is the read API and plain page, 011 is the designed UI). It is usable without 011: its panels go on 010's plain page.

### What this feature must never do

- **No writes.** It never writes to a device, the Source of Truth or the audit trail. Metrics are recorded in the worker's process and read by Prometheus. The dashboard stays read-only (010 FR-001).
- **No new dependencies for executor and collector.** `snapl-executor` and `snapl-collector` take no metrics dependency (ADR-0005). Timing is measured around them, in the orchestrator's activities.
- **No arbitrary queries.** The dashboard never runs a query its caller supplies.
- **No accidental deletion.** History is never deleted by any command except an explicitly confirmed purge.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — See How Long the Loop's Work Takes (Priority: P1) 🎯 MVP

An operator opens the dashboard and sees, for a chosen time range:
- how long deploys, scans and reconciles took;
- how long each device's gNMI calls took, by operation (configure or collect);
- how often those calls failed.

The numbers are measured as the work happens, not inferred from audit timestamps.

**Why this priority**: This is the information nothing else in snapl has. It also builds the whole chain the other stories reuse, from measurement in the worker, through the metrics store and the dashboard's metrics API, to a panel.

**Independent Test**:
- Run a workflow on a local test server with the worker's metrics enabled, and check that the gNMI timing for that device appears on the worker's metrics endpoint with the agreed labels.
- Separately, against captured metrics-store responses, request each metrics view from the dashboard and check its series.

**Acceptance Scenarios**:

1. **Given** the worker is running with metrics enabled, **When** a deploy configures `leaf-01`, **Then** the worker's metrics endpoint carries a configure-call duration for `leaf-01` with its outcome.
2. **Given** a scan collected six devices, **When** the operator looks at the gNMI panel, **Then** each device's collect duration is shown for the range.
3. **Given** a gNMI call failed, **When** the operator looks at the error panel, **Then** that device's failure rate is above zero for the period.
4. **Given** the operator picks the last 7 days, **When** the panels load, **Then** they cover that range at a resolution the page can draw (no more points than the metrics store allows).

---

### User Story 2 — Keep History Across Resets and Long Breaks (Priority: P1)

The maintainer resets the lab for a clean rehearsal, or comes back to the project after two months. The metrics recorded before are still there.

**Why this priority**: History that disappears on `demo.reset` or after a break defeats the point of recording it. That decision was settled in #147 and ADR-0005, and it shapes where the data lives.

**Independent Test**: Unit-test that the reset and teardown tasks never touch the metrics directory, and that the metrics store is configured with size-only retention. The live part (reset, bring up, query old data) is a lab task.

**Acceptance Scenarios**:

1. **Given** metrics recorded yesterday, **When** the maintainer runs `invoke demo.reset` then `invoke demo.up`, **Then** yesterday's metrics are still queryable.
2. **Given** the metrics store was stopped for 60 days, **When** it starts again, **Then** no data is deleted for being old. Data is only ever dropped to stay under the size cap.
3. **Given** the maintainer wants a clean slate, **When** they run `invoke history.purge`, **Then** they are asked to confirm, and nothing is deleted unless they do.

---

### User Story 3 — Notice a Struggling Worker and Past Outages (Priority: P2)

The operator sees whether the worker is keeping up (how long tasks wait before a worker picks them up) and whether it is being scraped at all. They also see which of the dashboard's sources were down over the range, not only right now.

**Why this priority**: These are early warnings. They matter, but they matter after the loop's own timing (US1) is visible.

**Independent Test**: Against captured responses, request the worker-lag and source-health views and check them. Unit-test that the dashboard's own metrics endpoint reports each source's last fetch as up or down.

**Acceptance Scenarios**:

1. **Given** the worker was slow to pick up tasks for ten minutes, **When** the operator looks at the worker panel, **Then** the lag is visibly higher over those ten minutes.
2. **Given** Temporal was down from 02:00 to 02:20, **When** the operator looks at source health the next morning, **Then** Temporal shows as down over that interval.
3. **Given** the worker isn't running, **When** the operator looks at the worker panel, **Then** it says the worker isn't being scraped, not "no lag".

---

### User Story 4 — Take the History With You (Priority: P2)

The maintainer runs `invoke history.archive` and gets one dated file with everything learned so far:
- the audit trail;
- a consistent snapshot of the metrics;
- the recorded live lab runs;
- a JSON export of the dashboard's views.

They can keep it, or open it somewhere else later.

**Why this priority**: The archive turns history into something that outlives the machine. Opening an archive in the dashboard is spec 011's.

**Independent Test**: With the metrics store's admin API and the dashboard faked, run the archive task into a temp directory and check the file's contents and its manifest.

**Acceptance Scenarios**:

1. **Given** the stack and the dashboard are running, **When** the maintainer runs `invoke history.archive`, **Then** one `snapl-history-<date>.tar.gz` file is written. It holds the audit DB, a metrics snapshot, the lab test runs, the dashboard export, and a manifest listing all of them.
2. **Given** the metrics store is down, **When** the archive runs, **Then** it still writes everything else. The manifest marks the metrics snapshot as missing with the reason, and the command exits non-zero so the gap is noticed.
3. **Given** `SNAPL_ARCHIVE_DIR` is set, **When** the archive runs, **Then** the file goes there.

---

### User Story 5 — Metrics Failing Is Data, Not an Outage (Priority: P3)

When the metrics store is down, the metric panels say so, with the command to fix it, and the rest of the dashboard is unaffected. When the worker's metrics port is already taken, the worker still starts and runs workflows, just without metrics, and says so in its log.

**Why this priority**: These are robustness guarantees. 010's rule is that failure is data (010 FR-007), and metrics must not become a new way for the loop to stop.

**Independent Test**: With the metrics store unreachable, the metrics views return 200 with `available: false`, a reason and a remedy. With the metrics port occupied, the worker's startup falls back to no metrics and logs a warning.

**Acceptance Scenarios**:

1. **Given** the metrics store is stopped, **When** the dashboard is opened, **Then** every metric panel says "metrics unavailable" with `uv run invoke dev.deps`. The other views load as before.
2. **Given** a worker already holds the metrics port, **When** a second worker starts (for example a branch worker on its own task queue), **Then** the second worker runs workflows and logs that its metrics are off.

---

### Edge Cases

- **Empty range.** A range with no data returns an empty series with a note, not an error.
- **Too many points.** A range too long for the step is answered with a coarser step, not with the metrics store's error.
- **Unknown metrics view.** Asking for a metrics view that doesn't exist returns 404, like any unknown route.
- **Worker restarts.** Counters reset when the worker restarts. Rates and quantiles are computed with functions that handle resets.
- **A device is renamed or removed.** Its old series stay in history under the old name. Nothing is rewritten.
- **The size cap is reached.** The oldest data is dropped first. The dashboard says when the oldest metric data starts, like 010 does for the audit trail.
- **Archive while the dashboard is down.** The export part is marked missing in the manifest. The audit DB, the metrics snapshot and the lab runs are still archived.
- **Purge while the metrics store is running.** The purge refuses and says to stop it first (`uv run invoke dev.stop`), so it never deletes files under a running store.

## Requirements *(mandatory)*

### Functional Requirements

**Recording (worker)**

- **FR-001**: The worker MUST expose the workflow engine's built-in metrics on a metrics endpoint bound to loopback, on `WORKER_METRICS_PORT` (default 18091).
- **FR-002**: If the metrics endpoint can't be opened, for example because the port is in use, the worker MUST log a warning naming the port and the variable that moves it, and MUST run without metrics. Metrics MUST never stop the worker.
- **FR-003**: The worker MUST record the duration of every gNMI configure call and every gNMI collect call, measured in the orchestrator activities that make them.
  - Each measurement carries `device`, `use_case`, `operation` (`configure` or `collect`) and `outcome` (`ok` or `error`), and no other labels of its own.
  - It never carries a per-path label.
- **FR-004**: A gNMI call that raises MUST still be recorded, with `outcome=error`, and the exception MUST propagate unchanged.
- **FR-005**: A call that returns a failure result without raising MUST be recorded with `outcome=error`. Today `ApplyResult.success` is false in that case.
- **FR-006**: `snapl-executor` and `snapl-collector` MUST gain no dependency, and no ABC or workflow signature MUST change.

**Storing**

- **FR-007**: The dev stack MUST run a metrics store on `PROMETHEUS_PORT` (default 18090), pinned to an exact image version. It scrapes the worker and the dashboard every 15 seconds.
- **FR-008**: Retention MUST be by size only: 2 GB by default, overridable, with no time limit.
- **FR-009**: The metrics store's data MUST live in a git-ignored host directory, `./.snapl/prometheus/`, not in a Docker volume. `demo.reset` and `dev.down --volumes` MUST leave it untouched.
- **FR-010**: `invoke history.purge` MUST be the only command that deletes metrics data.
  - It asks for confirmation interactively.
  - It requires `--yes` when not interactive.
  - It refuses while the metrics store is running.

**Reading (dashboard)**

- **FR-011**: The dashboard MUST serve named metrics views under `/api/v1/metrics/<name>`. Each one is a fixed query with `start`, `end` and `step` parameters.
  - The views are: `workflow-duration`, `gnmi-duration`, `gnmi-errors`, `worker-lag`, `worker-up` and `source-health`.
  - It MUST NOT accept a query string from the caller.
- **FR-012**: Each metrics response MUST use 010's envelope and view rules (FR-005, FR-007). When the metrics store is down, the view is `available: false` with a reason and the remedy `uv run invoke dev.deps`, inside a 200.
- **FR-013**: A requested range that would exceed the metrics store's point limit MUST be answered at a coarser step, and the response MUST say which step it used.
- **FR-014**: The dashboard MUST expose its own metrics endpoint at `/metrics`. For each 010 source it reports whether the last fetch succeeded and how long it took.
- **FR-015**: The page MUST show metric panels for the views in FR-011 over a selectable range: the last hour, 24 hours or 7 days. Each panel shows its unavailability with the reason and remedy, and renders values as text only (010 R11).
- **FR-016**: The `/sources` view MUST include the metrics store as a source.

**Archiving**

- **FR-017**: `invoke history.archive` MUST write `snapl-history-<UTC timestamp>.tar.gz` to `SNAPL_ARCHIVE_DIR` (default `./.snapl/archives/`). It contains:
  - a consistent copy of the audit DB;
  - a metrics snapshot taken through the store's admin API;
  - `.snapl/test-runs/`;
  - a JSON export of the dashboard's overview, history, links, sources and every metrics view for the last 7 days;
  - a `manifest.json` listing each part, its source and whether it is present.
- **FR-018**: A part that can't be collected MUST be marked missing in the manifest with its reason. The other parts are still written, and the command MUST exit non-zero.
- **FR-019**: The archive and purge tasks MUST NOT delete or modify the live audit DB, the live metrics data or the test runs, except when the purge is confirmed.

**Docs and governance**

- **FR-020**: ADR-0005 Decision 3 MUST record the metrics store's version and the facts this spec measured, and move to Accepted once the live evidence is in.
- **FR-021**: The constitution's Technology Stack MUST list the dashboard server (010) and the metrics store (012), as a minor version bump, once this spec's code has merged.

### Key Entities

- **Metric measurement**: one gNMI call's duration with `device`, `use_case`, `operation` and `outcome`. It is recorded in the worker and stored by the metrics store.
- **Metrics view**: a named, fixed query over a range, returned as series of `(time, value)` points with their labels, inside a 010 view.
- **Source health**: per dashboard source, whether its last fetch succeeded and how long it took, exported by the dashboard and stored over time.
- **History archive**: one dated file with the audit DB, a metrics snapshot, the lab test runs, a dashboard export and a manifest.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A gNMI call's duration appears on the dashboard's gNMI panel within 60 seconds of the call ending.
- **SC-002**: Metrics recorded before `demo.reset` are still queryable after `demo.up`, with no step beyond those two commands.
- **SC-003**: With the metrics store stopped, the page loads within 2 seconds (010 SC-003) and every metric panel shows a reason and a remedy.
- **SC-004**: With one worker holding the metrics port, a second worker starts and completes a workflow.
- **SC-005**: An archive of a lab with at least one day of data contains all five parts. Each part opens with standard tools: `tar`, `sqlite3`, the metrics store pointed at the snapshot directory, and a JSON parser.
- **SC-006**: The number of gNMI series per device is at most 4 (2 operations × 2 outcomes) for each histogram bucket. No series carries a config path.
- **SC-007**: `snapl-executor` and `snapl-collector` have the same dependency lists before and after this feature.

## Assumptions

- The worker runs on the host and the metrics store in Compose. The store reaches the worker through `host.docker.internal`, and a loopback-only bind is enough on macOS with OrbStack (measured 2026-11-08, research R2). Linux Docker hosts need extra setup, which this spec documents but doesn't automate.
- The range choices on the page (1 h, 24 h, 7 d) are enough for 010's plain page. Spec 011 adds a free range picker.
- 15 s scrapes and a 2 GB cap hold many months of data for six devices. The cap is a safety limit, not a target.
- Durations are reported in milliseconds, as the workflow engine's runtime records them.

## Out of Scope

- **Grafana or any second UI** (ADR-0005).
- **Alerting.** Nothing pages anyone.
- **Opening an archive in the dashboard.** That is spec 011 (`snapl dashboard --archive`).
- **Metrics for Infrahub or the Docker stack itself.**
- **Per-path or per-config-item metrics.**
- **Any change to what the audit trail records.**

## Dependencies

- Spec 010: the dashboard server, envelope, view rules, OpenAPI snapshot, page, and `.snapl/` directory.
- ADR-0005 Decision 3; the port registry's 18090 and 18091.
- The workflow engine's SDK runtime metrics (temporalio 1.25).
