import { describe, expect, it } from "vitest";

import type { HistoryResponse, Point, Series } from "../api/types";
import { FABRIC_HISTORY } from "../test/fixtures";
import { FABRIC_SERIES, latestRows, parseRunKey, runBars, runRows, runTriggers, scanPoints, seriesNamed, timeTicks } from "./fabricHistory";

const START = "2026-11-07T00:00:00Z";
const END = "2026-11-08T00:00:00Z";

function series(name: Series["name"], points: Point[], bucket: Series["bucket"] = null): Series {
  return { name, start: START, end: END, bucket, points, markers: [] };
}

describe("FABRIC_SERIES", () => {
  it("names the four series of spec 015 that the panel asks for", () => {
    expect(FABRIC_SERIES).toEqual(["in_sync_devices", "open_drifts", "runs", "repair_duration"]);
  });
});

describe("seriesNamed", () => {
  it("finds a series by name, and nothing when the answer does not hold it", () => {
    expect(seriesNamed(FABRIC_HISTORY, "runs")?.bucket).toBe("1h");
    expect(seriesNamed({ ...FABRIC_HISTORY, series: [] } as HistoryResponse, "runs")).toBeUndefined();
    expect(seriesNamed(undefined, "runs")).toBeUndefined();
  });
});

describe("scanPoints", () => {
  it("gives each point's time in milliseconds and its value, oldest first", () => {
    const points = scanPoints(
      series("in_sync_devices", [
        { t: "2026-11-07T10:00:00Z", value: 5 },
        { t: "2026-11-07T09:00:00Z", value: 4 },
      ]),
    );

    expect(points).toEqual([
      { t: Date.parse("2026-11-07T09:00:00Z"), value: 4 },
      { t: Date.parse("2026-11-07T10:00:00Z"), value: 5 },
    ]);
  });

  it("drops a point with no value rather than drawing it as zero", () => {
    expect(scanPoints(series("open_drifts", [{ t: "2026-11-07T09:00:00Z", value: null }]))).toEqual([]);
    expect(scanPoints(undefined)).toEqual([]);
  });
});

describe("parseRunKey", () => {
  it("splits a key on its last dot into a trigger and an outcome", () => {
    expect(parseRunKey("schedule.succeeded")).toEqual({ trigger: "schedule", outcome: "succeeded" });
    expect(parseRunKey("operator.running")).toEqual({ trigger: "operator", outcome: "running" });
  });

  it("folds a trigger it does not know into other, and keeps an outcome it does not know as failed", () => {
    expect(parseRunKey("unknown.failed")).toEqual({ trigger: "other", outcome: "failed" });
    expect(parseRunKey("cron.v2.succeeded")).toEqual({ trigger: "other", outcome: "succeeded" });
    expect(parseRunKey("schedule.cancelled")).toEqual({ trigger: "schedule", outcome: "failed" });
  });
});

describe("runBars", () => {
  it("gives each bucket's runs by trigger and outcome, oldest first", () => {
    const bars = runBars(
      series(
        "runs",
        [
          { t: "2026-11-07T01:00:00Z", value: 3, by_outcome: { "schedule.succeeded": 2, "operator.failed": 1 } },
          { t: "2026-11-07T00:00:00Z", value: 1, by_outcome: { "schedule.running": 1 } },
        ],
        "1h",
      ),
    );

    expect(bars).toEqual([
      { t: Date.parse("2026-11-07T00:00:00Z"), quiet: false, total: 1, "schedule.running": 1 },
      { t: Date.parse("2026-11-07T01:00:00Z"), quiet: false, total: 3, "schedule.succeeded": 2, "operator.failed": 1 },
    ]);
  });

  it("leaves a bucket where no run started empty and quiet, never zero", () => {
    const [bar] = runBars(series("runs", [{ t: "2026-11-07T00:00:00Z", value: null, by_outcome: {} }], "1h"));

    expect(bar).toEqual({ t: Date.parse("2026-11-07T00:00:00Z"), quiet: true, total: null });
  });

  it("adds the counts of unknown triggers together under other", () => {
    const [bar] = runBars(
      series("runs", [{ t: "2026-11-07T00:00:00Z", value: 3, by_outcome: { "unknown.failed": 1, "cron.failed": 2 } }], "1h"),
    );

    expect(bar).toMatchObject({ "other.failed": 3, total: 3 });
  });
});

describe("runTriggers", () => {
  it("lists the triggers present in a fixed order, so a colour follows its trigger", () => {
    const bars = runBars(
      series("runs", [{ t: "2026-11-07T00:00:00Z", value: 2, by_outcome: { "unknown.failed": 1, "operator.succeeded": 1 } }], "1h"),
    );

    expect(runTriggers(bars)).toEqual(["operator", "other"]);
  });
});

describe("runRows", () => {
  it("says each bucket's runs in words, newest first, and 'no runs' for a quiet one", () => {
    const rows = runRows(
      series(
        "runs",
        [
          { t: "2026-11-07T00:00:00Z", value: null, by_outcome: {} },
          { t: "2026-11-07T01:00:00Z", value: 4, by_outcome: { "schedule.succeeded": 2, "schedule.failed": 1, "operator.running": 1 } },
        ],
        "1h",
      ),
    );

    expect(rows).toEqual([
      { t: Date.parse("2026-11-07T01:00:00Z"), total: 4, text: "Scheduled: 2 succeeded, 1 failed. Operator: 1 running." },
      { t: Date.parse("2026-11-07T00:00:00Z"), total: null, text: "no runs" },
    ]);
  });
});

describe("latestRows", () => {
  it("gives the newest rows first, at most the limit, with how many were left out", () => {
    const points = [1, 2, 3, 4, 5].map((t) => ({ t, value: t }));

    expect(latestRows(points, 3)).toEqual({ rows: [points[4], points[3], points[2]], omitted: 2 });
    expect(latestRows(points, 10)).toEqual({ rows: [...points].reverse(), omitted: 0 });
  });
});

describe("timeTicks", () => {
  const at = (iso: string) => Date.parse(iso);

  it("puts ticks on whole hours, three hours apart, over a day", () => {
    const ticks = timeTicks(at("2026-11-06T21:20:48Z"), at("2026-11-07T21:20:48Z"));

    expect(ticks.map((t) => new Date(t).toISOString().slice(11, 16))).toEqual([
      "00:00", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00", "21:00",
    ]);
  });

  it("puts ticks on whole days over a week, and no more than eight", () => {
    const ticks = timeTicks(at("2026-11-01T05:00:00Z"), at("2026-11-08T05:00:00Z"));

    expect(ticks.map((t) => new Date(t).toISOString().slice(0, 10))).toEqual([
      "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07", "2026-11-08",
    ]);
    expect(timeTicks(at("2026-10-08T00:00:00Z"), at("2026-11-08T00:00:00Z")).length).toBeLessThanOrEqual(8);
  });

  it("puts ticks on whole minutes over a short range", () => {
    const ticks = timeTicks(at("2026-11-07T21:00:00Z"), at("2026-11-07T22:00:00Z"));

    expect(ticks.map((t) => new Date(t).toISOString().slice(11, 16))).toEqual([
      "21:00", "21:10", "21:20", "21:30", "21:40", "21:50", "22:00",
    ]);
  });
});
