# Contract: Device Telemetry (additions to v1)

**Feature**: 016-device-telemetry | **Extends**: [015's metrics views](../../015-more-metrics/contracts/metrics-views.md), [012's metrics API](../../012-measured-metrics/contracts/metrics-api.md) and [010's dashboard API](../../010-dashboard-read-api/contracts/dashboard-api.md)

Every addition is **compatible** under 010's versioning rules: an optional field, two routes, new enum values and new models. `DeviceHealthState` joins the enums whose unknown values consumers render as raw strings (010's rule is amended in the governance slice, as 011 and 012 did).

## The worker's figures (endpoint 18091)

| metric | type | labels |
| --- | --- | --- |
| `snapl_interface_up` | gauge, 1 or 0 | `device`, `use_case`, `interface` |
| `snapl_interface_octets_total` | counter | `device`, `use_case`, `interface`, `direction` |
| `snapl_interface_errors_total` | counter | `device`, `use_case`, `interface`, `direction` |
| `snapl_interface_discards_total` | counter | `device`, `use_case`, `interface`, `direction` |
| `snapl_bgp_session_established` | gauge, 1 or 0 | `device`, `use_case`, `peer` |
| `snapl_bgp_prefixes_received` | gauge | `device`, `use_case`, `peer` |
| `snapl_device_cpu_percent` | gauge | `device`, `use_case` |
| `snapl_device_memory_percent` | gauge | `device`, `use_case` |

- `device`, `interface` and `peer` come from intent. `direction` is `in` or `out`. Nothing else is a label (FR-013).
- The runtime adds one label to every family: `service_name="temporal-core-sdk"` (settled in PR C1). Counters are served under the names above, with no second `_total`.
- Budget for dcfabric: 16 links × 7 + 6 loopbacks × 1 + 16 session ends × 2 + 6 devices × 2 = 162 series measured from R1's shapes, under the spec's ceiling of 198 (a loopback has no counters).

## `MetricsViewName` (new values)

`$uc` and `$w` are as in spec 015. `rate()` is #241's first-observation form, as in spec 015.

| view | query (template) | unit | series labels |
| --- | --- | --- | --- |
| `interface-traffic` | `sum by (device, direction) (rate(snapl_interface_octets_total{use_case="$uc"}[$w]))` | `bytes_per_second` | `device`, `direction` |
| `interface-errors` | `sum by (device, interface) (rate(snapl_interface_errors_total{use_case="$uc"}[$w]))` | `per_second` | `device`, `interface` |
| `interface-discards` | `sum by (device, interface) (rate(snapl_interface_discards_total{use_case="$uc"}[$w]))` | `per_second` | `device`, `interface` |
| `bgp-established` | `sum by (device) (snapl_bgp_session_established{use_case="$uc"})` | `count` | `device` |
| `device-cpu` | `max by (device) (snapl_device_cpu_percent{use_case="$uc"})` | `percent` | `device` |
| `device-memory` | `max by (device) (snapl_device_memory_percent{use_case="$uc"})` | `percent` | `device` |

`bgp-established` is a gauge count, so it has no window note (as spec 015's `worker-slots`). Its note says a session removed from intent keeps counting until the worker restarts (research R5), when it does.

## `MetricUnit` (new values)

- `bytes_per_second`
- `percent`: 0 to 100

Consumers that don't know a unit show it raw.

## `DeviceView.health` (new optional field)

```text
DeviceHealthView {
  state: DeviceHealthState          # healthy | unhealthy | unknown
  reasons: list[HealthReasonView]   # empty when healthy
  since: datetime                   # when this state was recorded
  last_sample_at: datetime | null
}
HealthReasonView { kind: "interface" | "bgp_session" | "no_telemetry" | str, item: str | null, expected: str | null, actual: str | null, text: str }
```

`null` when no health has been recorded for the device. The rules for the live server are in data-model.md ("the worker is not running").

## `GET /api/v1/health`

| query | default | |
| --- | --- | --- |
| `start` | `end − 24h` | aware datetime |
| `end` | now | aware datetime |
| `use_case` | the server's | `422` if it isn't the server's |

```text
HealthResponse = Envelope + { health: View[FabricHealth] }
FabricHealth {
  start, end: datetime
  devices: list[DeviceHealthEntry]      # devices in intent, by name
  first_event_at: datetime | null       # the first health event in the trail
}
DeviceHealthEntry {
  device_id: UUID, name: str
  current: DeviceHealthView | null      # at `end`
  episodes: list[HealthEpisode]         # unhealthy spans overlapping the range
}
HealthEpisode { opened_at, closed_at: datetime | null, duration_s: int | null, reasons: list[HealthReasonView] }
```

- `422` when `start >= end` or the range exceeds the drift timeline's limit.
- The source is `audit`. With the audit trail down, the view is unavailable with its reason and remedy.
- An episode's `reasons` are those recorded when it opened. Its later changes in reasons are not listed (the audit trail has them).

## `GET /api/v1/network`

```text
NetworkResponse = Envelope + { network: View[FabricNetwork] }
FabricNetwork { at: datetime, devices: list[DeviceNetwork] }
DeviceNetwork {
  device_id: UUID, name: str
  interfaces: list[InterfaceState]   # intent's, by name
  sessions: list[SessionState]       # intent's, by peer
  cpu_percent: float | null
  memory_percent: float | null
}
InterfaceState {
  name: str, expected: "up" | "down", up: bool | null,
  in_bps: float | null, out_bps: float | null,      # bytes per second over $w
  in_errors_ps: float | null, out_errors_ps: float | null,
  in_discards_ps: float | null, out_discards_ps: float | null
}
SessionState { peer: str, expected: "established" | null, established: bool | null, prefixes_received: int | null }
```

- Items come from intent (the SoT, as `/devices` reads it). Values come from instant queries at `at` (now). An item with no series is `null` throughout, never 0.
- The source is `prometheus`. With the store down, the view is unavailable with its reason and remedy, as the metrics views are (US1 scenario 4).
- `at` is accepted in a later slice only if the archive needs it (PR F).

## Audit: `health_changed`

The event and its payload are in data-model.md. `GET /api/v1/runs` and every other existing route answer as before when these events are in the trail (SC-007).

## `snapl status`

Adds one line after the checks, `fabric health`:

```text
fabric health   4 healthy, 2 unhealthy (spine-01, spine-02), 0 unknown
```

It reports the network, not snapl, so it is never a failed check and never changes the exit code. `--json` adds `"health": {"healthy": [...], "unhealthy": [...], "unknown": [...]}` by device name. With no health recorded, the line says `no telemetry recorded`.
