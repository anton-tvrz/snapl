# Data Model: Designed Dashboard UI

New pydantic models live in `snapl_presentation/dashboard/models.py`, next to 010's and 012's, and follow their rules (frozen, `extra="forbid"`, aware UTC datetimes). They are part of the v1 contract and appear in the OpenAPI snapshot. Every addition is compatible under 010's versioning rules.

The UI's types are generated from that snapshot (research R5). The types under "In the browser" are the UI's own and are not part of the contract.

## Topology (research R6)

```python
class TopologyNode(_Model):
    device_id: UUID
    name: str
    role: str

class LinkEnd(_Model):
    device: str       # a device name
    interface: str

class TopologyLink(_Model):
    # Ends are ordered by (device, interface), so one link has one identity.
    a: LinkEnd
    b: LinkEnd
    declared_by: Literal["both", "a", "b"]
    # True only when both ends name each other's device and interface.
    agreed: bool

class UnlinkedInterface(_Model):
    device: str
    interface: str
    # The peer as intent declares it; not a device of this use case.
    peer_device: str
    peer_interface: str | None

class Topology(_Model):
    nodes: list[TopologyNode]          # sorted by role tier, then name
    links: list[TopologyLink]          # sorted by (a, b)
    unlinked: list[UnlinkedInterface]

class TopologyResponse(Envelope):
    topology: View[Topology]           # source: sot
```

Rules:
- `agreed` is false whenever `declared_by` is not `both`.
- A link's two ends are different devices. An interface that names its own device as peer goes in `unlinked`.

`IntentDevice` (`id`, `name`, `role`) is an internal record, not a contract model. `compose_devices` and the name lookup read it instead of `DesiredState`, so archive mode can supply it from an exported topology (research R6, R13).

## Drift timeline (research R10)

```python
class ScanPoint(_Model):
    t: UtcDatetime                     # the per-device scan record's timestamp
    state: DriftState                  # never `never_scanned`
    missing_paths: list[str] = []
    undesired_paths: list[str] = []
    unclassified_paths: list[str] = []
    error: str | None = None
    scan_workflow_id: str

class DeviceLane(_Model):
    device_id: UUID
    name: str
    in_intent: bool
    points: list[ScanPoint]            # in the range, oldest first

class EpisodeClose(_Model):
    kind: Literal["remediation", "reconcile", "deploy"]
    workflow_id: str                   # the DeployIntent run
    trigger: Trigger
    ended_at: UtcDatetime

class DriftEpisode(_Model):
    device_id: UUID
    name: str
    opened_at: UtcDatetime             # first scan that found it not in sync
    closed_at: UtcDatetime | None      # first later scan that found it in sync
    duration_s: int | None             # closed_at - opened_at; None while open
    states: list[DriftState]           # distinct states seen, in order of first appearance; `error` included, the closing `in_sync` not
    scans: int                         # scans from the opening one to the closing one, both included
    closed_by: EpisodeClose | None     # None while open, or when no deploy targeted the device

class DriftTimeline(_Model):
    start: UtcDatetime
    end: UtcDatetime
    lanes: list[DeviceLane]            # tier order, as in Topology when names are known
    episodes: list[DriftEpisode]       # every episode overlapping the range, oldest first
    markers: list[Marker]              # 010's Marker: reconcile and remediation runs
    latest_closed: DriftEpisode | None # in the whole trail up to `end`
    open_count: int                    # episodes open as of `end`
    first_event_at: UtcDatetime | None

class DriftTimelineResponse(Envelope):
    timeline: View[DriftTimeline]      # source: audit
```

Rules (each is a unit test):
- An episode opens on `missing`, `undesired`, `missing_and_undesired` or `drifted`. It closes on `in_sync`. `error` neither opens nor closes one.
- A device repaired and drifted again has two episodes.
- `closed_by` is the latest successful `DeployIntent` run for the device with `last drifted scan < ended_at < closed_at`. Its `kind` follows the run's trigger and parent (010 research R5).
- `duration_s` is set exactly when `closed_at` is.
- `closed_by` is None whenever `closed_at` is None.
- More than 20,000 scan points in the range is a `422`, not a truncated answer.
- The range is `[start, end]`, and nothing recorded after `end` is used. The answer is the timeline as of `end`: an episode that closed later is returned open. *Added in PR B*, so the "as of" control can ask for a past `end`.

## Run steps (research R15)

