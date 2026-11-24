# Feature Specification: Overnight Backlog (a flaky test, cancel races, a guard, a shorter page, a smaller snapshot)

**Feature Branch**: `018-overnight-backlog`
**Created**: 2026-11-21
**Status**: Accepted
**Umbrella**: #374
**Input**: The open backlog after spec 017, chosen by the maintainer on 2026-11-21 for one unattended run: #371, the other cancellation tests (#375, from #333's cause), #136's blast-radius guard, #305, #235, and task-box housekeeping (#376). #65 waits for spec 013's lab captures. #136's Temporal identity check stays open.

The maintainer decided four questions on 2026-11-21:
- **#305**: the Runs table shows the latest 10 runs, with a button for the rest, and every metrics group starts folded. SC-002 is re-measured on the lab afterwards and amended only if the page is still over.
- **#235**: the snapshot's bundle is embedded gzipped and base64-encoded, and unpacked by the browser. The budget is 3 MB for 7 and 30 days of real data. `--bundle-json` writes the readable bundle beside the snapshot. Measured on the 2026-11-20 archive before this spec: 7 days is 3.50 MB (1.08 MB of it the page's code), 30 days is 5.40 MB. Gzipped, they are 1.28 MB and 1.44 MB. Dropping the timeline's points between state changes was ruled out: the page holds a scan's state for 15 minutes (`HOLD_MS`) and gives every scan a scrubber stop, so the gaps would draw as "no data".
- **#136**: a remediate run whose scan finds more than half of the use case's devices with forward drift repairs nothing and stands the schedule down. The threshold is fixed at one half, not configurable.
- Housekeeping ticks only what the code shows is done. Closing umbrella issues is left to the maintainer.

## User Scenarios & Testing

### User Story 1: A UI test that waits for what it checks (#371, Priority: P2)

`App.test.tsx > shows the loop for the latest run` failed once in CI: it checked that the steps were asked for right after the region rendered, before the runs had loaded.

**Independent Test**: the test itself, run repeatedly.

**Acceptance Scenarios**:
1. **Given** a slow runner, **When** the test runs, **Then** it waits for the steps call before checking its arguments, and passes.

### User Story 2: No cancellation test can hang the way #333 did (#375, Priority: P2)

#333's cause: the time-skipping test server refuses a workflow task that cancels an activity whose completion arrived during the task, and the run hangs. Any test that cancels as soon as a side effect is visible can hit it.

**Independent Test**: each cancellation test, under the race-widening executor from #373.

**Acceptance Scenarios**:
1. **Given** any test in `tests/unit/test_orchestrator/` that calls `handle.cancel()`, **When** it cancels, **Then** the workflow is quiet: only the held activity is pending, and the last history event is `WORKFLOW_TASK_COMPLETED`.
2. **Given** the quiet wait, **When** two test files need it, **Then** they share one helper.

### User Story 3: A bad intent edit doesn't repair the whole fabric (#136, Priority: P1)

A schedule in `remediate` repairs every device with forward drift. A bad intent edit, such as a wrong ASN for the whole fabric, makes every device drift at once, and the next run would push the mistake to all of them.

**Independent Test**: `DriftResponse` with stand-in blocks on the time-skipping server.

**Acceptance Scenarios**:
1. **Given** a remediate run whose scan finds forward drift on more than half of the devices it scanned, and on more than one device, **When** the run decides what to repair, **Then** it starts no reconcile. It stands the schedule down to `report`, with a note naming the run, the count and the threshold, and records why in the audit trail.
2. **Given** exactly half, or fewer, **When** the run decides, **Then** it repairs as today.
3. **Given** a use case of one device that drifts, **When** the run decides, **Then** it repairs as today: one device is not a blast radius.
4. **Given** a run in `report`, **When** it scans, **Then** the guard does nothing.
5. **Given** a run started before this change (replay), **When** it replays, **Then** its history is unchanged: the guard sits behind a new patch id.

### User Story 4: The page is short enough to read (#305, Priority: P2)

The live page is 7086 px against spec 014 SC-002's 3062 px. The Runs table is about 2050 px of it, because the schedule writes two runs every 5 minutes. The metrics are about 1650 px, with only one group open.

**Independent Test**: the UI's tests, and `e2e/look.mjs` against the fixture server.

**Acceptance Scenarios**:
1. **Given** more than 10 runs, **When** the page shows the Runs table, **Then** it shows the latest 10, running ones first as today, and a button that says how many more there are and shows them all.
2. **Given** a device selected, **When** the Runs table filters to its runs, **Then** the cap applies to the filtered list.
3. **Given** a first visit with no `?open=`, **When** the page renders, **Then** every metrics group is folded behind its heading. A link from the number row, or `?open=`, still opens a group.
4. **Given** the fixtures, **When** `e2e/look.mjs` measures the page, **Then** the PR records the page's height and the Metrics section's top, before and after.

