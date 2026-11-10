// The metrics views of spec 012 and how their values read (spec 011 FR-029, FR-031).
import type { MetricSeries, MetricsViewName } from "../api/types";
import type { Range } from "./lanes";

export const METRIC_VIEWS: readonly { name: MetricsViewName; title: string }[] = [
  { name: "workflow-duration", title: "Workflow duration (95th percentile)" },
  { name: "gnmi-duration", title: "gNMI call duration (95th percentile)" },
  { name: "gnmi-errors", title: "gNMI errors" },
  { name: "worker-lag", title: "Wait before a worker picks up an activity (95th percentile)" },
  { name: "worker-up", title: "Worker up" },
  { name: "source-health", title: "Dashboard sources up" },
];

export const UNIT_NAMES: Record<string, string> = {
  ms: "milliseconds",
  per_second: "per second",
  up: "up or down",
};

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

export function formatValue(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined) return "no data";
  if (unit === "up") return value >= 1 ? "up" : "down";
  if (unit === "ms") return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`;
  if (unit === "per_second") return `${Number(value.toPrecision(3))}/s`;
  return `${value} ${unit}`;
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
