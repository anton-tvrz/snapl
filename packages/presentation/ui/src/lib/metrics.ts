// The metrics views of specs 012, 015 and 016, their groups, and how their values read (spec 011 FR-029, FR-031).
import type { MetricsData, MetricSeries, MetricsViewName } from "../api/types";
import type { Range } from "./lanes";
import { durationText } from "./time";

/** What a view's value is: each title says it (spec 015). */
// `count` is a gauge's count (spec 016's sessions established): no window, unlike `events`.
export type MetricKind = "percentile" | "mean" | "events" | "count" | "current" | "rate" | "up";

export type MetricGroupId = "timing" | "work" | "worker" | "dashboard" | "network";

/** The groups of spec 015 data-model.md "MetricGroup", in page order. */
export const METRIC_GROUPS: readonly { id: MetricGroupId; label: string }[] = [
  { id: "timing", label: "The loop's timing" },
  { id: "work", label: "What the loop did" },
  { id: "worker", label: "The worker" },
  { id: "dashboard", label: "The dashboard" },
  // Spec 016 data-model.md.
  { id: "network", label: "The network" },
];

const KIND_WORDS: Record<MetricKind, string | null> = {
  percentile: "95th percentile",
  mean: "mean",
  events: "count",
  count: "count",
  current: "current value",
  rate: "rate",
  // Up or down names itself.
  up: null,
};

// Every view the contract names, in its group's order. `satisfies` makes a view
// the contract adds fail the type check until it has a group.
const VIEWS = {
  "workflow-duration": { name: "Workflow duration", kind: "percentile", group: "timing" },
  "workflow-mean": { name: "Workflow duration", kind: "mean", group: "timing" },
  "gnmi-duration": { name: "gNMI call duration", kind: "percentile", group: "timing" },
  "gnmi-mean": { name: "gNMI call duration", kind: "mean", group: "timing" },
  "activity-time": { name: "Activity run time", kind: "mean", group: "timing" },
  "workflows-completed": { name: "Workflows completed", kind: "events", group: "work" },
  "scan-results": { name: "Scan results", kind: "events", group: "work" },
  "drift-paths": { name: "Differing paths found", kind: "events", group: "work" },
  "deploy-outcomes": { name: "Deploys ended", kind: "events", group: "work" },
  "gnmi-errors": { name: "gNMI errors", kind: "rate", group: "work" },
  "worker-up": { name: "Worker up", kind: "up", group: "worker" },
  "worker-lag": { name: "Wait before a worker picks up an activity", kind: "percentile", group: "worker" },
  "worker-slots": { name: "Worker slots in use", kind: "current", group: "worker" },
  "server-request-time": { name: "Temporal request time", kind: "mean", group: "worker" },
  "source-health": { name: "Dashboard sources up", kind: "up", group: "dashboard" },
  "source-fetch-time": { name: "Dashboard source read time", kind: "current", group: "dashboard" },
  "interface-traffic": { name: "Interface traffic", kind: "rate", group: "network" },
  "interface-errors": { name: "Interface errors", kind: "rate", group: "network" },
  "interface-discards": { name: "Interface discards", kind: "rate", group: "network" },
  "bgp-established": { name: "BGP sessions established", kind: "count", group: "network" },
  "device-cpu": { name: "Device CPU", kind: "current", group: "network" },
  "device-memory": { name: "Device memory", kind: "current", group: "network" },
} satisfies Record<MetricsViewName, { name: string; kind: MetricKind; group: MetricGroupId }>;

export interface MetricViewInfo {
  name: MetricsViewName;
  title: string;
  kind: MetricKind;
  group: MetricGroupId;
}

export const METRIC_VIEWS: readonly MetricViewInfo[] = (Object.keys(VIEWS) as MetricsViewName[]).map((name) => {
  const { name: words, kind, group } = VIEWS[name];
  const kindWords = KIND_WORDS[kind];
  return { name, title: kindWords ? `${words} (${kindWords})` : words, kind, group };
});

export interface MetricGroup {
  id: MetricGroupId | "other";
  label: string;
  views: { name: string; title: string; kind: MetricKind | null; group: MetricGroupId | "other" }[];
}

