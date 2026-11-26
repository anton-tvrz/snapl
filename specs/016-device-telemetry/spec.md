# Feature Specification: Device Telemetry

**Feature Branch**: `spec/016-device-telemetry`
**Created**: 2026-11-19
**Status**: Draft
**Input**: The maintainer, 2026-11-18: "let's write spec 016", after spec 015 named device telemetry as a later spec (spec 015, Assumptions: "Interface counters, BGP sessions and CPU need paths captured from the lab first, and a rule for how many series a fabric may add").
**Scope settled**: 2026-11-18. The maintainer chose all three signal groups (interfaces, BGP sessions, CPU and memory), streaming collection, and "show and flag": telemetry is shown, and a device whose intent expects something up that is down is flagged, separately from drift, with no automatic repair. The points this spec decided without asking are listed under Assumptions.
**Amended**: 2026-11-19, after capturing the subscriptions on the lab (research R1). Intent describes each eBGP session once, on its spine end, and no leaf has BGP config, so no session has ever been established on the lab (#311). The BGP stories now speak of the session ends intent describes, and the series budget is counted from the captured paths.

## Context

Everything snapl measures today is about its own loop: how long a scan takes, how many drifts it found, how often a deploy failed. The README says so plainly: "No device telemetry. The metrics describe the loop, not the network. snapl collects no interface counters, BGP state or device CPU."

So a fabric can be "6 of 6 in sync" while a link is down or a BGP session is idle. Config drift and operational health are different questions, and snapl only answers the first. The intent already holds what the second needs: the dcfabric intent lists 22 interfaces (16 fabric links and 6 loopbacks) and 8 eBGP sessions, and each of them is meant to be up. Today intent describes each session on its spine end only, which is why the leaves have no BGP and no session has ever come up (#311). That is exactly the kind of fault this feature exists to show: the fabric reads "6 of 6 in sync" while its underlay is down.

This feature streams operational state from each device, shows it on the dashboard, and flags a device whose intent expects an interface or BGP session up that is not. It does not repair anything: an operational fault is often outside config (a cable, a peer), and the loop's repairs stay config-only.

### What this feature must never do

- Put an unbounded value in a metric label: no error text, no prefix, no free-form description (spec 012 SC-006). Labels name a device, an interface, a peer or a fixed state, each from intent or from a fixed list.
- Treat health as drift. A device can be in sync and unhealthy, or drifted and healthy, and every existing drift figure stays as it is.
- Start a repair, a deploy or a workflow because of telemetry.
- Make a telemetry failure stop the worker, fail a scan, or take the dashboard down. A device whose stream is down is "health unknown", not "unhealthy".
- Grow without bound: the number of series a fabric adds has a budget (FR-013).
- Touch the lab during an unattended run.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — See Each Interface's State and Traffic (Priority: P1) 🎯 MVP

The operator opens a device and sees each of its interfaces in intent: whether it is up, and its traffic in and out, its errors and its discards over the selected range. The metrics section has a "The network" group with the fabric's interfaces: traffic by device, and errors and discards by interface.

**Why this priority**: Interfaces carry the fabric. A down link is the most common operational fault, and its counters are the first thing an operator asks for.

**Independent Test**: From telemetry captured on the lab and replayed, check that each interface in intent has its state and its rates, and that an interface not in intent is not shown. Check the views with the metrics store down.

**Acceptance Scenarios**:

1. **Given** the lab running with the worker, **When** the operator opens leaf-01, **Then** ethernet-1/49 and ethernet-1/50 show as up, with their traffic in and out over the range.
2. **Given** spine-01's ethernet-1/1 administratively disabled on the device, **When** the next sample arrives, **Then** the drawer shows it down within 30 seconds of the change.
3. **Given** an interface on a device that intent does not list (for example a management port), **When** the operator opens the device, **Then** that interface is not listed and adds no series.
4. **Given** the metrics store is down, **When** a network view is requested, **Then** the answer is unavailable with a reason and a remedy, as for the existing views.

---

### User Story 2 — Flag a Device That Is Not Healthy (Priority: P1)

When an interface or BGP session that intent expects up is down, the device is flagged as unhealthy, with the reason, on the fabric panel, in the device drawer and in `snapl status`. The flag is separate from the drift state: a device can show "in sync" and "unhealthy" at once.

**Why this priority**: This is what turns telemetry into an answer. "6 of 6 in sync" with a dead link is the gap this feature closes.

**Independent Test**: Replay captured telemetry in which one session goes idle and one interface goes down. Check the health of each device, its reasons, and that every drift figure is unchanged.

**Acceptance Scenarios**:

1. **Given** every interface and session in intent is up, **When** the operator opens the page, **Then** every device is healthy, and the number row says so.
2. **Given** leaf-01's ethernet-1/49 goes down, **When** its state arrives, **Then** leaf-01 is flagged unhealthy with the reason "ethernet-1/49 down (expected up)", and the BGP session spine-01 to leaf-01 that runs over it is flagged on each end intent describes once it drops (both ends once #311 is fixed).
3. **Given** leaf-01 is unhealthy but its config matches intent, **When** the operator reads the fabric panel, **Then** leaf-01 is shown in sync and unhealthy, and the in-sync count still counts it.
4. **Given** the stream from leaf-03 has delivered nothing for longer than its staleness limit, **When** the operator opens the page, **Then** leaf-03's health is "unknown" with the reason "no telemetry since <time>", never "unhealthy".
5. **Given** the interface comes back up, **When** its state arrives, **Then** the flag clears, and the history keeps when it was raised and cleared.

---

### User Story 3 — See BGP Sessions (Priority: P2)

The operator sees each BGP session end in intent: its state and the prefixes received. A view in "The network" group charts sessions established over time.

**Why this priority**: The fabric's routing runs over these sessions. They depend on interfaces, so they come after Story 1.

**Independent Test**: From captured telemetry, check each session end in intent, a session that never establishes (the lab today, #311), and a session that drops and recovers.

**Acceptance Scenarios**:

1. **Given** the lab converged with #311 fixed, **When** the operator opens spine-01, **Then** its four sessions to the leaves show as established, with prefixes received.
2. **Given** a session goes idle, **When** its state arrives, **Then** each end in intent shows it not established within 30 seconds, and its device is flagged (Story 2).
3. **Given** a session on the device that intent does not list, **When** telemetry arrives, **Then** it is not shown and adds no series.
4. **Given** the lab as it is today (#311 open), **When** the worker streams, **Then** both spines are flagged unhealthy with four reasons each ("BGP 10.10.1.1 active (expected established)"), and every leaf is healthy on its interfaces.

---

### User Story 4 — See CPU and Memory (Priority: P3)

The operator sees each device's CPU and memory use over the range, in the drawer and in "The network" group.

**Why this priority**: Easy to read, and useful to explain a slow device, but it says little about the fabric's health, so it does not flag a device.

**Independent Test**: From captured telemetry, check one CPU and one memory series per device, in percent.

**Acceptance Scenarios**:

1. **Given** the lab running, **When** the operator opens a device, **Then** its CPU and memory use are shown in percent over the range.
2. **Given** a device's CPU reads 100%, **When** the operator opens the page, **Then** the device is not flagged; CPU and memory never change health.

---

### User Story 5 — Telemetry in Archives and Snapshots (Priority: P3)

An archive and a snapshot carry the network views and every device's health over their range, so a recording of an incident shows the down link as well as the drift.

**Why this priority**: Archives and snapshots are how the demo is shared. They follow once the views exist.

**Independent Test**: Make an archive and a snapshot with telemetry in range, open them with the stack stopped, and check the views and the health history. Open an older archive and check each view says it is not in it.

**Acceptance Scenarios**:

1. **Given** an archive made while leaf-01's link was down, **When** it is opened, **Then** the health history shows when leaf-01 was flagged and cleared, and the network views show the interface down over that time.
2. **Given** an archive made before this feature, **When** it is opened, **Then** each network view says it is not in this archive.

---

### Edge Cases

- **A device restarts or is paused.** Its stream breaks. Health becomes "unknown" after the staleness limit, the stream reconnects with backoff, and the device's other figures (drift, scans) are unaffected.
- **A counter resets** when a device restarts. Rates must not show a negative or a spike from the reset.
- **The worker restarts.** Streams start again with the worker; nothing is replayed, and the gap is visible as missing points, not zeros.
- **Intent changes** (an interface or session added or removed). The set of streamed and flagged items follows intent within one refresh of intent, with no restart.
- **An interface is administratively disabled in intent.** It is expected down, so down is healthy for it, and up is not a health fault (it is drift, which the scan reports).
- **Both ends of a link report differently** (one side up, one down). Each device is judged on its own side.
- **The lab is not running.** Every device is "health unknown", every network view says there is no data, and nothing else changes.
- **A device not in intent** (left over in the lab). It is not streamed.

## Requirements *(mandatory)*

### Functional Requirements

**Collection**

- **FR-001**: The worker MUST hold one telemetry stream per device in intent while it runs, and start and stop the streams with itself.
- **FR-002**: Each stream MUST deliver, for that device: each intent interface's operational state and its in and out octets, errors and discards; each intent BGP session end's state and prefixes received; and the device's CPU and memory use. It MUST sample at a fixed interval of at most 30 seconds.
- **FR-003**: A broken stream MUST reconnect with backoff and MUST NOT affect scans, deploys or other devices' streams.
- **FR-004**: The set of streamed interfaces and sessions MUST follow intent, refreshed at least as often as a scheduled scan, without a restart.

**Health**

- **FR-005**: A device's health MUST be one of `healthy`, `unhealthy` or `unknown`, from a fixed list.
- **FR-006**: A device MUST be `unhealthy` when an interface or BGP session that intent expects up has been reported down for longer than a debounce of one sample interval. Each fault MUST be given as a reason naming the interface or session and its expected and actual state.
- **FR-007**: A device MUST be `unknown` when no telemetry has arrived for longer than its staleness limit (three sample intervals), with the time of the last sample as its reason.
- **FR-008**: CPU and memory MUST NOT change health.
- **FR-009**: Each change of a device's health MUST be recorded in the audit trail with its reasons, so history, archives and `snapl audit --device` show it.
- **FR-010**: Health MUST NOT change any drift figure, the in-sync count, or what the loop repairs.

**Presentation**

- **FR-011**: The dashboard MUST show each device's health on the fabric panel and in the device drawer, separately from its drift state; the drawer MUST list the device's intent interfaces and sessions with their state, and its CPU and memory. `snapl status` MUST report the fabric's health.
- **FR-012**: The metrics section MUST add a group "The network" with named views: traffic by device, errors and discards by interface, BGP sessions established, and CPU and memory by device. Views stay named, as in spec 012.
- **FR-013**: A fabric MUST add at most 7 series per interface in intent (its state, and octets, errors and discards in each direction), 2 per BGP session end in intent (state and prefixes received) and 2 per device (CPU and memory). Labels MUST come from intent (device, interface, peer) or from fixed lists (direction, state).
- **FR-014**: Rates MUST hide a counter reset (no negative rate, no spike).
- **FR-015**: Archives and snapshots MUST carry the network views and the health history over their range. An older archive MUST say the network views are not in it.
- **FR-016**: Everything MUST be additive to the v1 dashboard contract. No existing view, figure or field changes meaning.

### Key Entities

- **Telemetry stream**: one per device in intent, held by the worker. It has a state (connecting, streaming, broken) and the time of its last sample.
- **Interface state**: an intent interface's operational state and counters at a sample.
- **BGP session state**: one end of an intent session: its state and prefixes received.
- **Device health**: `healthy`, `unhealthy` or `unknown`, with zero or more reasons, and the time it last changed.
- **Health change**: an audit event recording a device's health before and after, with reasons.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On the lab, an interface disabled on a device shows as down on the dashboard within 30 seconds, and its device is flagged within 60 seconds.
- **SC-002**: On the lab, a BGP session brought down shows as not established on each end in intent within 30 seconds of the change. Needs #311 fixed, since no session is established before it.
- **SC-003**: With the lab converged and #311 fixed, all 6 devices are healthy, and none is ever flagged because of CPU or memory. Before #311, exactly the two spines are flagged, each for its four sessions.
- **SC-004**: The dcfabric fabric adds at most 198 series (22 interfaces × 7, 16 session ends × 2, 6 devices × 2; 182 while #311 leaves 8 ends), and no label value comes from outside intent or a fixed list.
- **SC-005**: Streaming changes the median scheduled scan's duration by at most 10%, measured on the lab as in spec 015 T044.
- **SC-006**: A device paused for one minute shows health `unknown`, never `unhealthy`, and its stream recovers within 60 seconds of the unpause, with no worker restart.
- **SC-007**: Every drift figure, view and test from specs 010 to 015 is unchanged.
- **SC-008**: An archive made across one induced link fault shows the fault's start and end in the health history when opened with the stack stopped.

## Assumptions

- **SR Linux only.** The paths are SR Linux 26.3.1's. The streams are written against the collector's interface so another platform can follow, but no other is built or claimed.
- **Paths are captured from the lab first.** Done 2026-11-19 while planning: sample-mode subscriptions from leaf-01 and spine-01 on SR Linux v26.3.1, kept as fixtures (research R1). Everything after builds and tests against those captures. Captures of a fault (an interface down, a paused device) are lab tasks.
- **CPU and memory on the lab describe the lab's host.** SR Linux in a container reports the VM's memory: every node reads the same 16.8 GB physical and 94% used. The panels say so on the lab, and no claim is made from them about a switch.
- **Streamed, not polled.** One subscription per device, sampled. The scan's own gNMI reads are unchanged.
- **The worker hosts the streams** and exports their figures on its existing metrics endpoint, so Prometheus needs no new target. Whether the streams run inside the worker process or beside it is for the plan.
- **Health is evaluated where the samples arrive** and recorded as audit events on change only. The dashboard reads health from the audit trail, so it works in archives.
- **Expected up** means: an interface in intent whose intended admin state is enabled, and every BGP session in intent. An interface intended disabled is expected down.
- **Debounce and staleness** are one and three sample intervals, so a single lost sample flags nothing.
- **"The network" group** joins the four metric groups from spec 015, folded by default, like the other groups after the first (spec 014 #290).
- **The page length** (spec 014 SC-002, #305) is not made worse by more than the network group's folded heading and the drawer's longer content.

## Out of Scope

- Repairing an operational fault, or any response to health beyond the flag.
- Alerting or notifications.
- Telemetry for anything not in intent.
- Interface counters beyond octets, errors and discards (no per-queue, no optics).
- Multi-vendor support.
- Long-term retention beyond the metrics store's existing settings.

## Dependencies

- Specs 012 and 015: the metrics store, the named views, the metric groups and the label rules.
- Spec 010: the audit trail and the dashboard's read API.
- The dcfabric lab, for the captures (first) and the live evidence (last).
- #311 (the leaf end of each eBGP session), for SC-002, SC-003 and US3 scenario 1. Nothing else in this spec waits for it: with #311 open, the spines are correctly flagged.
