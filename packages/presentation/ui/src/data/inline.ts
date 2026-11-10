// The data of a snapshot file (spec 011 research R14): the API's answers,
// bundled into the page by `snapl dashboard --snapshot`. A page opened from
// disk cannot ask a server, so the past states the server would compute are
// computed here from the bundled lanes and runs. inline.test.ts checks them
// against the server's own answers (SC-004).
import type {
  DeviceLane,
  DevicesResponse,
  DeviceView,
  DriftEpisode,
  DriftTimeline,
  DriftTimelineResponse,
  HistoryResponse,
  LinksResponse,
  MetricsResponse,
  MetricsViewName,
  ModeResponse,
  Overview,
  RunStepsResponse,
  RunsResponse,
  RunView,
  SourcesResponse,
  TopologyResponse,
} from "../api/types";
import { pointTime } from "../lib/lanes";
import { HttpProvider } from "./http";
import type { DataProvider, Range, RunId, RunsQuery } from "./provider";

export const SNAPSHOT_FORMAT = "snapl-snapshot/1";

export interface SnapshotBundle {
  manifest: {
    format: string;
    created_at: string;
    snapl_commit: string | null;
    use_case: string;
    start: string;
    end: string;
    source: "live" | "archive";
  };
  overview: Overview;
  devices: DevicesResponse;
  runs: RunsResponse;
  topology: TopologyResponse;
  timeline: DriftTimelineResponse;
  history: HistoryResponse;
  steps: Record<string, RunStepsResponse>;
  links: LinksResponse;
  sources: SourcesResponse;
  metrics: Partial<Record<MetricsViewName, MetricsResponse>>;
}

/** The bundle in the page's `snapl-bundle` element, or null on a served page. */
export function readBundle(doc: Document): SnapshotBundle | null {
  const text = doc.getElementById("snapl-bundle")?.textContent?.trim();
  return text ? (JSON.parse(text) as SnapshotBundle) : null;
}

const at = (iso: string | null | undefined) => (iso ? pointTime(iso) : Number.POSITIVE_INFINITY);

/** The last scan point recorded by `t`: the server's device view holds it with no time limit. */
function lastPoint(lane: DeviceLane | undefined, t: number) {
  let last;
  for (const point of lane?.points ?? []) {
    if (pointTime(point.t) > t) break;
    last = point;
  }
  return last;
}

/** A run as it stood at `t`: open runs are still running then. */
function runAt(run: RunView, t: number): RunView {
  if (at(run.ended_at) <= t) return run;
  return { ...run, state: "running", ended_at: null, duration_s: null, reason: null };
}

/** A timeline as answered with its end at `end`: episodes that closed later are open then. */
function timelineUntil(timeline: DriftTimeline, end: number): DriftTimeline {
  const episodes: DriftEpisode[] = timeline.episodes
    .filter((e) => pointTime(e.opened_at) <= end)
    .map((e) => (at(e.closed_at) <= end ? e : { ...e, closed_at: null, closed_by: null, duration_s: null }));
  const closed = [...episodes, ...(timeline.latest_closed ? [timeline.latest_closed] : [])]
    .filter((e) => at(e.closed_at) <= end)
    .sort((a, b) => at(b.closed_at) - at(a.closed_at));
  return {
    ...timeline,
    end: new Date(end).toISOString(),
    lanes: timeline.lanes.map((lane) => ({ ...lane, points: lane.points.filter((p) => pointTime(p.t) <= end) })),
    markers: timeline.markers.filter((m) => pointTime(m.t) <= end),
    episodes,
    latest_closed: closed[0] ?? null,
    open_count: episodes.filter((e) => !e.closed_at).length,
  };
}

/** The `DataProvider` of a snapshot: every answer comes from the bundle. */
export class InlineProvider implements DataProvider {
  readonly #bundle: SnapshotBundle;

  constructor(bundle: SnapshotBundle) {
    const format = bundle.manifest?.format;
    if (format !== SNAPSHOT_FORMAT) {
      throw new Error(`This snapshot is in format ${format}; this page reads ${SNAPSHOT_FORMAT}.`);
    }
    this.#bundle = bundle;
  }

