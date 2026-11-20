// Where the UI's data comes from. The live dashboard and an opened archive
// are both the HTTP API (archive mode is a server mode); a snapshot file reads
// the bundle embedded in it (spec 011 research R13, R14).
import type {
  DevicesResponse,
  DriftSummaryResponse,
  DriftTimelineResponse,
  HealthResponse,
  HistoryResponse,
  LinksResponse,
  MetricsResponse,
  MetricsViewName,
  ModeResponse,
  Overview,
  RunStepsResponse,
  RunsResponse,
  SeriesName,
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
  /**
   * History series. Without a range, the server's default: the last 24 hours,
   * hourly. Without names, 010's four; spec 015's are answered only when named.
   */
  history(range?: Range, bucket?: string, series?: SeriesName[]): Promise<HistoryResponse>;
  /** Each device's drift over a range (spec 015). Without one, the server's last 24 hours. */
  driftSummary(range?: Range): Promise<DriftSummaryResponse>;
  /** Without a range, the server's default: the last 24 hours by its own clock. */
  timeline(range?: Range): Promise<DriftTimelineResponse>;
  /** Each device's health now and its unhealthy stretches over a range (spec 016). Without one, the last 24 hours. */
  health(range?: Range): Promise<HealthResponse>;
  steps(run: RunId): Promise<RunStepsResponse>;
  /** Without a range, the server's default: the last hour by its own clock. */
  metrics(view: MetricsViewName, range?: Range): Promise<MetricsResponse>;
}
