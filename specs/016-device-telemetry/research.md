# Research: Device Telemetry

Each entry gives the decision, why, and what else was weighed. R1 was measured on the lab on 2026-11-19. Entries marked **settle in PR X** are decided by measurement in that slice and written back here.

## R1. The paths and what they return (measured)

**Decision**: one sample-mode subscription per device, every 10 s, `json_ietf`, with one path per intent item:

| what | path (SR Linux v26.3.1) | value |
| --- | --- | --- |
| interface state | `/interface[name=<if>]/oper-state` | `"up"` / `"down"` |
| interface counters | `/interface[name=<if>]/statistics` | object; keep `in-octets`, `out-octets`, `in-error-packets`, `out-error-packets`, `in-discarded-packets`, `out-discarded-packets` (decimal strings) |
| session state | `/network-instance[name=default]/protocols/bgp/neighbor[peer-address=<ip>]/session-state` | `"established"`, `"active"`, `"idle"`, `"connect"`, `"opensent"`, `"openconfirm"` |
| prefixes received | `/network-instance[name=default]/protocols/bgp/neighbor[peer-address=<ip>]/afi-safi[afi-safi-name=ipv4-unicast]/received-routes` | integer |
| CPU | `/platform/control[slot=A]/cpu[index=all]/total/instant` | integer percent |
| memory | `/platform/control[slot=A]/memory/utilization` | integer percent |

**Measured** with `dev/scripts/capture_srlinux_telemetry.py` against leaf-01 and spine-01, 35 s each (fixtures in `tests/unit/test_collector/fixtures/telemetry/`):

- The first message carries every path with `sync_response`, about 60 ms after subscribing (320 ms on spine-01). Then one message per path every 10.0 s, all within 20 ms of each other.
- An update's `path` is module-prefixed and cut at the list entry. Asking for `/interface[name=ethernet-1/49]/oper-state` returns path `srl_nokia-interfaces:interface[name=ethernet-1/49]` and `val` `{"oper-state": "up"}`. The parser keys on the list entry (`interface[name=…]`, `neighbor[peer-address=…]`, `cpu`, `memory`) and reads the leaf from `val`, ignoring module prefixes.
- Counters are decimal strings. CPU, memory and `received-routes` are integers.
- `lo0`'s statistics are `{}`: a loopback has a state and no counters. So a loopback adds 1 series, not 7.
- spine-01's neighbour `10.10.1.1` is `active`, 0 routes, 0 messages ever: no leaf has BGP config (#311).
- Memory is `94` on every node, `physical` 16 820 461 568 on every node: the containers report the OrbStack VM. CPU `instant` read 3 to 5.

**Why per-item paths, not wildcards**: subscribing to `interface[name=*]` would bring `mgmt0` and every unused port, and the worker would have to drop them. Asking only for what intent names keeps the rest out at the source (US1 scenario 3, US3 scenario 3) and bounds the series by intent (FR-013).

**Alternatives**: `on_change` for the states (faster, but two modes to test, and the spec's 30 s is met by a 10 s sample); `target_defined` (the device picks, so the timing is not ours to state).

## R2. How a stream runs

**Decision**: pygnmi's `subscribe2` in one thread per device, bridged to asyncio with a queue (`loop.call_soon_threadsafe`). The stream is stopped by closing the client from the event loop, which ends the iterator with `StatusCode.CANCELLED` (measured: pygnmi's reader thread prints the traceback on every close; the bridge treats `CANCELLED` after a requested stop as a clean end, and logs any other end as a break).

**Why**: pygnmi is already the gNMI library (constitution, Technology Stack), and its subscription is a blocking generator. Six devices are six threads; each spends its time blocked on the socket.

