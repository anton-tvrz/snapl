// 010's history view as a table, one row per bucket (the plain page's "Last
// 24 hours"). Per-scan series are folded into their bucket.
import type { HistoryResponse } from "../api/types";
import { ageText } from "./time";

type Series = HistoryResponse["series"][number];
type Marker = NonNullable<Series["markers"]>[number];
export type Bucket = "1h" | "1d" | "1w";

const BUCKET_MS: Record<string, number> = { "5m": 300_000, "1h": 3_600_000, "1d": 86_400_000, "1w": 604_800_000 };

export interface Counts {
  succeeded: number | null;
  failed: number | null;
}

export interface HistoryRow {
  start: string;
  /** No scan and no deploy in the bucket: shown as quiet, not as zeros. */
  quiet: boolean;
  scans: number;
  mostDrifted: number | null;
  mostUndesired: number | "unknown" | null;
  deploys: Counts;
  repairs: Counts;
}

/** A bucket that keeps a range to a readable number of rows. */
export function bucketFor(spanMs: number): Bucket {
  if (spanMs <= 2 * 86_400_000) return "1h";
  if (spanMs <= 60 * 86_400_000) return "1d";
  return "1w";
}

function counts(point: Series["points"][number] | undefined): Counts {
  return { succeeded: point?.by_outcome?.succeeded ?? null, failed: point?.by_outcome?.failed ?? null };
}

/** The table's rows, newest first. */
export function historyRows(history: HistoryResponse): HistoryRow[] {
  const byName = new Map(history.series.map((series) => [series.name, series]));
  const deploys = byName.get("deploys");
  if (!deploys) return [];
  const width = BUCKET_MS[deploys.bucket ?? "1h"] ?? BUCKET_MS["1h"]!;
  const scans = byName.get("drifted_devices")?.points ?? [];
  const undesired = byName.get("undesired_paths")?.points ?? [];
  const repairs = byName.get("repairs")?.points ?? [];

  const rows = deploys.points.map((bucket, index) => {
    const from = Date.parse(bucket.t);
    const inside = (point: { t: string }) => Date.parse(point.t) >= from && Date.parse(point.t) < from + width;
    const hourScans = scans.filter(inside);
    const hourUndesired = undesired.filter(inside).map((point) => point.value);
    const known = hourUndesired.filter((value): value is number => value !== null && value !== undefined);
    return {
      start: bucket.t,
      quiet: hourScans.length === 0 && bucket.by_outcome?.succeeded == null,
      scans: hourScans.length,
      mostDrifted: hourScans.length > 0 ? Math.max(...hourScans.map((point) => point.value ?? 0)) : null,
      mostUndesired: known.length > 0 ? Math.max(...known) : hourUndesired.length > 0 ? ("unknown" as const) : null,
      deploys: counts(bucket),
      repairs: counts(repairs[index]),
    };
  });
  return rows.reverse();
}

export function markerText(marker: Marker, now: Date): string {
  return `${ageText(marker.t, now)}: ${marker.kind} ${marker.workflow_id} (${marker.trigger})`;
}
