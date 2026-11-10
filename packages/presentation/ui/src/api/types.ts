// Names for the contract's types. Everything here is derived from
// schema.d.ts, which is generated from the server's OpenAPI snapshot, so the
// UI cannot read a field the server does not send (spec 011 research R5).
import type { Open, SourceName } from "./open";
import type { components } from "./schema";

type Schemas = components["schemas"];

/** One source's answer, with its provenance and age (010 FR-005). */
export interface View<T> {
  source: SourceName;
  available: boolean;
  fetched_at?: string | null;
  stale?: boolean;
  reason?: string | null;
  remedy?: string | null;
  data?: T | null;
}

/** The server's response type with its open enumerations opened. */
type Opened<T, K extends keyof T, V> = Omit<T, K> & Record<K, V>;

export type ModeResponse = Opened<Schemas["ModeResponse"], "mode", Open<Schemas["ModeResponse"]["mode"]>>;
export type SourcesResponse = Opened<Schemas["SourcesResponse"], "sources", View<null>[]>;
export type Overview = Schemas["Overview"];
export type LinksResponse = Schemas["LinksResponse"];
export type TopologyResponse = Schemas["TopologyResponse"];
export type DevicesResponse = Schemas["DevicesResponse"];
export type RunsResponse = Schemas["RunsResponse"];
export type DriftTimelineResponse = Schemas["DriftTimelineResponse"];
export type RunStepsResponse = Schemas["RunStepsResponse"];
export type MetricsResponse = Schemas["MetricsResponse"];
export type MetricsViewName = Schemas["MetricsViewName"];

export type DeviceView = Schemas["DeviceView"];
export type RunView = Schemas["RunView"];
export type Topology = Schemas["Topology"];
export type TopologyNode = Schemas["TopologyNode"];
export type TopologyLink = Schemas["TopologyLink"];
export type DriftTimeline = Schemas["DriftTimeline"];
export type DeviceLane = Schemas["DeviceLane"];
export type ScanPoint = Schemas["ScanPoint"];
export type DriftEpisode = Schemas["DriftEpisode"];
export type Marker = Schemas["Marker"];
