# Feature Specification: Designed Dashboard UI

**Feature Branch**: `011-dashboard-ui`
**Created**: 2026-11-09
**Status**: Draft
**Input**: Issue #146 (part of #101), ADR-0005 Decision 2, spec 010's read API and plain page, spec 012's metrics views and archive
**Scope settled**: 2026-11-07 (#146), revised with the maintainer on 2026-11-09 after a review against what 010 and 012 merged. The revision is recorded in #146.

## Context

Spec 010 made the loop's state readable, and spec 012 added measured timings. Both are shown on a deliberately plain page of tables, built to prove the data before any design work. The data is now proven, and the page has three problems the tables cannot fix:

- **The fabric is shown as a list.** A spine-leaf fabric is a shape. "leaf-02 has drifted on the interface facing spine-01" is one glance on a drawing and three table rows otherwise.
- **The loop is not visible as a loop.** snapl's claim is a closed loop built from six NAF blocks. The page shows the loop's results (a device is in sync, a run succeeded), never the loop itself: which block did what, in what order, for which device.
- **Nothing can be replayed or shown to someone else.** The demo arc (deploy, induced drift, detection, repair, clean scan) is over in a minute. Afterwards it survives only as rows in a runs table. Someone without the lab cannot see it at all.

This spec is the designed UI on 010's contract. It is the second of the three specs #101 was split into.

| spec | delivers |
| --- | --- |
| 010 (merged) | The local read-only dashboard server, its typed contract, and the plain page. |
| **011 (this spec)** | The designed UI: the fabric as a topology, the loop made visible, drift replayed over time, every 010 and 012 view as a designed panel, and two ways to look without the lab (an archive, and a single-file snapshot). |
| 012 (merged) | Measured metrics, long-lived history, `history.archive`. |

### What changed from the first scope

The first scope (#146, 2026-11-07) listed panels. The review of 2026-11-09 found that it could not be built on the contract as it stands, and that it had no story to tell:

- The topology needs links, and the contract has none. The Source of Truth does (each interface names its peer).
- "Click a device to see its history" needs per-device history. 010's history series are fabric-wide.
- A running-workflow marker fed only by the workflow engine misses any run shorter than the 5-second refresh.
- 012's metrics views, and the free time range it left to this spec, were not in the list.
- Snapshot export and the archive viewer were two features reading two formats. They are one format read two ways.

The technology choices (ADR-0005 Decision 2) belong to the plan. One of them changed in the review and is recorded in #146: the topology no longer uses a graph-canvas library.

### What this feature must never do

- **No actions.** The UI is read-only, as 010 decided. It shows the command to run and may copy it to the clipboard. It never runs it.
- **No requests to anywhere else.** The page loads nothing from another origin, in any mode. The lab's state does not leave the machine because a browser tab was opened.
- **No claim the data does not support (ADR-0004).** A loop step is shown as done only when the audit trail records it. A past state is shown only for sources that keep history. A duration is named for what it measures.
- **No runtime dependency on the build toolchain.** `snapl dashboard` works on a machine that has only the installed package.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — See the Fabric as a Fabric (Priority: P1) 🎯 MVP

An operator opens the dashboard and sees the fabric drawn as it is built: spines above, leaves below, the links between them. Each device shows its drift state at a glance. A device with work in progress, or work that just finished, is visibly active. A drifted path that names an interface marks the link on that interface. Selecting a device opens its detail: its differing paths (missing and undesired apart), its last scan and deploy, its recent runs, and the command that would reconcile it.

**Why this priority**: This replaces the device table, the first thing an operator looks at, and it carries everything else in this spec: the shell, the way the UI is built and shipped, and the fallback when it is not built.

**Independent Test**: Against the audit fixture captured from a live demo arc and a mocked Source of Truth with dcfabric's interfaces, open the page and verify six devices in two tiers, eight links, each device's state, the marked link for the drifted interface, and the detail of the drifted device.

**Acceptance Scenarios**:

1. **Given** dcfabric in the Source of Truth and a completed scan in which one leaf drifted, **When** the operator opens the dashboard, **Then** two spines and four leaves are drawn in their tiers with eight links, five devices are shown as in sync and one as drifted, and the drifted one is distinguishable without relying on colour.
2. **Given** a drifted path that names an interface with a peer, **When** the topology is shown, **Then** the link on that interface is marked as carrying drift.
3. **Given** a deploy that starts and ends between two refreshes, **When** the next refresh arrives, **Then** the device is shown as recently active, with the run's outcome.
4. **Given** the operator selects a device, **When** its detail opens, **Then** it lists missing and undesired paths separately, the last scan time, the last deploy outcome and reason, recent runs for that device, and the reconcile command with a copy control. For undesired-only drift it says that removal is not automatic (#65) and offers no command.
5. **Given** each of the seven drift states, **When** a device in that state is shown, **Then** each state has its own shape or mark as well as its own colour, in light and dark mode, and a legend names them.
6. **Given** the designed UI has not been built on this machine, **When** `snapl dashboard` is opened, **Then** 010's plain page is served in its place, with a notice naming the command that builds the designed UI.
7. **Given** the Source of Truth is unavailable, **When** the dashboard is opened, **Then** the devices known from the audit trail are shown without tiers or links, and the view says why and what to run.

---

### User Story 2 — Replay What Happened (Priority: P2)

An operator, or someone watching a demo, wants to see drift appear and be repaired. The dashboard shows one lane per device over time, coloured by the device's state at each scan, with the reconcile and remediation runs marked. A scrubber moves an "as of" time along it. As it moves, the topology and the device detail show the fabric as it was then. A headline states how long the latest drift lasted, from the scan that first saw it to the scan that first saw it gone, and what repaired it.

**Why this priority**: This is the demo. It turns a minute of terminal output into something that can be watched again, paused and pointed at. It is P2 because the topology of US1 is already useful live, and this builds on it.

**Independent Test**: Against the demo-arc audit fixture, verify each device's episodes (opened, closed, what closed it), verify the fabric's state at every scan boundary by setting the "as of" time, and verify the headline duration against the fixture's timestamps.

**Acceptance Scenarios**:

1. **Given** an induced drift on one leaf, a scan, a reconcile and a clean scan, **When** the operator views the lanes for that hour, **Then** that leaf's lane shows in sync, then drifted, then in sync, with the reconcile marked between them, and every other lane stays in sync.
2. **Given** the "as of" time is set between the drifted scan and the clean scan, **When** the topology is shown, **Then** that leaf is drawn as drifted with the paths that scan recorded, and the page states prominently that it is showing a past time.
3. **Given** the "as of" time is in the past, **When** a view has no history to show (the schedule, the worker, CI, running workflows), **Then** that view says it shows only the present. It does not show today's data as if it were the past.
4. **Given** a closed drift episode, **When** the headline is shown, **Then** it gives the time from the first scan that saw the drift to the first scan that saw the device clean, names what closed it (a scheduled remediation, an operator's reconcile, an operator's deploy, or unknown), and does not call it detection time.
5. **Given** a device whose drift was fixed with no reconcile or remediation run in between (by hand, or by a change of intent), **When** its episode is shown, **Then** what closed it is "unknown", not a guess.
6. **Given** a scan that could not collect a device during an open episode, **When** the lane is shown, **Then** that stretch is shown as an error and the episode stays open.
7. **Given** weeks with no scans, **When** the lanes cover them, **Then** that stretch is shown as no data, not as the last known state.
8. **Given** the operator returns to "now", **When** the page refreshes, **Then** every view is live again.

---

### User Story 3 — See the Loop Itself (Priority: P2)

For the latest run, or one the operator picks, the dashboard shows the six NAF blocks as a strip and marks which of them did their part: Intent supplied the desired state, the Executor applied it, the Collector read the device back, Observability compared the two. A failed step is shown on the block that failed, with its reason.

**Why this priority**: The six blocks are what snapl claims to be. This is the only view that shows them working rather than listing them. P2 because it explains the loop without changing what an operator can find out.

**Independent Test**: Against the demo-arc audit fixture, pick a successful deploy and a failed one and verify which blocks are marked done, failed and not reached, and that nothing is marked that the fixture does not record.

**Acceptance Scenarios**:

1. **Given** a completed deploy whose audit records show the desired state fetched, the config applied, the state collected and the drift check passed, **When** the run is shown on the strip, **Then** Intent, Executor, Collector and Observability are each marked done, in that order, with the time each was recorded.
2. **Given** a deploy whose apply step failed, **When** it is shown, **Then** Intent is marked done, the Executor is marked failed with the recorded reason, and the Collector and Observability are marked not reached.
3. **Given** a run in progress, **When** it is shown, **Then** only the steps the audit trail has recorded are marked. No step is shown as in progress unless a record says it started.
4. **Given** a run type whose steps the audit trail does not record one by one, **When** it is shown, **Then** the strip says so and shows the run's outcome only.

---

### User Story 4 — Everything the Plain Page Shows, Designed (Priority: P3)

Every view of 010 and 012 has a designed panel: the schedule with its repeated failed repairs, the workflow runs, the list of undesired config across the fabric, CI and live lab evidence, source status, the links, and the six metrics views. The metrics panels take any time range, not only the three presets. Each panel says where its data came from, how old it is, and what to run when its source is down.

**Why this priority**: Without it, an operator needs both pages. It is P3 because the plain page already answers these questions, and US1 to US3 answer new ones.

**Independent Test**: Against 010's and 012's fixtures, verify each panel shows what the plain page shows for the same fixture, then stop each source in turn and verify only its panels report it.

**Acceptance Scenarios**:

1. **Given** the same fixtures, **When** the designed page and the plain page are opened, **Then** every fact on the plain page is on the designed page.
2. **Given** a remediate-mode schedule whose repair of a device has failed three times running, **When** the dashboard is opened, **Then** the schedule panel names the device and count, the device is marked on the topology, and the command that switches to report mode is shown.
3. **Given** the operator picks a custom range for the metrics panels, **When** it is applied, **Then** every metrics panel shows that range, and a range the server refuses is reported with the server's reason.
4. **Given** a source is unavailable, **When** the dashboard is opened, **Then** only the panels that depend on it say so, each with the reason and remedy, and a stale answer is labelled with its age.
5. **Given** the operator selects a device, a range and an "as of" time, **When** they copy the page address and open it again, **Then** the same device, range and time are shown.
6. **Given** the dashboard tab is hidden, **When** time passes, **Then** it stops refreshing, and it refreshes at once when shown again.

---

### User Story 5 — Open an Archive Without the Lab (Priority: P4)

The maintainer has a `history.archive` file from three months ago, and nothing is running. `snapl dashboard --archive <file>` opens it. The same UI shows the fabric as it was when the archive was made, with the lanes, the scrubber, the runs and the lab evidence working over the whole archived history, and the metrics as they were exported. Nothing is contacted, and the archive is not changed.

**Why this priority**: 012 made history outlive the machine. This makes it readable again. P4 because it depends on everything above and serves an occasional need.

**Independent Test**: Build an archive from fixtures, open it with every service stopped and the network disabled, and verify the audit-backed views, the lab evidence, the exported metrics and the banner. Verify the archive file's checksum is unchanged afterwards.

**Acceptance Scenarios**:

1. **Given** a complete archive and no running stack, **When** it is opened, **Then** the topology, lanes, scrubber, runs and lab evidence work over the archive's full history, and a banner names the archive, when it was made, and the commit it was made at.
2. **Given** an archive is open, **When** ages are shown, **Then** they are relative to the archive's creation time, and the "as of" time starts there.
3. **Given** an archive is open, **When** the schedule, worker, running-workflow and CI panels are shown, **Then** each shows what the archive exported, labelled as of the archive's time, or says that an archive does not hold it. None is shown as a failed source with a remedy.
4. **Given** an archive is open, **When** the metrics panels are shown, **Then** they show the exported series over the exported range, and say that other ranges need the live stack.
5. **Given** an archive made before this feature, which has no topology in it, **When** it is opened, **Then** devices are shown without tiers or links, and the view says the archive predates the topology export.
6. **Given** an archive whose manifest names a missing part, **When** it is opened, **Then** the views that need that part say it is not in the archive, and the rest work.
7. **Given** a file that is not a snapl archive, or one from a newer format, **When** it is opened, **Then** the command refuses with a message that says which, and starts no server.

---

### User Story 6 — Hand Someone a Snapshot (Priority: P4)

The maintainer wants to show the loop to someone who will never run the lab. One command writes a single file. Opened from disk in a browser, with no server and no network, it shows the same topology, lanes, scrubber, loop strip and runs over the exported range, and says plainly that it is a recording.

**Why this priority**: This is what a demo post links to. P4 because it is a way of delivering US1 to US3, not a new view.

**Independent Test**: Write a snapshot from fixtures, open the file directly from disk with the network disabled, and verify the views, the scrubber, the recording notice, and that no request leaves the page.

**Acceptance Scenarios**:

1. **Given** a running dashboard, or an archive, **When** the maintainer runs the snapshot command, **Then** one file is written, and the command prints its path, its size, the time range it covers, and the kinds of data it contains.
2. **Given** the snapshot file, **When** it is opened from disk with no server and no network, **Then** the topology, lanes, scrubber, loop strip, device detail and runs work over its range.
3. **Given** a snapshot is open, **When** any view is shown, **Then** a notice says it is a recording, when it was made and from which commit, and no view is labelled live.
4. **Given** a snapshot is open, **When** a suggested command would be shown, **Then** it is shown as what the operator ran or could have run, with nothing implying the viewer can act on this fabric.
5. **Given** a snapshot, **When** it is inspected, **Then** it holds no credentials, no tokens and no environment values. Free text recorded from devices (error reasons) is included as recorded, and the command's output says so.

---

### Edge Cases

- **Empty audit trail** (fresh checkout): the topology shows every device as never scanned, the lanes are empty with the command that would fill them, and the headline says no drift has been recorded.
- **A link declared at one end only, or whose two ends disagree**: the link is drawn, and the detail of both devices says the two ends do not agree. Intent is shown as it is, not corrected.
- **An interface whose peer is not a device of this use case**: no link is drawn for it. The device's detail lists it.
- **A device in the audit trail that is no longer in the Source of Truth**: left off the topology, as 010 leaves it out of the device view, and kept in the lanes and runs, labelled as no longer in intent.
- **A role the layout does not know** (the site edge, #117): devices are grouped by role in a stable order. Nothing is dropped or stacked on top of another device.
- **More devices than fit one screen**: the topology stays readable down to a stated limit (see Assumptions) and above it shows the device list instead, saying why.
- **The "as of" time is before the first audit event**: every device is shown as never scanned, and the page says where history starts.
- **The devices or links were different at the "as of" time**: the Source of Truth keeps no history here, so the topology draws today's devices and links, and says so whenever a past time is shown.
- **An episode still open**: it has no duration. The headline counts open episodes separately and shows how long each has been open as of the "as of" time.
- **A device repaired and drifted again**: two episodes, not one.
- **Audit rows written late** (010 research R3): a past state can change when a late row lands. The page shows what the trail holds now.
- **A drift state, run state or source name the UI does not know** (a newer server): shown as its raw text with a neutral mark, never hidden.
- **A very long range for the lanes**: the server bounds the answer and the UI says the range is too long, as 010's history does. The lanes never silently drop scans.
- **The viewer asks for reduced motion**: nothing pulses or animates. Activity is shown with a static mark.
- **Keyboard only**: every device, panel control and the scrubber can be reached and used from the keyboard.
- **A narrow window**: the layout targets a laptop screen (see Assumptions). Below it, panels stack and the page stays usable without horizontal scrolling of the whole page.
- **Device-supplied text** (error reasons, paths): always shown as text, never interpreted as markup.
- **A snapshot or archive from another use case**: opened as that use case. Nothing assumes dcfabric.
- **Two archives, or an archive and the live stack, at once**: each `snapl dashboard` is one mode on one port. The port-in-use rule of 010 applies.

## Requirements *(mandatory)*

### Functional Requirements

#### Delivery and safety

- **FR-001**: `snapl dashboard` MUST serve the designed UI at its root when the UI's built files are present, and MUST always serve 010's plain page at `/plain`.
- **FR-002**: When the built files are absent, the root MUST serve the plain page with a notice naming the one command that builds the designed UI. The server MUST start either way.
- **FR-003**: Running the dashboard from an installed package MUST NOT need the build toolchain. A released package MUST include the built files. The built files MUST NOT be committed to the repository.
- **FR-004**: The UI MUST be read-only. It MUST NOT offer any control that changes a device, the Source of Truth, the workflow engine, the audit trail, an archive or the lab run records. A control that copies a shown command to the clipboard is allowed.
- **FR-005**: In every mode, the page MUST make no request to any origin but its own, and the server MUST instruct the browser to refuse any such request. A snapshot opened from disk MUST make no request at all.
- **FR-006**: Text that originates on a device or in the audit trail MUST be rendered as text only, and a check in CI MUST fail on any code path that would render it as markup.
- **FR-007**: The UI's types for the contract MUST be generated from the server's published contract document. CI MUST fail when they are out of date.
- **FR-008**: Values of the contract's open enumerations (010 contract, "Versioning") that the UI does not know MUST be shown as their raw text.

#### Topology (US1)

- **FR-009**: The contract MUST gain a topology view for the use case: its devices with their roles, and its links, each link naming both devices and both interfaces. Links MUST come from the peer each interface declares in the Source of Truth, behind the same identity check as the device view (010 FR-015). This is a compatible addition under 010's versioning rules.
- **FR-010**: The topology view MUST report a link whose two ends disagree, or which only one end declares, and MUST list interfaces whose peer is not a device of the use case, without drawing a link for them.
- **FR-011**: The UI MUST draw devices in tiers by role, in an order that is the same on every load for the same intent. For dcfabric that is spines above leaves.
- **FR-012**: Each device MUST show its drift state from the device view. Every drift state MUST be told apart by shape or mark as well as colour, in light and dark mode, and a legend MUST name each state shown.
- **FR-013**: A device MUST be shown as active while a workflow targeting it is running, and as recently active for a short stated time after a run targeting it ended (see Assumptions), using the audit trail for runs that have ended. A fabric-wide run MUST be shown on the fabric, not on every device.
- **FR-014**: When a differing path of a device names an interface that is one end of a link, that link MUST be marked. A path that names no interface marks the device only.
- **FR-015**: A device flagged for repeated failed repairs (010 FR-020) MUST be marked on the topology.
- **FR-016**: Selecting a device MUST open its detail: role, drift state, missing and undesired paths separately, error if any, last scan time, last deploy outcome, reason and time, its recent runs, its drift episodes (FR-019), and the suggested command or the note that explains why there is none.
- **FR-017**: When the Source of Truth is unavailable or foreign, the UI MUST show the degraded device list of 010 without tiers or links, with the view's reason and remedy.
- **FR-018**: Above a stated number of devices (see Assumptions), the UI MUST show the device list in place of the drawing and say why.

#### Drift over time (US2)

- **FR-019**: The contract MUST gain drift episodes, derived only from the audit trail. For each device and a requested range: each episode's opening scan, its closing scan if any, the states recorded in between, and what closed it. An episode opens at the first scan that finds the device not in sync and closes at the first later scan that finds it in sync. A scan that could not collect the device neither opens nor closes an episode.
- **FR-020**: What closed an episode MUST be the successful deploy that targeted the device between the last drifted scan and the closing scan, with its trigger and its kind: a scheduled remediation, an operator's reconcile, or an operator's direct deploy. With no such deploy it MUST be `unknown`. It MUST NOT be inferred from timing alone when no deploy targeted the device. *Amended in planning (research R10)*: the first wording named only reconcile and remediation runs, and a direct `snapl deploy <device>` repairs drift too.
- **FR-021**: The UI MUST show one lane per device over the selected range, showing the state each scan recorded, the stretches with no scan as no data, error scans as errors, and reconcile and remediation runs as markers.
- **FR-022**: The device view MUST accept a time, and return each device's state as the audit trail recorded it up to that time. This is a compatible addition. Any time the audit trail covers MUST be accepted.
- **FR-023**: The UI MUST offer an "as of" control over the lanes. When it is set to a past time, the topology, the device detail, the lanes' cursor and the runs list MUST show that time, and the page MUST state prominently that it shows a past time, with one control that returns to now.
- **FR-024**: When a past time is shown, any view whose source keeps no history MUST say it shows only the present, and the topology MUST say its devices and links are today's.
- **FR-025**: The UI MUST show a headline for the latest closed episode in the fabric: its duration from opening scan to closing scan, the device, and what closed it. The wording MUST NOT describe it as time to detect. It MUST also show the number of episodes open as of the shown time.

#### The loop (US3)

- **FR-026**: The contract MUST expose, for one run, the steps the audit trail recorded for it: each step's name, the NAF block that owns it, its outcome, its reason on failure, and its recorded time. This is a compatible addition. The mapping from step to block MUST live in one place on the server, not in the page.
- **FR-027**: The UI MUST show the six NAF blocks as a strip for the latest run, or a run the operator picks, marking each block done, failed or not reached from those steps only. A block MUST NOT be shown as in progress unless a record says its step started.
- **FR-028**: For a run whose steps are not recorded one by one, the strip MUST say so and show the run's outcome.

#### Panels (US4)

- **FR-029**: The UI MUST have a panel for every view of 010's contract and every metrics view of 012: schedule and worker, runs and running workflows, undesired config across the fabric, CI, live lab evidence, source status, links, and the six metrics views. Every fact the plain page shows MUST be available on the designed page.
- **FR-030**: Every panel MUST show its source, its data's age, and, when the source is unavailable, the reason and the remedy command. Stale data MUST be labelled with its age (010 FR-031).
- **FR-031**: The metrics panels MUST accept the presets of 012 and any custom range the metrics contract allows, and MUST show the step the server used. A refused range MUST be reported with the server's reason.
- **FR-032**: The selected device, the range and the "as of" time MUST be held in the page address, so that opening the address again shows the same view.
- **FR-033**: The UI MUST refresh every 5 seconds as 010's page does, and the metrics every 30 seconds as 012's do. It MUST stop refreshing while the tab is hidden, and refresh at once when it is shown again.
- **FR-034**: Wherever a command is suggested, the UI MUST show it in full with a copy control.

#### Archive and snapshot (US5, US6)

- **FR-035**: One bundle format MUST serve both the archive viewer and the snapshot. `history.archive`'s export MUST gain the topology, and whatever else of this spec's contract additions the snapshot needs, as a compatible addition to 012's archive. An archive made before this feature MUST still open.
- **FR-036**: `snapl dashboard --archive <file>` MUST serve the UI from the archive alone: the audit trail and lab runs through the same projections as live, and the schedule, CI, links and metrics from the archive's export. It MUST contact no service, MUST NOT modify the archive, and MUST leave nothing behind when it stops.
- **FR-037**: In archive mode, "now" MUST be the archive's creation time. A banner MUST name the archive, its creation time and its commit. A view the archive does not hold MUST say so, without a remedy that implies a failure.
- **FR-038**: In archive mode, the metrics panels MUST show only the exported ranges and say that other ranges need the live stack.
- **FR-039**: An archive with a part missing MUST open, with the views that need the part saying it is absent. A file that is not a snapl archive, or whose format is newer than the server knows, MUST be refused before any server starts, with a message that says which.
- **FR-040**: One command MUST write a snapshot: a single file holding the UI and the bundle's data for a stated range (default the last 7 days), from a running dashboard or from an archive. It MUST print the file's path, size, range and the kinds of data included, and MUST say that device-supplied text is included as recorded.
- **FR-041**: A snapshot MUST open from disk with no server and no network, and MUST provide the topology, lanes, "as of" control, loop strip, device detail and runs over its range. It MUST carry a notice that it is a recording, with its creation time and commit, and MUST NOT label any view live.
- **FR-042**: A snapshot MUST NOT contain credentials, tokens or environment values.

#### Quality

- **FR-043**: Motion MUST stop when the viewer asks for reduced motion, with a static mark in its place.
- **FR-044**: Every interactive element MUST be reachable and usable from the keyboard, and text and marks MUST meet a stated contrast level in both modes (see Assumptions).
- **FR-045**: The colour and chart rules this UI follows MUST be written down in `dev/skills/dashboard/`, with drift-state encodings checked for light mode, dark mode and common colour-vision deficiencies.
- **FR-046**: UI code MUST follow Principle III: for every unit with logic (layout, state encoding, time handling, the data providers for live, archive and snapshot), the test is written first, and new UI logic keeps at least 80% line coverage. The server additions follow Principle III as every Python change does.
- **FR-047**: CI MUST gain one job, run only when the UI or the contract changes, that type-checks the UI, runs its unit tests, checks the generated types (FR-007), and runs one end-to-end check of the page in a browser against the server serving fixtures captured from a real lab.

### Key Entities

- **Topology**: the use case's devices with roles, and its links. A link names two devices and two interfaces, and whether its ends agree. Comes from intent only.
- **Drift episode**: for one device, the span from the scan that first found it not in sync to the scan that first found it in sync again: the states recorded in between, whether it is still open, and what closed it (a run and its trigger, or unknown).
- **Device state at a time**: the device view as the audit trail recorded it up to a given time.
- **Run steps**: for one run, the recorded steps in order, each with its owning NAF block, outcome, reason and time.
- **Bundle**: the data a UI needs without a live stack: the audit-derived views over a range, the topology, the lab runs, and the exported schedule, CI, links and metrics, with a manifest saying when it was made, from which commit, and which parts are present.
- **Data mode**: live, archive or snapshot. It decides where views come from, what "now" means, and which views say they are not available.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: During the full demo arc (deploy, induced drift, scan, reconcile, scan), every step is visible on the topology within 10 seconds of the audit trail recording it, with nothing typed. This is 010's SC-001, kept.
- **SC-002**: A deploy that takes less than one refresh interval is still shown as activity on its device, in every one of 20 consecutive runs.
- **SC-003**: Someone who has never used snapl, given only a snapshot file, can say which device drifted, what repaired it and how long the drift lasted, in under 60 seconds.
- **SC-004**: For every scan boundary in the demo-arc fixture, the fabric shown at that "as of" time equals the device view computed from the audit trail up to that time. A test checks every boundary.
- **SC-005**: Every episode in a fixture with repeated drift, an error scan, a hand repair and a weeks-long gap is reported with the right opening, closing and closing cause, and the hand repair's cause is `unknown`.
- **SC-006**: With each source stopped in turn, the page is usable within 2 seconds, every other panel is unchanged, and the stopped source's panels name it and give a remedy. This is 010's SC-003, kept.
- **SC-007**: In live, archive and snapshot mode, the page makes zero requests to another origin. A test fails on the first one.
- **SC-008**: On a machine with nothing running and the network disabled, an archive of 100,000 audit events opens to a usable page within 10 seconds, and the archive file is byte-for-byte unchanged afterwards.
- **SC-009**: A snapshot opened from disk, with no server and the network disabled, shows the topology and replays the demo arc.
- **SC-010**: Each of the seven drift states can be told from the others in greyscale and under simulated red-green and blue-yellow colour-vision deficiency, in light and dark mode.
- **SC-011**: For the same fixtures, the designed page, the plain page and `snapl status --json` agree on every device's state and on the schedule.
- **SC-012**: A fresh checkout with no build toolchain still serves a working dashboard at the root.
- **SC-013**: With 100,000 audit events, the new views (topology, episodes, state at a time, run steps) each answer within 1 second on the maintainer's laptop. This extends 010's SC-007.
- **SC-014**: An operator can answer "is the fabric in sync, what did the schedule do overnight, and is the lab evidence current?" from the designed page in under 15 seconds, half of 010's SC-008.
- **SC-015**: The page can be operated end to end (select a device, set a range, move the "as of" time, return to now) with the keyboard alone.

## Assumptions

- **One viewer, one machine**, as in 010. No authentication, no remote access. A published snapshot is a file, not a service.
- **Screen**: the layout targets a laptop, 1280 pixels wide or more, and stays usable down to 1024. Phones are out of scope.
- **Fabric size**: the drawing is designed for up to 32 devices on one screen without pan or zoom. dcfabric has 6. Above 32, the device list is shown (FR-018).
- **Recently active**: a device is shown as recently active for 10 seconds after a run targeting it ends, which is two refreshes.
- **Lanes**: a scan's state is drawn until the next scan, for at most 15 minutes (three default schedule intervals). Beyond that the lane shows no data. An episode's duration is still measured scan to scan.
- **The audit trail records completed and failed steps, not started ones**, and records them one by one for deploys. So the loop strip marks done, failed and not reached, and shows deploys in full. Recording more steps is out of scope here. If a later spec records them, the strip shows them with no change to this one.
- **The loop strip's other two blocks**: the Orchestrator is marked for any run the audit trail records, since the run is its work. Presentation is shown as the surface being looked at, and never marked as a step.
- **The Source of Truth keeps no history the dashboard reads.** A past time shows today's devices and links (FR-024).
- **Contrast**: WCAG 2.1 AA for text and for the marks that carry state.
- **Refresh stays polling.** Server-pushed updates are added only if SC-002 cannot be met without them.
- **Snapshot range**: 7 days by default, matching `history.archive`'s export.
- **Publishing a snapshot** (for example to a public page for a demo post) is a manual step by the maintainer. Before the first public one, the maintainer checks what the device-supplied text in it exposes. The repository and its CI results are already public.
- **The archive's Prometheus data is not readable without Prometheus.** Archive mode shows the exported metrics only (FR-038).
- **Technology choices are ADR-0005 Decision 2's**, as revised in #146 on 2026-11-09. The plan records them, updates the ADR, and lists the constitution's Technology Stack change the ADR defers to this spec.
- **Principle III is written in terms of pytest.** FR-046 states how it applies to UI code. The constitution amendment that says so travels with this feature.
- **Skills**: the decisions in #146 stand (the design skill tried locally before any part is committed, the browser-testing skill pinned, the prose skill used for UI copy but not vendored).

## Out of Scope

- **Any action from the dashboard**: deploy, reconcile, schedule changes, as in 010.
- **Authentication, remote access and alerting.**
- **Recording more of the loop**: step-started records, per-step records for scans and reconciles, the operator's identity on operator-started runs. The UI shows what the trail holds.
- **History of intent**: what the devices and links were at a past time.
- **Time to detect**: nothing records when drift was introduced.
- **Querying an archive's Prometheus data.**
- **Operational expectations** (BGP sessions up, interfaces up): not checked by snapl yet, as in 010.
- **A second use case's layout**: the site edge (#117) is drawn by the generic role tiers. A layout designed for it belongs to its own spec.
- **Hosting a snapshot**: the feature writes the file. Where it is published is not automated.
- **Retiring the plain page.** It stays, as the fallback and as a second reading of the same data.

## Dependencies

- Spec 010: the dashboard server, its contract and versioning rules, the plain page, the source and freshness model.
- Spec 012: the metrics views, `history.archive` and its format.
- Spec 001: interface peers in the Source of Truth, which the topology's links come from.
- Spec 005: the audit event model, including the step records the loop strip reads.
- Spec 007 and #65: undesired config, and the rule that its removal is never automatic.
- Spec 009: the schedule, remediation runs and their triggers.
- ADR-0004 (claim only what is true) and ADR-0005 Decision 2 (the UI's build toolchain).