### User Story 5: A snapshot small enough to send (#235, Priority: P2)

A 7-day snapshot of real data is 3.50 MB, and 30 days is 5.40 MB. A snapshot is meant to be attached or published.

**Independent Test**: unit tests on `snapshot.py` and `inline.ts`, the e2e snapshot test, and snapshots made offline from a real archive.

**Acceptance Scenarios**:
1. **Given** a snapshot is written, **When** it is opened from disk in a current browser, **Then** the page shows the same views as before this spec.
2. **Given** a snapshot written before this spec (plain JSON in the element), **When** it is opened, **Then** it still opens.
3. **Given** the 2026-11-20 archive, **When** 7-day and 30-day snapshots are made from it, **Then** each is under 3 MB.
4. **Given** `--bundle-json FILE`, **When** a snapshot is written, **Then** the readable bundle is written to FILE as well, and the summary names it. Without it, the summary says how to get one.
5. **Given** a bundle that is corrupt or can't be unpacked, **When** the page opens, **Then** it refuses with a message, as for a bundle it can't read today.

### User Story 6: Task lists say what is left (#376, Priority: P3)

Specs 003 and 005 list all their tasks as open, though both blocks shipped. Spec 001's T028-followup has every milestone ticked but is itself open.

**Acceptance Scenarios**:
1. **Given** a task whose file, test or behaviour exists on main, **When** housekeeping runs, **Then** its box is ticked.
2. **Given** one that doesn't, **When** housekeeping runs, **Then** it stays open and the PR lists it.

### Edge Cases

- **The guard**: a scan where some devices errored. The threshold's denominator is the devices the scan covered (`total`), errored ones included. A device with only undesired config isn't forward drift and doesn't count.
- **The guard**: a run whose reconcile child would skip some devices for a deploy in flight. The guard counts drift before eligibility, because a bad edit is the risk either way.
- **The cap**: fewer than 10 runs shows no button. "Show every device's runs" and "show all runs" are separate controls.
- **Gzip**: a browser without `DecompressionStream` (older than Safari 16.4 or Firefox 113). The page says it needs a newer browser, rather than showing nothing.
- **Gzip**: `</script>` can't occur in base64, so the escaping that `render` does today is no longer needed for the packed form.

## Requirements

### Functional Requirements

- **FR-001**: `App.test.tsx`'s latest-run test MUST wait for the steps call before checking its arguments.
- **FR-002**: Every unit test that cancels a workflow MUST cancel only once it is quiet, through one shared helper.
- **FR-003**: In `remediate`, `DriftResponse` MUST start no reconcile when the scan's drifted devices are more than one, and more than half of `total`. It MUST stand the schedule down through the existing `stand_down_schedule` activity, with a note that names the run, the drifted count, the total and the rule, and MUST record the outcome in the audit trail.
- **FR-004**: The guard MUST sit behind a new `workflow.patched` id, so histories made before it replay unchanged.
- **FR-005**: The Runs table MUST show at most 10 runs until the viewer asks for all, saying how many it hides.
- **FR-006**: No metrics group MUST be open by default. `?open=` and the number row's links MUST still open them.
- **FR-007**: A snapshot MUST embed its bundle gzipped and base64-encoded, marked so the page can tell it from plain JSON. The page MUST still read plain JSON.
- **FR-008**: `snapl dashboard --snapshot` MUST accept `--bundle-json FILE` and write the readable bundle there. The summary MUST print the packed size and the unpacked bundle's size.
- **FR-009**: Housekeeping MUST tick a task only when main shows it done.
- **FR-010**: Everything MUST be additive to the v1 dashboard API. The snapshot file's bundle element changes on purpose (FR-007), and spec 011's `contracts/cli-and-build.md` is amended to say so.

### Key Entities

None new.

## Success Criteria

- **SC-001**: On the lab, the live page at 1280 px wide is at most 3062 px (spec 014 SC-002), or SC-002 is amended with the new measure and the maintainer's reason. *Met on 2026-11-24 by amendment: 3661 px, and spec 014 SC-002 now reads at most 4000 px.*
- **SC-002**: Snapshots of 7 and 30 days from the 2026-11-20 archive are each under 3 MB, and open in Chromium from disk in the e2e snapshot test.
- **SC-003**: On the lab, a remediate schedule given an intent edit that drifts the whole fabric repairs nothing, and the schedule shows `report` with the guard's note.
- **SC-004**: `uv run invoke test-unit`, `mypy packages/`, `bandit` and the UI's tests pass after every slice.

## Assumptions

- `DecompressionStream` is available in every browser the snapshot is meant for. It has shipped in Chromium since 80, Firefox since 113 and Safari since 16.4.
- The threshold of one half is fixed until someone needs it changed. A field on the schedule request can come later without replay trouble.
- The SC-003 lab check uses a branch or a revert the maintainer chooses. The overnight run never edits intent on the lab.