```python
class NafBlock(StrEnum):
    INTENT = "intent"
    EXECUTOR = "executor"
    COLLECTOR = "collector"
    OBSERVABILITY = "observability"
    ORCHESTRATOR = "orchestrator"
    PRESENTATION = "presentation"

class BlockStatus(StrEnum):
    DONE = "done"
    FAILED = "failed"
    NOT_REACHED = "not_reached"
    NOT_RECORDED = "not_recorded"   # this run type records no steps one by one
    SURFACE = "surface"             # Presentation: what is being looked at

class RunStep(_Model):
    name: str                       # the activity name as recorded
    block: NafBlock | None          # None for a name the server does not map
    outcome: Literal["done", "failed"]
    reason: str | None = None
    t: UtcDatetime

class BlockState(_Model):
    block: NafBlock
    status: BlockStatus
    reason: str | None = None       # on FAILED
    t: UtcDatetime | None = None    # when its step was recorded

class RunSteps(_Model):
    run: RunView
    recorded: bool                  # True for DeployIntent
    steps: list[RunStep]            # as recorded, oldest first
    blocks: list[BlockState]        # always six, in loop order

class RunStepsResponse(Envelope):
    steps: View[RunSteps]           # source: audit; data None when no such run
```

`NafBlock` and `BlockStatus` join the open enums: a consumer renders an unknown value as its raw text.

Loop order of `blocks`: intent, executor, collector, observability, orchestrator, presentation.

## Mode (research R13)

```python
class ArchivePart(_Model):
    name: str
    present: bool
    reason: str | None = None

class ArchiveInfo(_Model):
    name: str                       # the file's name, not its path
    created_at: UtcDatetime
    snapl_commit: str | None
    parts: list[ArchivePart]

class ModeResponse(Envelope):
    mode: Literal["live", "archive"]
    # The server's "now": the wall clock when live, the archive's creation time otherwise.
    now: UtcDatetime
    archive: ArchiveInfo | None = None
```

`archive` is set exactly when `mode` is `archive`.

## Query parameters added to existing routes (research R11)

| route | parameter | meaning |
| --- | --- | --- |
| `/api/v1/devices` | `at` | project from events with `timestamp <= at` |
| `/api/v1/runs` | `at` | the same; an open run is `running` if it ended after `at`, else `unfinished` |

`at` must carry an offset and must not be after the server's now, else `422`.

## Archive additions (research R13)

`export/` gains `topology.json`, `devices.json`, `runs.json` and `drift-timeline.json`, each the API response as served. `manifest.json` and its `format` are unchanged. A reader treats a missing file as "not in this archive".

## In the browser

```ts
type DataMode = "live" | "archive" | "snapshot";

interface DataProvider {
  mode(): Promise<ModeInfo>;                       // DataMode, now, archive or snapshot info
  overview(): Promise<Overview>;
  devices(at?: Date): Promise<DevicesResponse>;
  runs(q: { at?: Date; device?: string; limit?: number }): Promise<RunsResponse>;
  topology(): Promise<TopologyResponse>;
  timeline(range: Range): Promise<DriftTimelineResponse>;
  steps(run: RunId): Promise<RunStepsResponse>;
  metrics(view: MetricsViewName, range: Range): Promise<MetricsResponse>;
  links(): Promise<LinksResponse>;
  sources(): Promise<SourcesResponse>;
}
```

- `HttpProvider` calls the API. It serves live and archive mode alike, because archive mode is a server mode.
- `InlineProvider` reads the bundle embedded in a snapshot file.

```ts
interface SnapshotBundle {
  manifest: { format: "snapl-snapshot/1"; created_at: string; snapl_commit: string | null;
              use_case: string; start: string; end: string; source: "live" | "archive" };
  overview: Overview; devices: DevicesResponse; runs: RunsResponse;
  topology: TopologyResponse; timeline: DriftTimelineResponse;
  steps: Record<string, RunStepsResponse>;         // keyed by `${workflow_id}@${started_at}`
  links: LinksResponse; sources: SourcesResponse;
  metrics: Partial<Record<MetricsViewName, MetricsResponse>>;
}
```

Other UI-only types, each with its own module and test:

| type | module | what it is |
| --- | --- | --- |
| `Encoding` | `src/lib/encoding.ts` | per drift state: colour token, shape, icon, label (research R17) |
| `Placed` | `src/lib/layout.ts` | node positions and link paths from `layoutTiers` (research R7) |
| `Activity` | `src/lib/activity.ts` | per device: `active`, `recent` with outcome, or none (research R9) |
| `LaneSegment` | `src/lib/lanes.ts` | drawn stretches from scan points, with the 15-minute hold and no-data gaps |
| `UrlState` | `src/lib/urlState.ts` | `device`, `at`, `from`, `to`, `run` (research R12) |