/**
 * The named views in their groups, leaving out empty ones. A view this list
 * does not know (a newer server's) goes to a last group, Other, by its raw name.
 */
export function groupViews(names: readonly string[]): MetricGroup[] {
  const known = new Map<string, MetricViewInfo>(METRIC_VIEWS.map((view) => [view.name, view]));
  const groups: MetricGroup[] = METRIC_GROUPS.map(({ id, label }) => ({
    id,
    label,
    views: METRIC_VIEWS.filter((view) => view.group === id && names.includes(view.name)),
  }));
  const other = names.filter((name) => !known.has(name));
  groups.push({
    id: "other",
    label: "Other",
    views: other.map((name) => ({ name, title: name, kind: null, group: "other" as const })),
  });
  return groups.filter((group) => group.views.length > 0);
}

export const UNIT_NAMES: Record<string, string> = {
  ms: "milliseconds",
  per_second: "per second",
  up: "up or down",
  count: "count",
  bytes_per_second: "bytes per second",
  percent: "percent",
};

// Spec 016 data-model.md: on the lab, every container reports the host VM's CPU and memory.
const LAB_NOTE = "On the lab, this is the host VM's, the same on every device";
const VIEW_NOTES: Partial<Record<string, string>> = { "device-cpu": LAB_NOTE, "device-memory": LAB_NOTE };

/** A note the page adds to a view, beside the server's own note. */
export function viewNote(name: string): string | null {
  return VIEW_NOTES[name] ?? null;
}

// Views where zero means nothing happened, so all zeros are said, not drawn (#312, 2026-11-20).
const NONE_TEXT: Partial<Record<string, string>> = {
  "interface-errors": "No errors on any interface in this range.",
  "interface-discards": "No discards on any interface in this range.",
};

/** "No errors on any interface in this range." when every series of such a view stayed at zero, else null. */
export function noneText(name: string, data: MetricsData): string | null {
  const text = NONE_TEXT[name];
  const series = data.series ?? [];
  if (!text || series.length === 0) return null;
  const values = series.flatMap((s) => s.points.map((point) => point.value)).filter((v): v is number => v !== null && v !== undefined);
  return values.length > 0 && values.every((value) => value === 0) ? text : null;
}

/** The highest value of a series over the range, skipping gaps, or null. */
export function peak(series: MetricSeries): number | null {
  let top: number | null = null;
  for (const point of series.points) {
    const value = point.value;
    if (value !== null && value !== undefined && (top === null || value > top)) top = value;
  }
  return top;
}

/** The chart's palette has six validated slots, used in order and never cycled. */
export const SLOTS = 6;

/**
 * The series a chart draws (#312, 2026-11-20): all of them, in the server's
 * order, up to six; past six, the six highest by their peak over the range,
 * highest first, with the server's order kept between equal peaks.
 */
export function chartSeries(series: readonly MetricSeries[]): { drawn: MetricSeries[]; total: number } {
  if (series.length <= SLOTS) return { drawn: [...series], total: series.length };
  const ranked = series
    .map((s, index) => ({ s, index, top: peak(s) }))
    .sort((a, b) => {
      if (a.top === b.top) return a.index - b.index;
      if (a.top === null) return 1;
      if (b.top === null) return -1;
      return b.top - a.top || a.index - b.index;
    });
  return { drawn: ranked.slice(0, SLOTS).map((entry) => entry.s), total: series.length };
}

/** A series' name from its labels: "leaf-01 configure", or "all" when it has none. */
export function seriesLabel(labels: Record<string, string>): string {
  const values = Object.values(labels);
  return values.length > 0 ? values.join(" ") : "all";
}

/** The last value that is not a gap, or null. */
export function latest(series: MetricSeries): number | null {
  for (let i = series.points.length - 1; i >= 0; i--) {
    const value = series.points[i]!.value;
    if (value !== null && value !== undefined) return value;
  }
  return null;
}

/** True when a per-second rate is shown per minute. */
function perMinute(value: number): boolean {
  return Math.abs(value) < 1;
}

export interface LeadValue {
  /** The latest value, formatted: "212 ms", "1.4/min", "up". */
  text: string;
  /** Which series it is, when the view has more than one and the value belongs to one. */
  series: string | null;
  /** "per minute" when a per-second rate was reworded. */
  unitNote: string | null;
  /** For a count of events, the window the point covers: "in the last 1 min". */
  window: string | null;
}

