# Implementation Plan: Device Telemetry

**Branch**: `overnight/016-*` (one branch per PR slice) | **Date**: 2026-11-19 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/016-device-telemetry/spec.md`

## Summary

The worker streams interface, BGP and platform state from each device in intent, over one gNMI subscription per device. It exports the figures on its metrics endpoint and records each device's health in the audit trail when it changes. The dashboard shows health beside drift, the drawer shows each device's interfaces and sessions, and a new metrics group charts the network. Nothing repairs, and no drift figure changes.

The subscription paths were captured on the lab while planning (research R1). That capture also found that no eBGP session has ever been established on the lab, because intent configures the spine end only (#311). This feature flags that fault as soon as it runs.

## Technical Context

**Language/Version**: Python 3.12+ for the collector, observability, the worker and the server. TypeScript 5.9 and React 19 for the UI.
**Primary Dependencies**: none new. pygnmi's `subscribe2` (R2), `temporalio`'s runtime meter (R5), FastAPI and pydantic; Recharts in the UI.
**Storage**: the audit trail (one new event type) and Prometheus (eight new families). No schema change.
**Testing**: pytest, with the captured subscriptions (`tests/unit/test_collector/fixtures/telemetry/`), a fake collector for the runner, spec 012's Prometheus fixtures plus synthetic network series, and the captured audit trail. Vitest and Playwright for the UI.
**Target Platform**: the dev stack on one machine (macOS with OrbStack, or Linux), SR Linux v26.3.1 in Containerlab.
**Project Type**: additions to `collector`, `observability`, `orchestrator` and `presentation`.
**Performance Goals**: a fault shown within 30 s and flagged within 60 s (SC-001); a median scan no more than 10% slower (SC-005); each new projection within spec 010's 1 s budget.
**Constraints**: labels from intent or an enum only; additions only to the v1 contract; health never touches drift; nothing in the lab during an unattended run.
**Scale/Scope**: 6 devices, 22 interfaces, 8 session ends today (16 once #311 is fixed); 162 series measured, 198 at most. About 10 PR slices.

## Constitution Check

*GATE: checked before research and again after design.*

| Principle | How this feature meets it |
| --- | --- |
| I. NAF blocks | Streams are Collector's (`stream_telemetry`); the health rule is Observability's (`evaluate_health`); the Orchestrator composes them in the worker; Presentation reads. The dependency arrows are unchanged (R3). |
| II. Spec-driven | This spec, plan, contract and tasks exist before any code. |
| III. Test-first, live evidence | Every slice lists its tests first. The collector slice is a boundary change and is built against payloads captured from the lab. Faults not yet captured (an interface down, a paused device) are **Needs the lab** tasks, and each leaves a fixture behind. |
| V. Contract-first | One abstract method on `Collector` and one on `Observer`, with pydantic models, defined in PRs A and B before the runner uses them. |
| VI. Named seams | The SR Linux paths and parsing live in `GnmiCollector`, an existing seam. The runner, the health rule and every model are vendor-neutral. |
| VII. Simplicity | One fixed interval, one off switch, no new process, no new dependency. Stale series after an intent change are recorded, not engineered away (R5). |
| VIII. Intent-first | Health compares operational state with intent's expectations (VIII.3), and never promotes device state into intent. An interface intended down is expected down. Health never repairs (VIII.2 applies to drift; health has the single response "report"). |
| Honest claims | CPU and memory on the lab are the VM's, and the panels say so. The README's "no device telemetry" changes only once the lab has shown it. The eBGP underlay claim gets a caveat until #311 is fixed. |

No violation, so the Complexity Tracking table is empty.

## Project Structure

### Documentation (this feature)

```text
specs/016-device-telemetry/
├── spec.md
├── plan.md
├── research.md              # R1 to R11 (R1 measured on the lab)
├── data-model.md
├── contracts/telemetry.md   # figures, views, units, health, network, status
├── quickstart.md            # the lab checks
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
packages/collector/snapl_collector/
├── abc.py                   # Collector.stream_telemetry
├── models.py                # TelemetryTarget, *Sample
└── gnmi/
    ├── subscribe.py         # new: the thread-to-asyncio bridge over pygnmi's subscribe2
    └── collector.py         # stream_telemetry: SR Linux paths and parsing

packages/observability/snapl_observability/
├── abc.py                   # Observer.evaluate_health
├── health.py                # new: the rule (pure)
└── models.py                # DeviceHealthState, HealthReason, DeviceHealth, …

packages/orchestrator/snapl_orchestrator/
├── models.py                # AuditEventType.HEALTH_CHANGED and its validation
├── metrics.py               # the eight families and the counter-delta rule
├── telemetry/
│   ├── __init__.py
│   └── runner.py            # new: streams, ticks, intent refresh, health, audit
└── worker/run.py            # starts and stops the runner; SNAPL_TELEMETRY=off, SNAPL_TELEMETRY_USE_CASE (#341; unset means every use case since spec 008 FR-019)

packages/presentation/snapl_presentation/
├── dashboard/models.py      # views, units, DeviceHealthView, FabricHealth, FabricNetwork
├── dashboard/prometheus.py  # the six views; instant queries for /network
├── dashboard/projections.py # health per device, health episodes
├── dashboard/app.py         # /health, /network; DeviceView.health
├── dashboard/archive.py, snapshot.py
├── cli.py, render.py        # status's fabric health line
└── ui/src/…                 # health marks, drawer sections, "The network" group

tasks/history.py                         # archive the new routes
dev/scripts/serve_dashboard_fixtures.py  # synthetic health and network data
dev/scripts/capture_srlinux_telemetry.py # the capture used for R1 (added with this plan)
```

**Structure Decision**: each addition sits in the block that owns its kind. The only new modules are the subscription bridge, the health rule and the runner.

## Design

### Order of work

1. **Collector** (PR A): models, the abstract method, the bridge and the SR Linux parsing, tested on the captures.
2. **Observability** (PR B): the health rule, pure and table-tested.
3. **Orchestrator** (PRs C1, C2): the audit event and the metric families, then the runner and the bootstrap. After C2, a worker started on the lab streams and flags. That is the first point the lab can check anything, and C2's live evidence is a lab task.
4. **Presentation server** (PRs D1, D2): health from the trail, then the network from the store, each with its fixture-server data.
5. **UI** (PRs E1, E2): health on the page, then the drawer's network and the group.
6. **Archives and snapshots** (PR F).
7. **Docs and governance** (PR G).

A and B do not depend on each other. C needs both. D1 needs C1's event. D2 needs C1's metric names. E1 needs D1, E2 needs D2. F needs D1 and D2.

### What stays the same

- Every route, view, figure and projection from specs 010 to 015 (SC-007). A test runs every projection on the captured trail with health events interleaved.
- What a scan reads, what a deploy writes, and what the loop repairs.
- The scrape config: the figures come out of the worker's existing endpoint.

### Live evidence plan

The lab tasks, in quickstart.md order, are run by the maintainer or an attended session: capture an interface fault and a paused device as fixtures; with the worker on main, check SC-001, SC-003 (today: exactly the spines flagged), SC-004 (count the series), SC-005 (scan time with telemetry off and on) and SC-006; after #311 is fixed, SC-002 and SC-003's healthy fabric; then an archive across a fault (SC-008).

## Complexity Tracking

No constitution violation to justify.