**Amended in PR A (#319)**: pygnmi's stream subscriber reads the gRPC stream in a thread of its own. When the stream fails, that thread records the error on `subscriber.error` and stops, but a reader blocked on `next()` is never woken; a device that stops answering without closing the socket (a paused container) wakes nobody either. So the bridge does not block on the iterator. Its thread polls the subscriber's `peek()` and `error` every 100 ms and stops on a flag, which also makes a stop independent of `CANCELLED`. The async side raises `TelemetryStreamError` when no message arrives for the dial timeout plus three intervals (60 s at the defaults). The health rule's 30 s staleness (R6) is unaffected: it reads the last sample's time, not the stream's state.

**Alternatives**:
- *gnmic in Compose, exporting to Prometheus*: a new container and a new config language, and health could then only be computed by reading Prometheus back. Rejected.
- *A Temporal workflow per device with a long-running, heartbeating activity*: every sample would be activity heartbeat traffic, a worker restart would replay, and a stream is not a durable business process. Rejected.
- *Polling GET each interval*: the maintainer chose streaming (scope, 2026-11-18).

## R3. Where the streams live

**Decision**: in the worker process, beside the Temporal worker. `run_worker` starts a `TelemetryRunner` task next to `worker.run()` and stops it when the worker stops. `SNAPL_TELEMETRY=off` leaves it out (tests, and a worker started without the lab). No other setting: the interval is fixed at 10 s (Principle VII).

**Blocks (Principle I, V)**:
- **Collector** gains the stream: `Collector.stream_telemetry` (R4). The SR Linux paths and parsing live in `GnmiCollector`, an existing named seam (Principle VI). No new seam.
- **Observability** gains health: `Observer.evaluate_health`, a pure rule over intent's expectations and the latest samples (R6). This is Principle VIII.3, "intent extends beyond configuration", which snapl has not delivered until now.
- **Orchestrator** composes them, as it composes every block: it reads intent, runs the streams, asks for health, records changes in the audit trail and exports the figures.
- **Presentation** reads the audit trail and the metrics store, as it does today.

**Why not a separate process**: it would need its own intent client, audit writer and metrics port, and Prometheus a new target (the spec's Assumptions keep the worker's endpoint). One process keeps one audit writer.

Recorded as a decision in ADR-0005 (the governance slice).

## R4. The collector's contract

**Decision**: one new abstract method on `Collector`:

```python
def stream_telemetry(self, target: TelemetryTarget, *, interval_s: int) -> AsyncIterator[TelemetrySample]
```

`TelemetryTarget` is the device and the intent items to watch: interface names and BGP peer addresses. `TelemetrySample` is vendor-neutral: a device-level timestamp and zero or more `InterfaceSample`, `BgpSessionSample` and one optional `PlatformSample` (data-model.md). The iterator ends when the stream breaks, raising a new `TelemetryStreamError` (in `snapl_collector/exceptions.py`, beside `CollectorError`); restarting is the caller's job (R2, FR-003). `collect` reports a failure in its result instead, but a stream has no result to carry one.

**Why**: workflows and the runner never see a path or a module prefix (Principle VI). A second vendor implements the same method.

**Alternatives**: a path-based `subscribe(device, paths)` returning raw updates. Then the runner would parse SR Linux shapes outside a named seam. Rejected.

## R5. Exporting the figures

**Decision**: the worker's existing endpoint (18091), through the Temporal runtime's own meter (`Runtime.metric_meter`), which has gauges and counters (checked: `temporalio` 1.25 `MetricMeter` has `create_gauge`, `create_gauge_float`, `create_counter`). No new dependency.

| metric | type | labels | value |
| --- | --- | --- | --- |
| `snapl_interface_up` | gauge | `device`, `use_case`, `interface` | 1 up, 0 otherwise |
| `snapl_interface_octets_total` | counter | `device`, `use_case`, `interface`, `direction` | octets |
| `snapl_interface_errors_total` | counter | same | error packets |
| `snapl_interface_discards_total` | counter | same | discarded packets |
| `snapl_bgp_session_established` | gauge | `device`, `use_case`, `peer` | 1 established, 0 otherwise |
| `snapl_bgp_prefixes_received` | gauge | `device`, `use_case`, `peer` | received routes |
| `snapl_device_cpu_percent` | gauge | `device`, `use_case` | percent |
| `snapl_device_memory_percent` | gauge | `device`, `use_case` | percent |

`direction` is `in` or `out`. `interface` and `peer` come from intent only (the target), never from the device's answer. The count per interface is 1 + 6 = 7, per session end 2, per device 2 (FR-013).

**Counters from cumulative values (FR-014)**: the device sends totals; the meter's counter takes increments. The runner keeps the last total per series and adds `new − last`, or `new` when `new < last` (the device's counter restarted). The first total after the worker or the stream starts adds 0, so a worker restart never counts a device's lifetime as one step. So `rate()` never sees a negative or a spike.