/** A window as the server's note words it under two minutes ("60 s"), and as a duration above. */
function windowText(seconds: number): string {
  return seconds < 120 ? `${seconds} s` : durationText(seconds);
}

/**
 * The seconds a count's point covers: its step (#308), or the window note of
 * an archive made before #308, whose points covered 60 s each (research R4).
 */
export function countWindow(data: MetricsData): number {
  const noted = /events in the last (\d+) s/.exec(data.note ?? "");
  return noted ? Number(noted[1]) : data.step;
}

/**
 * The one value a metrics panel leads with (spec 014 FR-023): the highest
 * latest value of a duration, a rate or a count, and for an up/down view the
 * first series that is down, or "up" when none is. A count of events names
 * the window its point covers; points are never added up, since they can
 * overlap (spec 015). Null when there is no data.
 */
export function leadValue(data: MetricsData, kind?: MetricKind | null): LeadValue | null {
  const known = (data.series ?? [])
    .map((series) => ({ label: seriesLabel(series.labels), value: latest(series) }))
    .filter((entry): entry is { label: string; value: number } => entry.value !== null);
  if (known.length === 0) return null;
  const many = (data.series ?? []).length > 1;
  if (data.unit === "up") {
    const down = known.find((entry) => entry.value < 1);
    return { text: down ? "down" : "up", series: down && many ? down.label : null, unitNote: null, window: null };
  }
  const top = known.reduce((a, b) => (b.value > a.value ? b : a));
  return {
    text: formatValue(top.value, data.unit),
    series: many ? top.label : null,
    unitNote: data.unit === "per_second" && perMinute(top.value) ? "per minute" : null,
    window: kind === "events" ? `in the last ${windowText(countWindow(data))}` : null,
  };
}

export function formatValue(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined) return "no data";
  if (unit === "up") return value >= 1 ? "up" : "down";
  if (unit === "ms") return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`;
  // A rate under one a second reads per minute: 0.024/s is 1.4/min (spec 014 FR-026).
  if (unit === "per_second") {
    return perMinute(value) ? `${Number((value * 60).toPrecision(2))}/min` : `${Number(value.toPrecision(3))}/s`;
  }
  // Prometheus extrapolates a count, so it can be fractional: shown whole (spec 015 research R4).
  if (unit === "count") return `${Math.round(value)}`;
  // Spec 016: bytes in thousands, as network tools show them, and a percent to one decimal under ten.
  if (unit === "bytes_per_second") return bytesText(value);
  if (unit === "percent") return `${Math.abs(value) < 10 ? Number(value.toFixed(1)) : Math.round(value)}%`;
  return `${value} ${unit}`;
}

const BYTE_STEPS: readonly [number, string][] = [
  [1e9, "GB/s"],
  [1e6, "MB/s"],
  [1e3, "kB/s"],
];

function bytesText(value: number): string {
  for (const [size, unit] of BYTE_STEPS) {
    if (Math.abs(value) >= size) return `${Number((value / size).toPrecision(2))} ${unit}`;
  }
  return `${Math.round(value)} B/s`;
}

export function upState(value: number | null | undefined): "up" | "down" | "gap" {
  if (value === null || value === undefined) return "gap";
  return value >= 1 ? "up" : "down";
}

export interface Preset {
  label: string;
  ms: number;
}

export const PRESETS: readonly Preset[] = [
  { label: "1 h", ms: 3_600_000 },
  { label: "24 h", ms: 86_400_000 },
  { label: "7 d", ms: 604_800_000 },
];

/** A preset range ending at `now`: the server's clock, never the browser's. */
export function presetRange(preset: Preset, now: Date): Range {
  return { start: new Date(now.getTime() - preset.ms), end: now };
}

/**
 * Whether an archive holds a range at 15 s for the network views: it lies
 * inside one of the windows around a fault (spec 016, #351).
 */
export function insideWindow(range: Range | undefined, windows: readonly Range[]): range is Range {
  return range !== undefined && windows.some((w) => w.start <= range.start && range.end <= w.end);
}
