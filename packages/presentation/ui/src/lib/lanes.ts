// How a device's scans are drawn over time (spec 011 FR-021, research R10).
// The server sends scan points; the drawing rules live here.
import type { DeviceLane, DriftTimeline, ScanPoint } from "../api/types";

/** A scan's state is drawn for at most three default schedule intervals. */
export const HOLD_MS = 15 * 60_000;

/** A stretch of no scan, drawn as no data rather than the last known state. */
export const NO_DATA = "no_data";

export interface Segment {
  /** A drift state, or `no_data`. */
  state: string;
  start: Date;
  end: Date;
  /** The scan the stretch is drawn from. Unset for no data. */
  point?: ScanPoint;
}

export interface Range {
  start: Date;
  end: Date;
}

/**
 * A point's time in milliseconds, rounded up. The audit trail records
 * microseconds and a `Date` keeps milliseconds: rounding down would put a
 * scan just after an "as of" time the server counts it before.
 */
export function pointTime(iso: string): number {
  const base = Date.parse(iso);
  const fraction = /\.(\d+)/.exec(iso)?.[1] ?? "";
  return /[1-9]/.test(fraction.slice(3)) ? base + 1 : base;
}

/** The stretches a lane is drawn as over `range`, up to `now` at the latest. */
export function segments(points: readonly ScanPoint[], range: Range, now: Date): Segment[] {
  const start = range.start.getTime();
  const limit = Math.min(range.end.getTime(), now.getTime());
  const raw: Segment[] = [];
  const add = (state: string, from: number, to: number, point?: ScanPoint) => {
    const a = Math.max(from, start);
    const b = Math.min(to, limit);
    if (b > a) raw.push({ state, start: new Date(a), end: new Date(b), ...(point && { point }) });
  };

  const first = points[0];
  add(NO_DATA, start, first ? pointTime(first.t) : limit);
  points.forEach((point, index) => {
    const from = pointTime(point.t);
    const next = points[index + 1];
    const until = next ? pointTime(next.t) : limit;
    const held = Math.min(until, from + HOLD_MS);
    add(point.state, from, held, point);
    add(NO_DATA, held, until);
  });
  return raw;
}

/** A device's state at `t`: its last scan at or before then, while it is held. */
export function stateAt(points: readonly ScanPoint[], t: Date): { state: string; point?: ScanPoint } {
  const time = t.getTime();
  let last: ScanPoint | undefined;
  for (const point of points) {
    if (pointTime(point.t) > time) break;
    last = point;
  }
  if (!last) return { state: "never_scanned" };
  if (time - pointTime(last.t) > HOLD_MS) return { state: NO_DATA };
  return { state: last.state, point: last };
}

/** One "as of" stop per scan: the time its last device was recorded, rounded up. */
export function scanStops(lanes: readonly DeviceLane[]): Date[] {
  const latest = new Map<string, number>();
  for (const lane of lanes) {
    for (const point of lane.points) {
      const time = pointTime(point.t);
      latest.set(point.scan_workflow_id, Math.max(latest.get(point.scan_workflow_id) ?? 0, time));
    }
  }
  return [...latest.values()].sort((a, b) => a - b).map((time) => new Date(time));
}

/**
 * The stretch of time drawn, and where history starts. When the first scan or
 * repair run is inside the range, the drawing starts just before it rather
 * than spending most of its width on nothing.
 */
export function domainOf(timeline: DriftTimeline): Range & { history?: Date } {
  const start = new Date(timeline.start);
  const end = new Date(timeline.end);
  const history = timeline.first_event_at ? { history: new Date(timeline.first_event_at) } : {};
  const firsts = [
    ...timeline.lanes.flatMap((lane) => (lane.points[0] ? [pointTime(lane.points[0].t)] : [])),
    ...timeline.markers.map((marker) => pointTime(marker.t)),
  ];
  if (firsts.length === 0) return { start, end, ...history };
  const first = Math.min(...firsts);
  const pad = (end.getTime() - first) * 0.03;
  return { start: new Date(Math.max(start.getTime(), first - pad)), end, ...history };
}

/** The next stop after `value`, or undefined (now) after the last. */
export function nextStop(stops: readonly Date[], value: Date | undefined): Date | undefined {
  if (!value) return undefined;
  return stops.find((stop) => stop > value);
}

/** The stop before `value` (now when undefined), or the first stop. */
export function previousStop(stops: readonly Date[], value: Date | undefined): Date | undefined {
  const before = value ? stops.filter((stop) => stop < value) : stops;
  return before.at(-1) ?? stops[0];
}
