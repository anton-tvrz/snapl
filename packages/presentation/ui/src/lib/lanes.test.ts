import { describe, expect, it } from "vitest";

import type { ScanPoint } from "../api/types";
import { DEVICES_AT_SCAN_1, DEVICES_AT_SCAN_2, DEVICES_AT_SCAN_3, SCAN_STOPS, TIMELINE } from "../test/fixtures";
import { domainOf, HOLD_MS, nextStop, pointTime, previousStop, scanStops, segments, stateAt } from "./lanes";

const T0 = new Date("2026-11-07T12:00:00Z").getTime();
const at = (minutes: number) => new Date(T0 + minutes * 60_000);

function point(minutes: number, state: string, scan = `scan-${minutes}`): ScanPoint {
  return {
    t: at(minutes).toISOString(),
    state: state as ScanPoint["state"],
    scan_workflow_id: scan,
    missing_paths: [],
    undesired_paths: [],
    unclassified_paths: [],
    error: state === "error" ? "gNMI dial timed out" : null,
  };
}

const drawn = (list: ReturnType<typeof segments>) =>
  list.map((s) => [s.state, (s.start.getTime() - T0) / 60_000, (s.end.getTime() - T0) / 60_000]);

const timeline = TIMELINE.timeline.data!;
const lane = (name: string) => timeline.lanes.find((l) => l.name === name)!;

describe("segments", () => {
  const range = { start: at(0), end: at(60) };

  it("draws each state from its scan to the next scan", () => {
    const points = [point(10, "in_sync"), point(15, "missing"), point(20, "in_sync")];

    expect(drawn(segments(points, range, at(25)))).toEqual([
      ["no_data", 0, 10],
      ["in_sync", 10, 15],
      ["missing", 15, 20],
      ["in_sync", 20, 25],
    ]);
  });

  it("draws 15 minutes of a state and then no data across a longer gap", () => {
    const points = [point(0, "in_sync"), point(40, "undesired")];

    expect(drawn(segments(points, range, at(45)))).toEqual([
      ["in_sync", 0, 15],
      ["no_data", 15, 40],
      ["undesired", 40, 45],
    ]);
  });

  it("holds the last point to now for at most 15 minutes", () => {
    expect(drawn(segments([point(10, "in_sync")], range, at(20)))).toEqual([
      ["no_data", 0, 10],
      ["in_sync", 10, 20],
    ]);
    expect(drawn(segments([point(10, "in_sync")], range, at(60)))).toEqual([
      ["no_data", 0, 10],
      ["in_sync", 10, 25],
      ["no_data", 25, 60],
    ]);
  });

  it("stops at the range's end when that is before now", () => {
    expect(drawn(segments([point(50, "missing")], range, at(90)))).toEqual([
      ["no_data", 0, 50],
      ["missing", 50, 60],
    ]);
  });

  it("draws an error scan as an error stretch", () => {
    const points = [point(0, "missing"), point(5, "error"), point(10, "missing")];

    expect(drawn(segments(points, range, at(12))).map(([state]) => state)).toEqual(["missing", "error", "missing"]);
    expect(segments(points, range, at(12))[1]?.point?.error).toBe("gNMI dial timed out");
  });

  it("is one stretch of no data for a lane with no scans", () => {
    expect(drawn(segments([], range, at(30)))).toEqual([["no_data", 0, 30]]);
  });

  it("keeps the hold constant at three default schedule intervals", () => {
    expect(HOLD_MS).toBe(15 * 60_000);
  });
});

describe("stateAt", () => {
  const points = [point(10, "missing"), point(20, "in_sync")];

  it("is the last point at or before the time", () => {
    expect(stateAt(points, at(10)).state).toBe("missing");
    expect(stateAt(points, at(19)).state).toBe("missing");
    expect(stateAt(points, at(20)).state).toBe("in_sync");
    expect(stateAt(points, at(19)).point?.t).toBe(at(10).toISOString());
  });

  it("is never scanned before the first point", () => {
    expect(stateAt(points, at(9))).toEqual({ state: "never_scanned" });
  });

  it("is no data when the last point is more than 15 minutes old", () => {
    expect(stateAt(points, at(35)).state).toBe("in_sync");
    expect(stateAt(points, at(36))).toEqual({ state: "no_data" });
  });
});

