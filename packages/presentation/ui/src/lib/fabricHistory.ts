// The fabric history's four series (spec 015 FR-005), shaped for the charts
// and their tables. A point with no value and a bucket where no run started
// are kept empty, never drawn as zero (FR-008).
import type { HistoryResponse, SeriesName, Series } from "../api/types";

/** The series the panel asks `/history` for, by name. */
export const FABRIC_SERIES: SeriesName[] = ["in_sync_devices", "open_drifts", "runs", "repair_duration"];

export interface ScanPoint {
  /** Milliseconds. */
  t: number;
  value: number;
}

export function seriesNamed(history: HistoryResponse | undefined, name: SeriesName): Series | undefined {
  return history?.series.find((series) => series.name === name);
}

/** A per-scan series (or the repairs), oldest first, without empty points. */
export function scanPoints(series: Series | undefined): ScanPoint[] {
  const points: ScanPoint[] = [];
  for (const point of series?.points ?? []) {
    if (point.value !== null && point.value !== undefined) points.push({ t: Date.parse(point.t), value: point.value });
  }
  return points.sort((a, b) => a.t - b.t);
}

// The triggers the panel names, in the fixed order their colours follow.
export const RUN_TRIGGERS = ["schedule", "operator", "other"] as const;
export type RunTrigger = (typeof RUN_TRIGGERS)[number];
export const RUN_OUTCOMES = ["succeeded", "failed", "running"] as const;
export type RunOutcome = (typeof RUN_OUTCOMES)[number];

export const TRIGGER_WORDS: Record<RunTrigger, string> = { schedule: "Scheduled", operator: "Operator", other: "Other" };

/**
 * A `runs` key, `<trigger>.<outcome>`, split on its last dot. `unknown` and
 * any trigger the page does not name are "other"; an outcome it does not know
 * did not succeed, so it counts as failed, as the server counts a cancellation.
 */
export function parseRunKey(key: string): { trigger: RunTrigger; outcome: RunOutcome } {
  const dot = key.lastIndexOf(".");
  const rawTrigger = dot < 0 ? "" : key.slice(0, dot);
  const rawOutcome = key.slice(dot + 1);
  const trigger: RunTrigger = rawTrigger === "schedule" || rawTrigger === "operator" ? rawTrigger : "other";
  const outcome: RunOutcome = rawOutcome === "succeeded" || rawOutcome === "running" ? rawOutcome : "failed";
  return { trigger, outcome };
}

export type RunKey = `${RunTrigger}.${RunOutcome}`;

export type RunBar = { t: number; quiet: boolean; total: number | null } & Partial<Record<RunKey, number>>;

function counts(byOutcome: Record<string, number | null> | null | undefined): Map<RunKey, number> {
  const out = new Map<RunKey, number>();
  for (const [key, count] of Object.entries(byOutcome ?? {})) {
    if (!count) continue;
    const { trigger, outcome } = parseRunKey(key);
    const ours: RunKey = `${trigger}.${outcome}`;
    out.set(ours, (out.get(ours) ?? 0) + count);
  }
  return out;
}

/** The runs per bucket, oldest first. A bucket where no run started is quiet and holds no count. */
export function runBars(series: Series | undefined): RunBar[] {
  return [...(series?.points ?? [])]
    .map((point) => {
      const bar: RunBar = { t: Date.parse(point.t), quiet: point.value === null || point.value === undefined, total: point.value ?? null };
      for (const [key, count] of counts(point.by_outcome)) bar[key] = count;
      return bar;
    })
    .sort((a, b) => a.t - b.t);
}

/** The triggers with at least one run, in the fixed order. */
export function runTriggers(bars: RunBar[]): RunTrigger[] {
  return RUN_TRIGGERS.filter((trigger) => bars.some((bar) => RUN_OUTCOMES.some((outcome) => bar[`${trigger}.${outcome}`])));
}

export interface RunRow {
  t: number;
  total: number | null;
  /** The bucket in words: "Scheduled: 2 succeeded, 1 failed. Operator: 1 running." */
  text: string;
}

/** The table's rows, newest first. */
export function runRows(series: Series | undefined): RunRow[] {
  return runBars(series)
    .reverse()
    .map((bar) => {
      if (bar.quiet) return { t: bar.t, total: null, text: "no runs" };
      const parts = RUN_TRIGGERS.flatMap((trigger) => {
        const said = RUN_OUTCOMES.flatMap((outcome) => {
          const count = bar[`${trigger}.${outcome}`];
          return count ? [`${count} ${outcome}`] : [];
        });
        return said.length > 0 ? [`${TRIGGER_WORDS[trigger]}: ${said.join(", ")}.`] : [];
      });
      return { t: bar.t, total: bar.total, text: parts.join(" ") };
    });
}

/** The newest `limit` rows, newest first, and how many older ones were left out. */
export function latestRows<T>(points: T[], limit: number): { rows: T[]; omitted: number } {
  const rows = points.slice(-limit).reverse();
  return { rows, omitted: points.length - rows.length };
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
// Tick steps, smallest first. Each lands on whole minutes, hours or days in UTC.
const TICK_STEPS = [MINUTE, 5 * MINUTE, 10 * MINUTE, 15 * MINUTE, 30 * MINUTE, HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, DAY, 2 * DAY, 7 * DAY];
const MOST_TICKS = 8;

/** Ticks for a time axis: on whole UTC minutes, hours or days, the finest step that gives at most eight. */
export function timeTicks(start: number, end: number): number[] {
  const step = TICK_STEPS.find((size) => Math.floor(end / size) - Math.ceil(start / size) + 1 <= MOST_TICKS) ?? TICK_STEPS.at(-1)!;
  const ticks: number[] = [];
  for (let t = Math.ceil(start / step) * step; t <= end; t += step) ticks.push(t);
  return ticks;
}
