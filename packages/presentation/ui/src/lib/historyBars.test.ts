import { describe, expect, it } from "vitest";

import type { HistoryResponse } from "../api/types";
import { HISTORY } from "../test/fixtures";
import { historyBars } from "./historyBars";

describe("historyBars", () => {
  it("gives one bar per bucket, oldest first", () => {
    const bars = historyBars(HISTORY);

    expect(bars).toHaveLength(25);
    expect(bars.map((bar) => bar.t)).toEqual([...bars.map((bar) => bar.t)].sort((a, b) => a - b));
    expect(new Date(bars.at(-1)!.t).toISOString()).toBe("2026-11-07T21:00:00.000Z");
  });

  it("carries the scans, the most drifted devices, and the counts by outcome", () => {
    expect(historyBars(HISTORY).at(-1)).toEqual({
      t: Date.parse("2026-11-07T21:00:00Z"),
      quiet: false,
      scans: 3,
      mostDrifted: 2,
      deploysOk: 8,
      deploysFailed: 0,
      repairsOk: 0,
      repairsFailed: 0,
    });
  });

  it("draws nothing for a quiet bucket: its counts are null, not zero", () => {
    expect(historyBars(HISTORY)[0]).toEqual({
      t: Date.parse("2026-11-06T21:00:00Z"),
      quiet: true,
      scans: null,
      mostDrifted: null,
      deploysOk: null,
      deploysFailed: null,
      repairsOk: null,
      repairsFailed: null,
    });
  });

  it("keeps a bucket with deploys and no scan: its scans are null, its deploys counted", () => {
    const history = {
      ...HISTORY,
      series: HISTORY.series.map((series) =>
        series.name === "drifted_devices" || series.name === "undesired_paths" ? { ...series, points: [] } : series,
      ),
    } as HistoryResponse;

    expect(historyBars(history).at(-1)).toMatchObject({ quiet: false, scans: null, mostDrifted: null, deploysOk: 8 });
  });

  it("gives no bars for an empty history", () => {
    expect(historyBars({ ...HISTORY, series: [] })).toEqual([]);
  });
});
