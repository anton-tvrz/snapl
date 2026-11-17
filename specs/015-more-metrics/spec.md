# Feature Specification: More Metrics

**Feature Branch**: `overnight/015-spec-more-metrics`
**Created**: 2026-11-15
**Status**: Draft
**Input**: Issue #249 (part of #101), specs 010, 011, 012 and 014, and a review of the live stack on 2026-11-15
**Scope settled**: 2026-11-15. The maintainer asked for as many metrics as snapl can capture and accepted the review's tiers. The points this spec decided without asking are listed under Assumptions.

## Context

Spec 012 gave snapl measured metrics: six views over one snapl histogram and four of the Temporal SDK's families. The review on 2026-11-15 found that much more is already measured and never shown, and that some of the most useful figures need no metrics store at all:

- The worker exports 23 metric families. The dashboard draws 3 of them, plus `up` and its own source gauge.
- The dashboard exports each source's fetch time, and no view shows it.
- The audit trail holds every scan and every run, so "how many devices were in sync at 14:00" and "how many runs failed today" can be answered from it, in an archive too.
- Every drawn duration is a 95th percentile, which is only as fine as its histogram bucket. On the lab the gNMI panel read 480 ms while the mean was 212 to 275 ms (#192).
- A scheduled scan records no steps, so the loop strip has nothing to show for the most common run.

This feature shows what is already measured, adds figures from the audit trail, adds three counters to the worker, and records a scan's steps. All of it is about snapl's own loop. It does not measure the network's traffic.

### What this feature must never do

- Put a config path, an error message, or any unbounded value in a metric label (spec 012 SC-006).
- Let a view take a query from the request. Views stay named, as in spec 012.
- Break the v1 contract. Every change is an addition.
- Present an estimate as a measurement. A percentile says it is one; a mean says it is one; the repair duration stays "scan to scan" and is never called time to detect.
- Make a metrics failure stop the worker or the dashboard (spec 012 FR-002, 010 FR-007).
- Touch the lab during an unattended run.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — See What Is Already Measured (Priority: P1) 🎯 MVP

The operator opens the metrics section and finds views for everything the worker and the dashboard already export: mean durations beside the percentiles, how many workflows completed and of which kind, how long each activity takes, how busy the worker's slots are, how long the worker's requests to Temporal take, and how long each dashboard source takes to answer.

**Why this priority**: It needs no change to the worker or the lab. Every one of these numbers is in the metrics store today.

**Independent Test**: With the metrics store's responses captured or built in its format, request each new view and check its series, unit and labels. Check that an unknown view is still a 404 and that the store's failure is still a view with a reason.

**Acceptance Scenarios**:

1. **Given** the worker has run scans for an hour, **When** the operator opens "Activity time", **Then** there is one series per activity (fetch intent, collect, detect drift, record audit, apply config) with its mean duration.
2. **Given** twelve scans and one deploy completed in the range, **When** the operator opens "Workflows completed", **Then** the counts by workflow type add up to thirteen.
3. **Given** the gNMI percentile view and the gNMI mean view, **When** the operator compares them, **Then** each panel says which it is.
4. **Given** the metrics store is down, **When** any new view is requested, **Then** the answer is unavailable with a reason and a remedy, as for the existing views.
5. **Given** an archive made after this feature, **When** it is opened, **Then** the new views are shown over the exported range. **Given** an older archive, **Then** each says it is not in this archive.

---

### User Story 2 — The Fabric's History in Numbers, From the Audit Trail (Priority: P1)

The operator sees, over any range: how many devices were in sync at each scan, how many drifts were open, how many runs there were by trigger and outcome, how long each repair took scan to scan, and which devices drifted most. None of it needs the metrics store, so it works in an archive and in a recording.

**Why this priority**: These are the numbers a reader of the demo asks for, and the audit trail has held them all along.

**Independent Test**: From the captured audit trail, request the history with the new series and the drift summary, and check them against the known arc. Check a range before the first event and a range with no scan.

**Acceptance Scenarios**:

1. **Given** six devices and one drifted between two scans, **When** the operator reads "Devices in sync", **Then** it shows five at the first scan and six after the repair.
2. **Given** 40 scheduled scans, 2 operator deploys and 1 failed repair in 24 hours, **When** the page loads, **Then** the number row's fourth card reads the runs in 24 hours with the failures named.
3. **Given** three repaired drifts in the range, **When** the operator opens "Repair duration", **Then** there are three points, each scan to scan, each naming its device and what repaired it.
4. **Given** leaf-01 drifted three times and spine-01 once, **When** the operator opens "Drift by device", **Then** leaf-01 is first, with its episode count and its total drifted time.
5. **Given** a range with no scan, **When** any of these is requested, **Then** it has no points and says so. It does not show zero.

---

### User Story 3 — Count What the Loop Finds and Does (Priority: P2)

The worker counts each scan's result per device, the differing paths it found by kind, and each deploy's outcome. The operator sees them as rates and totals over time, next to the timing views.

**Why this priority**: These are the loop's own events as counters, which makes them alertable and comparable with the timings. They change the worker, so they come after the stories that don't.

**Independent Test**: Run each activity and workflow against a local test server with the metrics runtime on, and read the worker's metrics endpoint. One test holds each counter's label set.

**Acceptance Scenarios**:

1. **Given** a scan finds leaf-01 with one undesired path, **When** the worker's metrics are read, **Then** the scan counter for leaf-01 in state `undesired` is one higher and the path counter for kind `undesired` is one higher.
2. **Given** a deploy fails with reason `verification_failed`, **When** the worker's metrics are read, **Then** the deploy counter for that device, outcome `failed` and that reason is one higher.
3. **Given** the worker runs with metrics off, **When** a scan runs, **Then** it succeeds and records nothing.
4. **Given** any of these counters, **When** its series are listed, **Then** no label holds a path, a message or an id that grows without bound.

---

### User Story 4 — A Scheduled Run Shows Its Steps (Priority: P2)

A scan and a drift response record what each block did: intent read, device collected, drift evaluated. The loop strip then shows a scheduled run's blocks as done or failed, with times, and no longer as "not recorded".

**Why this priority**: The strip carries the claim that snapl is six blocks, and the most common run shows none of them.

**Independent Test**: Run a scan against the local test server with fakes for the devices, read the audit trail, and project the run's steps.

**Acceptance Scenarios**:

1. **Given** a scan of six devices that all answer, **When** its steps are read, **Then** Intent, Collector and Observability are done, with the time of the last step of each, and Executor is not reached.
2. **Given** a scan where one device can't be collected, **When** its steps are read, **Then** Collector is failed with that device's reason.
3. **Given** a drift response that found nothing, **When** its steps are read, **Then** it shows its scan's blocks and Executor as not reached.
4. **Given** a scan recorded before this feature, **When** its steps are read, **Then** it still says its steps are not recorded.

---

### User Story 5 — More Views Stay Readable and Cheap (Priority: P3)

The metrics section is grouped (the loop's timing, what the loop did, the worker, the dashboard), each group under its label. A view with many series draws its largest and says how many more are in its table. The snapshot file and the archive stay small.

**Why this priority**: Twenty views are only useful if the page stays readable and the files stay portable.

**Independent Test**: Render the section from fixtures and check the groups and their order. Measure the snapshot on the test fixtures.

**Acceptance Scenarios**:

1. **Given** the metrics section, **When** it renders, **Then** every view is in exactly one named group.
2. **Given** a view with twelve series, **When** it renders, **Then** it draws six, says twelve are in the table, and the table holds all twelve.
3. **Given** a seven-day snapshot, **When** it is written, **Then** its size is within the limit in SC-006.

---

### Edge Cases

- A counter's first event: the view must show it (#191). If PR #241 has merged, its helper is used; if not, the view carries the same known gap and says so in the spec's dependency note.
- A family that only exists after a first failure (the SDK's failure counters): its view says "no data", not zero.
- A worker restart: counters reset. Rates and increases handle it; totals are never drawn as raw counter values.
- Devices removed from intent: the audit-trail figures count devices in intent at the time shown, by the same rule the devices view uses.
- A range before the audit trail starts, or longer than the limits spec 010 and 011 set.
- An older worker (no new counters) with a newer dashboard: the new views say "no data".
- An older archive with a newer dashboard, and a newer archive with an older dashboard (unknown views are ignored by it).

---

## Requirements *(mandatory)*

### Functional Requirements

**Views over what is already exported**

- **FR-001**: The metrics API MUST add these views: workflow mean duration, gNMI mean duration, workflows completed, activity time, worker slots, server request time, and source fetch time. Their names, queries, units and labels are in contracts/metrics-views.md.
- **FR-002**: The metrics unit MUST gain `count`, for a number of events per step. Existing units keep their meaning.
- **FR-003**: Every new view MUST follow spec 012's rules: a named view, the namespace and use case from settings only, the 15 s cache, the step raised when needed, failure as an unavailable view.
- **FR-004**: A view's title and unit line MUST say whether it is a mean, a 95th percentile, a count per step or a current value.

**Figures from the audit trail**

- **FR-005**: The history API MUST add these series: devices in sync (per scan), open drifts (per scan), runs (per bucket, by trigger and outcome), and repair duration (one point per repaired drift, in seconds, scan to scan).
- **FR-006**: A new read-only route MUST return the drift summary for a range: per device, its episodes, how many are open, its total drifted time scan to scan, and the most paths it had missing and undesired.
- **FR-007**: These figures MUST come from the audit trail only, MUST honour the "as of" time, and MUST work in archive and snapshot modes.
- **FR-008**: A range with no scan MUST give no points. It MUST NOT give zeros.
- **FR-009**: The number row's fourth card MUST show the runs in the last 24 hours with the failures, replacing spec 014's deploys and repairs.

**New counters in the worker**

- **FR-010**: The worker MUST count scan results by device, use case and drift state.
- **FR-011**: The worker MUST count the differing paths each scan finds by device, use case and kind (`missing`, `undesired`, `unclassified`).
- **FR-012**: The worker MUST count deploy ends by device, use case, outcome and reason, where the reason is one of the workflow's own reason values or `none`.
- **FR-013**: No other label is allowed on these counters. One test per counter MUST hold its label set, and the values of state, kind, outcome and reason MUST come from enums.
- **FR-014**: Recording MUST never raise into the activity or workflow, and MUST be a no-op when metrics are off.
- **FR-015**: The metrics API MUST add views for the three counters, as counts per step.

**Scan steps**

- **FR-016**: A scan MUST record in the audit trail a step for reading intent, a step per device for collecting, and its existing step per device for evaluating drift, each with its outcome and, when it failed, its reason.
- **FR-017**: The steps projection MUST map a scan's steps to the Intent, Collector and Observability blocks, and a drift response's steps to its scan's blocks plus its repairs.
- **FR-018**: A run recorded before this feature MUST still be shown as not recorded.
- **FR-019**: The extra audit events MUST NOT change any existing figure: the devices view, the drift timeline, the history and the runs list give the same answers on the same trail.

**The page**

- **FR-020**: Every new view and figure MUST have a panel on the designed page, in spec 014's frame and scale, with a text alternative.
- **FR-021**: The metrics section MUST be grouped under labels: the loop's timing, what the loop did, the worker, the dashboard.
- **FR-022**: The plain page MUST show the new metrics views, as it shows the existing ones.
- **FR-023**: Every new panel MUST work in live, archive and snapshot modes and at a past time, or say it shows only the present.

**Files and evidence**

- **FR-024**: `invoke history.archive` and `snapl dashboard --snapshot` MUST carry the new views, series and summary.
- **FR-025**: The fixture server MUST answer every new view and figure, so the page can be looked at with no lab.
- **FR-026**: The boundary changes (the worker's counters and the scan's audit events) MUST have live evidence from the lab before the feature is called done (constitution, Principle III).
- **FR-027**: The data model, the metrics contract, the README and the demo scenarios MUST describe the new views and say what each measures.

### Key Entities

- **Metrics view**: a name, a query template, a unit and its series labels (spec 012). This feature adds ten.
- **History series**: a name, a range, points and markers (spec 010). This feature adds four.
- **Drift summary**: per device, its drift episodes over a range.
- **Counter**: a name and a fixed label set, recorded by the worker.
- **Scan step**: an audit event with an activity name, a device or the use case, an outcome and a reason.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The dashboard shows at least 16 metrics views, up from 6, and every one states what it measures.
- **SC-002**: Every figure in User Story 2 is answered with the metrics store stopped.
- **SC-003**: Each new counter has at most the series its enums allow per device: 7 for scan results, 3 for paths, and outcome × reason for deploys. No series carries a path.
- **SC-004**: On the captured audit trail, every existing API answer is unchanged by the new scan events (FR-019), checked by the existing tests without loosening them.
- **SC-005**: A scheduled scan run after this feature shows at least three blocks as done on the loop strip.
- **SC-006**: A seven-day snapshot on the test fixtures grows by less than 60% over its size before this feature, and the page's first load makes no more than one request per view.
- **SC-007**: New Python and UI code keeps at least 80% line coverage, and CI's gates pass.
- **SC-008**: The metrics section's panels all appear in the look check's pictures with data, in light and dark.

---

## Assumptions

- **Telemetry from the devices is a later spec.** Interface counters, BGP sessions and CPU need paths captured from the lab first, and a rule for how many series a fabric may add.
- **Means are added beside the percentiles, not instead of them.** PR #241 fixes the buckets (#192). The mean is exact either way.
- **Activity time replaces separate timers for intent and audit.** The SDK already times every activity by type, so no new histogram is added for them.
- **Counts are per step.** A view of events shows how many happened in each step of the range, not a rate per second.
- **The runs card replaces the deploys-and-repairs card** once the runs series exists.
- **The scan's new audit events use the existing event types** with new activity names, so no reader of the trail breaks.
- **The label for a deploy's reason is bounded** because it is the workflow's reason enum.
- **Worker slices wait for PR #241.** It rewrites the worker's metrics module, the audit helper and the scan workflow. Building on it avoids redoing it.

## Out of Scope

- Device telemetry, alerting, Grafana, and any exporter beyond the two that exist.
- Scraping Temporal's server or Infrahub.
- Changing the existing six views' names or meaning.
- The fixes in PR #241.
- Durable storage changes for the audit trail.

## Dependencies

- Spec 014 complete: the frame, the scale, the number row and the metrics panel this feature fills.
- Spec 012's metrics API and spec 010's history API, which this feature extends.
- PR #241 merged, for the slices marked **After #241** in tasks.md.
- The lab, for the evidence tasks only.
