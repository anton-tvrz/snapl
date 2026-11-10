// Where the UI's data comes from. The live dashboard and an opened archive
// are both the HTTP API (archive mode is a server mode); a snapshot file reads
// the bundle embedded in it (spec 011 research R13, R14).
import type {
  DevicesResponse,
  DriftTimelineResponse,
  LinksResponse,
  MetricsResponse,
  MetricsViewName,
  ModeResponse,
  Overview,
  RunStepsResponse,
  RunsResponse,
  SourcesResponse,
  TopologyResponse,
} from "../api/types";

export interface Range {
  start: Date;
  end: Date;
}

/** One run: a workflow id can hold many, so its start time is part of its identity. */
export interface RunId {
  workflow_id: string;
  started_at: string;
}

export interface RunsQuery {
  at?: Date;
  device?: string;
  limit?: number;
}

export interface DataProvider {
  mode(): Promise<ModeResponse>;
  overview(): Promise<Overview>;
  sources(): Promise<SourcesResponse>;
  links(): Promise<LinksResponse>;
  topology(): Promise<TopologyResponse>;
  devices(at?: Date): Promise<DevicesResponse>;
  runs(query: RunsQuery): Promise<RunsResponse>;
  /** Without a range, the server's default: the last 24 hours by its own clock. */
  timeline(range?: Range): Promise<DriftTimelineResponse>;
  steps(run: RunId): Promise<RunStepsResponse>;
  /** Without a range, the server's default: the last hour by its own clock. */
  metrics(view: MetricsViewName, range?: Range): Promise<MetricsResponse>;
}
