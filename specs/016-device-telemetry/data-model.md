# Data Model: Device Telemetry

The served models are in [contracts/telemetry.md](contracts/telemetry.md). This file holds the models inside the blocks and the rules the contract does not state. Every model is frozen; every datetime is aware UTC.

## Collector (`snapl_collector/models.py`)

| model | fields | notes |
| --- | --- | --- |
| `TelemetryTarget` | `device: Device`, `interfaces: tuple[str, ...]`, `peers: tuple[str, ...]` | What intent names on one device. Built by the runner from `DesiredState`. Two targets are equal when the same items are named, which is how the runner tells that intent changed (FR-004). |
| `InterfaceSample` | `name`, `oper_up: bool \| None`, `in_octets`, `out_octets`, `in_errors`, `out_errors`, `in_discards`, `out_discards` (each `int \| None`) | `None` means this message did not carry that leaf. A loopback never carries counters. |
| `BgpSessionSample` | `peer`, `state: str \| None`, `prefixes_received: int \| None` | `state` is the device's word (`established`, `active`, …), lower-cased. |
| `PlatformSample` | `cpu_percent: int \| None`, `memory_percent: int \| None` | |
| `TelemetrySample` | `device_name`, `timestamp`, `interfaces: tuple[InterfaceSample, ...]`, `sessions: tuple[BgpSessionSample, ...]`, `platform: PlatformSample \| None`, `sync: bool` | One gNMI message. `timestamp` is the device's, from the notification. `sync` marks the first, complete message. |

Rules:
- An update for an item the target does not name is dropped in the collector (it should not arrive, R1, but a device may send it).
- A value that does not parse (a non-numeric counter) is `None` and logged once per stream, never raised.

## Observability (`snapl_observability/models.py`)

| model | fields |
| --- | --- |
| `DeviceHealthState` | `StrEnum`: `healthy`, `unhealthy`, `unknown` |
| `HealthReasonKind` | `StrEnum`: `interface`, `bgp_session`, `no_telemetry` |
| `HealthReason` | `kind`, `item: str \| None` (interface name or peer), `expected: str \| None`, `actual: str \| None`, `text: str` |
| `HealthExpectation` | `device_name`, `interfaces_up: frozenset[str]`, `interfaces_down: frozenset[str]`, `sessions_established: frozenset[str]` |
| `ObservedItem` | `state: str`, `since: datetime` (when it first read this state) |
| `HealthObservation` | `last_sample_at: datetime \| None`, `interfaces: Mapping[str, ObservedItem]`, `sessions: Mapping[str, ObservedItem]` |
| `DeviceHealth` | `device_name`, `state: DeviceHealthState`, `reasons: tuple[HealthReason, ...]`, `evaluated_at` |

`Observer.evaluate_health(expectation, observation, *, now, interval_s) -> DeviceHealth` applies research R6. It is pure: no clock, no I/O.

Reason texts, fixed so the dashboard, the CLI and the audit trail say the same thing:

| kind | text |
| --- | --- |
| `interface` | `<name> <actual> (expected <expected>)`; `actual` is `up`, `down` or `absent` |
| `bgp_session` | `BGP <peer> <actual> (expected established)` |
| `no_telemetry` | `no telemetry since <ISO time>`, or `no telemetry yet` |

Reasons are ordered: interfaces by name, then sessions by peer.

## Orchestrator

### `AuditEventType.HEALTH_CHANGED` (`models.py`)

| field | value |
| --- | --- |
| `workflow_id` | `telemetry:<device name>` |
| `workflow_type` | `DeviceTelemetry` |
| `target_id` | the device's id |
| `actor` | `worker` (required) |
| `payload.before`, `payload.after` | `DeviceHealthState` values (required) |
| `payload.reasons` | list of `{kind, item, expected, actual, text}` for `after` |
| `payload.last_sample_at` | ISO time or `null` |

### The runner (`snapl_orchestrator/telemetry/runner.py`)

State per device:

| state | meaning |
| --- | --- |
| `connecting` | dialling, or waiting out a backoff |
| `streaming` | a sync message arrived and the stream is open |
| `broken` | the stream ended; next attempt after the backoff |

Transitions: `connecting → streaming` on the first `sync` sample; `streaming → broken` when the iterator raises or ends; `broken → connecting` after the backoff (1 s, doubling, capped at 30 s, reset on `streaming`). A broken stream never touches another device's stream, a scan or a deploy (FR-003).

Every 10 s the runner:
1. evaluates each device's health and records a change (R6, R7);
2. every sixth tick (60 s), re-reads intent; a device whose `TelemetryTarget` changed has its stream restarted, a device that left intent has it stopped, a new one has it started (FR-004).

Counter deltas: `last_totals[(device, interface, leaf)]`, reset when a stream restarts (R5).

### Metrics (`metrics.py`)

The eight families in research R5. As spec 015 does, `metrics.py` is the only place their names and labels appear, and one test per family holds its label set. `direction` is an enum (`in`, `out`). No recorder raises.

## Presentation

### Health on a device

`DeviceView.health: DeviceHealthView | None`, from the device's latest `health_changed` event. Rules:
- No event for the device: `null` (an older archive, or a worker that has never streamed).
- The live server, with its worker view showing no pollers: state `unknown`, reason `the worker is not running`, `since` the worker view's time. An archive or a snapshot shows what was recorded.
- `in_sync` counts and every drift figure ignore health (FR-010).

### Health episodes

An episode opens when a device becomes `unhealthy` and closes when it leaves `unhealthy`. `unknown` closes an episode (we no longer know) and opens none. Episodes are clipped to the range, like drift episodes.

### UI (`packages/presentation/ui/src/lib/`)

| group | views |
| --- | --- |
| The network | `interface-traffic`, `interface-errors`, `interface-discards`, `bgp-established`, `device-cpu`, `device-memory` |

It comes after spec 015's four groups, folded by default. Titles end as spec 015's do: `(rate)` for traffic, errors and discards, `(count)` for sessions established, `(current value)` for CPU and memory. On the lab, the CPU and memory panels carry the note "On the lab, this is the host VM's, the same on every device" (spec Assumptions).

Health on the page:
- **Fabric panel**: each device's chip gains a health mark beside its drift colour: none when healthy, a red "!" when unhealthy, a grey "?" when unknown. The drift colour does not change.
- **Number row**: the in-sync card's context names unhealthy devices ("6 of 6 in sync · 2 unhealthy"), or "all healthy".
- **Drawer**: a "Health" section first (state, reasons, since), then "Interfaces" and "BGP sessions" tables from the device's entry in `/network`, then CPU and memory over the range.
