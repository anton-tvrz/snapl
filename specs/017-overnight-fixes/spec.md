# Feature Specification: Overnight Fixes (counts, newest step, seed, a flaky test)

**Feature Branch**: `017-overnight-fixes`
**Created**: 2026-11-20
**Status**: Accepted
**Input**: Four open issues, chosen by the maintainer on 2026-11-20 for one unattended run: #308, #306, #62 and #333. #305 (the page's height), #235 (the snapshot's size) and #136's remaining ideas are left out: each needs a design call made while looking at the page.

The maintainer decided two questions on 2026-11-20:
- #308: each point counts only its own step, as a whole number. The contract's "never a sum of points" stays.
- #306: an exported range ends at the export's own time and keeps its step.

## User Scenarios & Testing

### User Story 1: Counts that read as what happened (#308, Priority: P1)

The four views that count events (`workflows-completed`, `scan-results`, `drift-paths`, `deploy-outcomes`) show 5.33 scans where there were 4. One scan shows as four points in a row, because each point counts the last 60 s at a 15 s step.

**Independent Test**: render each counting view's query at several steps, and parse a Prometheus answer for it. On the lab, four scheduled scans read as 4.

**Acceptance Scenarios**:
1. **Given** 4 scans in a range, **When** the `scan-results` view is read at any step, **Then** every point is a whole number, the points add up to 4, and no scan is counted in two points.
2. **Given** a counter's first observation, or a worker restart that resets it, **When** the view is read, **Then** the count after the first observation or the reset is counted, not dropped and not negative.
3. **Given** any counting view, **When** it is read, **Then** it carries no "events in the last 60 s" note, because a point covers its own step.

### User Story 2: An export keeps its newest data (#306, Priority: P1)

An archive made at 19:44 exported metrics ending at 19:06, because a 7-day range at a 2520 s step is rounded down at both ends. In a snapshot, spec 015's views at a 3 h step ended at 18:00. The newest data is often the incident someone wants to share.

**Independent Test**: plan a range with an explicit end, and export an archive and a snapshot with stub servers.

**Acceptance Scenarios**:
1. **Given** a range with an explicit end, **When** it is planned at any step, **Then** it ends at that end and its start is a whole number of steps before it.
2. **Given** a range with no end (the live page following now), **When** it is planned, **Then** both ends are rounded down as today, so nearby requests share a cache entry.
3. **Given** an archive or a snapshot made at time T, **When** it is opened, **Then** every metrics view's exported range ends at T, to the second, and its default view still answers.

### User Story 3: Seeding says what's wrong, where it's wrong (#62, Priority: P2)

`infrahub/seed.py` has three problems:
- it reads the default branch but writes the target branch;
- it lets raw connection and SDK errors escape;
- it silently skips a session whose peer address doesn't resolve, which the device then rejects at deploy time.

**Independent Test**: unit tests with the seed module's fake Infrahub client.

**Amended in PR C**: the code showed PR #241 (`cd22360`) had already fixed all three problems, with tests, without closing #62. Every lookup goes through `SeedIngester._find`, which passes the branch. The namespace lookup runs inside the translated block. A missed session address raises, and an unused peer group warns. One gap was left: the seed copied the store's SDK-error translation instead of sharing it. PR C moves that rule into `snapl_intent/infrahub/sdk_errors.py`, used by the store and the seed alike. It also adds the tests T013 names that #241 lacked: the branch on each kind of lookup, `OSError` and `TimeoutError` from any lookup, and each leaf's sessions.

**Acceptance Scenarios**:
1. **Given** `seed(branch="b")`, **When** it looks up an existing node or a relationship's peer, **Then** it looks on branch `b`.
2. **Given** a connection failure or an Infrahub SDK error anywhere in a seed, **When** it happens, **Then** the caller gets `IntentConnectionError` or the store's existing SDK-error translation, never a raw exception.
3. **Given** a session whose `local_ip` or `remote_ip` lookup misses, **When** it is seeded, **Then** `IntentValidationError` names the session and the address, as the ASN lookups already do.
4. **Given** a declared peer group that no session references, **When** it is seeded, **Then** a warning names it.

### User Story 4: A test that fails once and passes on rerun (#333, Priority: P3)

`test_a_cancelled_scan_keeps_the_reports_that_completed` failed once in CI: the cancel's workflow task was not answered within 20 s.

**Independent Test**: the issue's own "Done when".

**Acceptance Scenarios**:
1. **Given** the test under CPU contention (a few busy processes beside it; a shell loop runs it, no new dependency), **When** it runs 50 times, **Then** it passes 50 times, and the cause of the original failure is written down: shown by a repro, or reasoned from the SDK's task handling.
2. **Given** no cause can be shown, **When** the slice ends, **Then** it ships only what is supported by evidence (for example a deadline raised to a measured CI time) and says so; #333 stays open.

### Edge Cases

- A counting view at a step shorter than the worker's 5 s scrape. The API's default step is never under 15 s, but `step` is a request parameter.
- A counter series that disappears (worker stopped) and comes back.
- An archive's 15 s detail windows (#351) already end at whole steps. Planning them with an explicit end must not move them.
- An archive made before this spec: its exported ranges are floored, and it must still open with its default view.

## Requirements

### Functional Requirements

- **FR-001**: A counting view's point at time t MUST be the counter's increase over (t − step, t], as a whole number, without extrapolation.
- **FR-002**: The increase MUST count a counter's first observation, and the value after a reset, as #191's `_increase` does for the first observation.
- **FR-003**: The window note ("events in the last N s") MUST no longer appear on counting views. The spec 015 contract (`specs/015-more-metrics/contracts/metrics-views.md`, "The window note") MUST be amended to say so. `rate()` views keep their window rule.
- **FR-004**: `plan_range` MUST align a range to an explicitly requested end, and keep rounding both ends down when the end defaults to now.
- **FR-005**: `history.archive` MUST pass the archive's time as `end` for every ranged export, so the exported metrics end at it. A snapshot already passes its end.
- **FR-006**: The archive reader MUST keep answering an archive made before this spec at its exported range.
- **FR-007**: The seed MUST thread `branch` through every lookup, translate every connection and SDK error, and fail on an unresolved session address with `IntentValidationError`.
- **FR-008**: The seed MUST log a warning for a declared peer group that no session references.
- **FR-009**: Everything MUST be additive to the v1 dashboard contract except the counting views' values and note, which FR-001 and FR-003 change on purpose.

### Key Entities

None new.

## Success Criteria

- **SC-001**: On the lab, `scan-results` over the last hour adds up to the number of ScanDrift runs the audit trail has in that hour, and every point is a whole number. *Met on 2026-11-24 (T006).*
- **SC-002**: On the lab, an archive made at T exports every metrics view to T, not to T rounded down to its step. *Met on 2026-11-24 (T012), for the archive and for a snapshot.*
- **SC-003**: The cancellation test passes 50 runs in a row locally under contention, or #333 says what was ruled out.
- **SC-004**: `uv run invoke test-unit`, `mypy packages/`, `bandit` and the UI's tests pass after every slice.

## Assumptions

- The worker is scraped every 5 s (#348), and Temporal's counters come from the worker's runtime, so `workflows-completed` follows the same rule as snapl's counters.
- #62's related gap (sessions seeded on one side only) was closed by #316 and #318. A test shows the leaves have their sessions, and nothing else changes for it.
