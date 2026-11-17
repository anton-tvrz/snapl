# Implementation Plan: More Metrics

**Branch**: `overnight/015-*` (one branch per PR slice) | **Date**: 2026-11-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/015-more-metrics/spec.md`

## Summary

Show what snapl already measures, add figures from the audit trail, add three counters to the worker and record a scan's steps. The API gains ten metrics views, one unit, four history series and one route. The designed page gains a panel for each, grouped, in spec 014's frame.

## Technical Context

**Language/Version**: Python 3.12+ for the server and the worker; TypeScript 5.9 and React 19 for the UI.
**Primary Dependencies**: none new. `temporalio`'s metric meter (spec 012), FastAPI, pydantic, httpx; Recharts in the UI.
**Storage**: the audit trail and Prometheus, as they are. No schema change.
**Testing**: pytest with spec 012's Prometheus fixtures and spec 010's captured audit trail; `WorkflowEnvironment.start_local()` for the worker; Vitest and Playwright for the UI.
**Target Platform**: the dev stack on one machine (macOS with OrbStack, or Linux).
**Project Type**: additions to `presentation` and `orchestrator`.
**Performance Goals**: no more than one request per view on first load; each new projection within spec 010's 1 s budget on the captured trail; a scan no more than 10% slower with the extra audit writes (measured in the lab).
**Constraints**: additions only to the v1 contract; labels from enums only; views are named, never queried; nothing in the lab during an unattended run.
**Scale/Scope**: 16 metrics views in all, 8 history series, about 9 PR slices.

## Constitution Check

*GATE: checked before research and again after design.*

| Principle | How this feature meets it |
| --- | --- |
| I. NAF blocks | Counters and scan steps are recorded by `orchestrator`; views, series and panels are `presentation`. `observability` is unchanged: it still only evaluates drift. This is the split spec 012 and ADR-0005 Decision 3 set. |
| II. Spec-driven | This spec, plan, contract and tasks exist before any code. |
| III. Test-first | Every slice lists its tests first. Boundary changes (the worker's metrics endpoint, the audit trail a scan writes) carry live evidence, in tasks marked **Needs the lab**. |
| Honest claims | Each view says whether it is a mean, a percentile, a count or a current value. Durations from the trail are scan to scan. |

No violation, so the Complexity Tracking table is empty.

## Project Structure

### Documentation (this feature)

```text
specs/015-more-metrics/
├── spec.md
├── plan.md
├── research.md                  # R1 to R9
├── data-model.md
├── contracts/metrics-views.md   # the views, units, series, route, counters, step names
├── quickstart.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
packages/presentation/snapl_presentation/dashboard/
├── models.py          # MetricsViewName, MetricUnit, SeriesName, DriftSummary models
├── prometheus.py      # VIEWS: the new specs (After #241)
├── projections.py     # the four series, project_drift_summary, STEP_BLOCKS
├── app.py             # GET /drift-summary; /history's series parameter
├── archive.py, snapshot.py     # carry the summary; views follow the enum
└── static/index.html  # the plain page's metrics list

packages/orchestrator/snapl_orchestrator/
├── metrics.py                 # the three counters (After #241)
├── activities/observability.py  # records scan results and paths
├── workflows/deploy_intent.py   # records deploy ends
└── workflows/scan_drift.py      # the two new step events (After #241)

packages/presentation/ui/src/
├── lib/metrics.ts             # the view list with groups, the count unit
├── lib/numbers.ts             # the runs card
├── components/panels/Metrics.tsx        # groups
├── components/panels/FabricHistory.tsx  # new: in sync, open drifts, runs, repair durations
├── components/panels/DriftByDevice.tsx  # new: ranked bars from the summary
└── data/{provider,http,inline}.ts       # driftSummary, history series

tasks/history.py                         # export the summary and the new series
dev/scripts/serve_dashboard_fixtures.py  # synthetic data for every new view
tests/unit/test_presentation/, tests/unit/test_orchestrator/
```

**Structure Decision**: every addition goes where its kind already lives. No new module in the server; two new panels in the UI.

## Design

### Order of work

1. **Audit-trail figures first** (server, then UI). They touch nothing PR #241 touches and need no metrics store.
2. **Views over exported families.** A change to `VIEWS` only, but in a file #241 rewrites, so it waits for #241.
3. **Counters**, then **scan steps**, both after #241.
4. **Grouping, files and docs.**

### The contract

One file, [contracts/metrics-views.md](contracts/metrics-views.md). The OpenAPI snapshot and the UI's generated types are regenerated in each server slice (`invoke dashboard.types`), and `types:check` fails the UI when they drift.

### Honest counts (research R4)

A `count` view's point is the events in its window. The panel's lead value is the latest point with the window named ("3 in the last 60 s"). It never prints a total across points.

### What stays the same

- The six existing views, their names and queries (apart from #241's own change).
- `/history`'s default answer.
- Every existing projection's answer on the same trail (FR-019).
- The scrape config and Prometheus's retention.

## Complexity Tracking

No constitution violation to justify.