  mode = async (): Promise<ModeResponse> => {
    const { manifest } = this.#bundle;
    return {
      contract_version: "1",
      generated_at: manifest.end,
      use_case: manifest.use_case,
      mode: "snapshot",
      now: manifest.end,
      // The recording's description, in the shape the archive banner reads.
      archive: {
        name: "snapshot",
        created_at: manifest.created_at,
        snapl_commit: manifest.snapl_commit,
        parts: [],
        metrics_start: manifest.start,
        metrics_end: manifest.end,
      },
    };
  };

  overview = async () => this.#bundle.overview;
  sources = async () => this.#bundle.sources;
  links = async () => this.#bundle.links;
  topology = async () => this.#bundle.topology;
  history = async () => this.#bundle.history;

  devices = async (when?: Date): Promise<DevicesResponse> => {
    const answer = this.#bundle.devices;
    if (!when) return answer;
    const t = when.getTime();
    const lanes = new Map((this.#bundle.timeline.timeline.data?.lanes ?? []).map((lane) => [lane.name, lane]));
    const deploys = (this.#bundle.runs.runs.data ?? []).filter(
      (run) => run.workflow_type === "DeployIntent" && at(run.ended_at) <= t,
    );
    const devices = (answer.devices.data ?? []).map((device): DeviceView => {
      const point = lastPoint(lanes.get(device.name), t);
      const deploy = deploys.find((run) => run.target === device.name);
      return {
        ...device,
        drift_state: point?.state ?? "never_scanned",
        missing_paths: point?.missing_paths ?? [],
        undesired_paths: point?.undesired_paths ?? [],
        unclassified_paths: point?.unclassified_paths ?? [],
        error: point?.error ?? null,
        last_scanned_at: point?.t ?? null,
        last_scan_workflow_id: point?.scan_workflow_id ?? null,
        last_deploy: deploy
          ? { workflow_id: deploy.workflow_id, success: deploy.state === "succeeded", ended_at: deploy.ended_at! }
          : null,
        // The command and note are the ones for the state at the bundle's time.
        suggested_command: point?.state === device.drift_state ? device.suggested_command : null,
        note: point?.state === device.drift_state ? device.note : null,
      };
    });
    return { ...answer, devices: { ...answer.devices, data: devices } };
  };

  runs = async (query: RunsQuery): Promise<RunsResponse> => {
    const answer = this.#bundle.runs;
    if (!query.at && !query.device) return answer;
    const t = query.at?.getTime() ?? Number.POSITIVE_INFINITY;
    const runs = (answer.runs.data ?? [])
      .filter((run) => pointTime(run.started_at) <= t)
      .filter((run) => !query.device || run.target === query.device)
      .map((run) => (query.at ? runAt(run, t) : run))
      .slice(0, query.limit ?? 50);
    return { ...answer, runs: { ...answer.runs, data: runs } };
  };

  timeline = async (range?: Range): Promise<DriftTimelineResponse> => {
    const answer = this.#bundle.timeline;
    const data = answer.timeline.data;
    if (!range || !data || range.end.getTime() >= pointTime(data.end)) return answer;
    return { ...answer, timeline: { ...answer.timeline, data: timelineUntil(data, range.end.getTime()) } };
  };

  steps = async (run: RunId): Promise<RunStepsResponse> => {
    const found = this.#bundle.steps[`${run.workflow_id}@${run.started_at}`];
    if (found) return found;
    return {
      contract_version: "1",
      generated_at: this.#bundle.manifest.end,
      use_case: this.#bundle.manifest.use_case,
      steps: { source: "audit", available: true, stale: false, fetched_at: this.#bundle.manifest.end, data: null },
    };
  };

  metrics = async (view: MetricsViewName, range?: Range): Promise<MetricsResponse> => {
    const bundled = this.#bundle.metrics[view];
    const { manifest } = this.#bundle;
    const same = !range || (range.start.toISOString() === new Date(manifest.start).toISOString() && range.end.toISOString() === new Date(manifest.end).toISOString());
    if (bundled && same) return bundled;
    return {
      contract_version: "1",
      generated_at: manifest.end,
      use_case: manifest.use_case,
      metrics: {
        source: "prometheus",
        available: false,
        stale: false,
        reason: `a snapshot holds only its own range, ${manifest.start} to ${manifest.end}`,
        remedy: "uv run snapl dashboard",
        data: null,
      },
    };
  };
}

/** The bundle's provider in a snapshot file; the server's on a served page. */
export function providerFor(doc: Document): DataProvider {
  const bundle = readBundle(doc);
  return bundle ? new InlineProvider(bundle) : new HttpProvider();
}