**Stale series**: the runtime's meter cannot drop a series. An interface or session removed from intent keeps its last values until the worker restarts. The drawer and health read only what intent names, so they are right. A removed session's last `established` of 1 still counts in `bgp-established` until then. Intent changes are rare in this lab, and the view's note says so. Recorded, not solved.

**Settle in PR C1**: the labels the runtime adds to a runtime-level meter (an activity's meter adds `activity_type`, `namespace`, `task_queue`; this one may add only `service_name`), and that a counter keeps its `_total` name (spec 015 R6 says the runtime adds none).

**Settled in PR C1 (#323)**, measured on a `temporalio` 1.25 runtime: the runtime's own meter adds one label, `service_name="temporal-core-sdk"`, and nothing else. A counter is served under the name it is created with, `snapl_interface_octets_total`, with no second `_total`. Every family is typed as created (`# TYPE … gauge` or `counter`). `test_worker_metrics.py` holds all three.

## R6. The health rule

**Decision**: evaluated by the runner every interval (a tick, not only on a sample, so silence is noticed), by `Observer.evaluate_health`:

1. No sample for longer than 3 intervals (30 s), or never: `unknown`, reason "no telemetry since <time>" (or "no telemetry yet"). FR-007.
2. Otherwise, a **fault** is an expected item whose latest state is not its expected state and has not been for longer than 1 interval (10 s, the debounce). FR-006.
   - an interface with `enabled: true` in intent is expected `up`. One with `enabled: false` is expected `down`, and being up is not a health fault (the scan reports it as drift).
   - a BGP session end with `enabled: true` is expected `established`. A disabled session has no expectation.
   - an item intent names but the device never reported is a fault, with actual state `absent` (a misspelt interface would otherwise read healthy forever).
3. Any fault: `unhealthy`, with one reason per fault ("ethernet-1/49 down (expected up)", "BGP 10.10.1.1 active (expected established)"). None: `healthy`.
4. CPU and memory are never read by the rule (FR-008).

A link that is down usually takes its session down too. Both are listed: the rule does not guess at cause.

**Change only**: the runner records a `health_changed` audit event when the state or the set of reasons changes (R7). A flapping link writes at most one event per change, and the debounce keeps a single lost sample from writing any.

**Start and stop**: at start the runner reads each device's last recorded health from the audit trail, so a restart does not write a change that did not happen. A clean stop records each device `unknown`, "telemetry stopped". A crash records nothing; the live dashboard shows health `unknown`, "the worker is not running" whenever its worker view has no pollers (data-model.md).

## R7. Health in the audit trail

**Decision**: a new `AuditEventType.HEALTH_CHANGED`:

- `workflow_id` `telemetry:<device name>`, `workflow_type` `DeviceTelemetry`, `target_id` the device id, `actor` `worker`, `outcome` and `reason` unset.
- `payload`: `before`, `after` (each a `DeviceHealthState`), `reasons` (a list of `{kind, item, expected, actual}`), `last_sample_at`.
- The model's validator requires `before` and `after` from the enum and an `actor`, as `schedule_changed` does.

`snapl audit --device leaf-01` shows them with no change, since it reads by `target_id`.

**Existing projections ignore it**: they select on `workflow_type` (`ScanDrift`, `DeployIntent`, `DriftResponse`) and on `WORKFLOW_STARTED`. A test interleaves health events through the captured trail and checks that every projection answers the same as without them (SC-007).

**Concurrency**: the runner and the activities share the worker's one `SqliteAuditLog`, whose `append` already serialises writes through an `asyncio.Lock` (checked 2026-11-19). The runner gets the same instance from the bootstrap, never a second writer.

## R8. The read API

**Decision** (contracts/telemetry.md):

- `DeviceView` gains an optional `health`. A server or an archive with no health event for a device answers `null`.
- `GET /api/v1/health`: each device's current health and its health episodes over a range, as `drift-timeline` does for drift. The source is the audit trail.
- `GET /api/v1/network`: every device's intent interfaces and sessions with their latest state and values, and its CPU and memory, from instant queries to the metrics store. One route for the fabric, not one per device: six devices are a small answer, and an archive records one answer per route. Unavailable with a reason when the store is down (US1 scenario 4).
- Six named metrics views in a new group: `interface-traffic`, `interface-errors`, `interface-discards`, `bgp-established`, `device-cpu`, `device-memory`.
- Two new units: `bytes_per_second` and `percent`.
- `DeviceHealthState` joins the enums consumers render raw when unknown (010's versioning rule, amended as 011 and 012 did).

**Alternatives**: health computed by the dashboard from the metrics store. Then archives would need the raw series to show health, and two places would hold the rule. Rejected: the audit trail is already what archives carry.

## R9. Rates for the views

`interface-traffic` is `sum by (device) (rate(snapl_interface_octets_total{use_case="$uc"}[$w]))` in bytes per second (by device and direction until 2026-11-20, #312: see contracts/telemetry.md). `rate()` is #241's first-observation form, as spec 015's views use. Errors and discards are by `device, interface`, in packets per second (`per_second`). The R5 delta rule already removes resets, so `rate()`'s own reset handling is a second guard, not the only one.

## R10. Scans while streaming (SC-005)

Streams hold their own gRPC connections. A scan's GET dials separately, as today. SR Linux's gNMI server serves both. The measurement is the lab task for SC-005, as spec 015 T044 measured scan time: the median of at least 10 scheduled scans with telemetry off, then on.

## R11. The page

The drawer grows by a list of 3 to 5 interfaces and 0 to 4 sessions, and two small charts. "The network" group is folded by default. The page height issue (#305) is not made worse beyond that. The look check runs on the drawer of spine-01 (5 interfaces, 4 sessions: the longest).

## R12. Snapshot and archive size

**Measured in PR F (T052)**: a seven-day snapshot's bundle on the fixture app (`serve_dashboard_fixtures.build_app(up=True)`, `collect_bundle(days=7)`), with a stub template, as spec 015 R8 measured it:

| | Bytes | Against before |
|---|---:|---:|
| Before PR F (`1a03d23`, spec 016 PR E2 merged) | 267,413 | |
| After PR F: `/health`, `/network` and the six views at a 3 h step | 416,171 | +56% |

Of the 148,758 bytes added, the six views are 138,267: `interface-errors` and `interface-discards` 38,964 and 38,966 (16 series each), `interface-traffic` 15,790, `device-memory` 15,054, `bgp-established` 14,784 and `device-cpu` 14,709. `/network` is 6,097 and `/health` 4,260. The views follow spec 015's rule (`COARSE_VIEWS`): the smallest step on the ladder that keeps at most 60 points per series, 3 h for seven days, said in each view's note. Spec 016 sets no size limit, so nothing is cut further; the errors and discards views are half the growth if one is wanted.

An archive exports the six views at the page's step over seven days, as it does every view, and `/health` and `/network`. Its size is dominated by the Prometheus snapshot, which archive mode never reads.