describe("pointTime", () => {
  it("rounds a time recorded in microseconds up to the next millisecond", () => {
    expect(pointTime("2026-11-07T21:20:13.665165Z")).toBe(Date.parse("2026-11-07T21:20:13.666Z"));
    expect(pointTime("2026-11-07T21:20:13.665000Z")).toBe(Date.parse("2026-11-07T21:20:13.665Z"));
    expect(pointTime("2026-11-07T21:20:13Z")).toBe(Date.parse("2026-11-07T21:20:13Z"));
  });
});

describe("the demo arc", () => {
  it("has one stop per scan, just after the scan's last device", () => {
    expect(scanStops(timeline.lanes).map((stop) => stop.toISOString())).toEqual(SCAN_STOPS);
  });

  // SC-004: what the lanes say at each stop is what the server says the
  // devices were at that time.
  it.each([
    [0, DEVICES_AT_SCAN_1],
    [1, DEVICES_AT_SCAN_2],
    [2, DEVICES_AT_SCAN_3],
  ])("matches the server's device view at stop %i", (index, response) => {
    const stop = new Date(SCAN_STOPS[index]!);
    const server = Object.fromEntries(response.devices.data!.map((d) => [d.name, d.drift_state]));
    const lanes = Object.fromEntries(timeline.lanes.map((l) => [l.name, stateAt(l.points, stop).state]));

    expect(lanes).toEqual(server);
  });

  it("does not count a scan a millisecond before its rounded time", () => {
    const early = new Date(Date.parse(SCAN_STOPS[0]!) - 1);

    expect(stateAt(lane("spine-01").points, early).state).toBe("never_scanned");
  });

  it("starts the drawing just before the first scan when that is inside the range", () => {
    const domain = domainOf(timeline);
    const firstScan = Math.min(...timeline.lanes.map((l) => pointTime(l.points[0]!.t)));

    expect(domain.end).toEqual(new Date(timeline.end));
    expect(domain.start.getTime()).toBeLessThan(firstScan);
    expect(domain.start.getTime()).toBeGreaterThan(Date.parse(timeline.first_event_at!));
    expect(domain.history).toEqual(new Date(timeline.first_event_at!));
  });

  it("starts the drawing before a repair run that came before any scan", () => {
    const early = { ...timeline, markers: [{ ...timeline.markers[0]!, t: "2026-11-07T21:19:30Z" }] };

    expect(domainOf(early).start.getTime()).toBeLessThan(Date.parse("2026-11-07T21:19:30Z"));
  });

  it("draws the whole range when the first scan is before it", () => {
    const domain = domainOf({ ...timeline, start: "2026-11-07T21:20:14Z", first_event_at: "2026-01-01T00:00:00Z" });

    expect(domain.start).toEqual(new Date("2026-11-07T21:20:14Z"));
  });

  it("draws the whole range, with no history, for an empty trail", () => {
    const domain = domainOf({ ...timeline, first_event_at: null, lanes: [], markers: [] });

    expect(domain.start).toEqual(new Date(timeline.start));
    expect(domain.history).toBeUndefined();
  });
});

describe("stepping", () => {
  const stops = SCAN_STOPS.map((s) => new Date(s));

  it("steps forward one scan, and past the last to now", () => {
    expect(nextStop(stops, stops[0])).toEqual(stops[1]);
    expect(nextStop(stops, new Date(Date.parse(SCAN_STOPS[0]!) + 500))).toEqual(stops[1]);
    expect(nextStop(stops, stops[2])).toBeUndefined();
  });

  it("steps back one scan, and from now to the last scan", () => {
    expect(previousStop(stops, undefined)).toEqual(stops[2]);
    expect(previousStop(stops, stops[2])).toEqual(stops[1]);
    expect(previousStop(stops, stops[0])).toEqual(stops[0]);
  });
});
