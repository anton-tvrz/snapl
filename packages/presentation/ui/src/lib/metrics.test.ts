import { describe, expect, it } from "vitest";

import { METRICS, NOW } from "../test/fixtures";
import { formatValue, latest, METRIC_VIEWS, PRESETS, presetRange, seriesLabel, upState } from "./metrics";

describe("metrics helpers", () => {
  it("lists the six views of spec 012 in order, with titles", () => {
    expect(METRIC_VIEWS.map((v) => v.name)).toEqual([
      "workflow-duration",
      "gnmi-duration",
      "gnmi-errors",
      "worker-lag",
      "worker-up",
      "source-health",
    ]);
    expect(METRIC_VIEWS[0]!.title).toBe("Workflow duration (95th percentile)");
  });

  it("names a series by its labels, and an unlabelled one as all", () => {
    expect(seriesLabel({ device: "leaf-01", operation: "configure" })).toBe("leaf-01 configure");
    expect(seriesLabel({})).toBe("all");
  });

  it("finds a series' latest value, skipping gaps", () => {
    const series = METRICS["workflow-duration"].metrics.data!.series![1]!;
    const gapAtEnd = { ...series, points: [...series.points.slice(0, 5), { t: "2026-11-07T21:20:00Z", value: null }] };

    expect(latest(gapAtEnd)).toBe(series.points[4]!.value);
    expect(latest({ labels: {}, points: [] })).toBeNull();
  });

  it.each([
    [1234.5, "ms", "1.23 s"],
    [87.25, "ms", "87 ms"],
    [0.05, "per_second", "0.05/s"],
    [0, "per_second", "0/s"],
    [1, "up", "up"],
    [0, "up", "down"],
    [null, "ms", "no data"],
  ] as const)("formats %s %s as %s", (value, unit, expected) => {
    expect(formatValue(value, unit)).toBe(expected);
  });

  it("reads an up/down value as up, down or no data", () => {
    expect(upState(1)).toBe("up");
    expect(upState(0)).toBe("down");
    expect(upState(null)).toBe("gap");
  });

  it("makes each preset range end at the server's now", () => {
    expect(PRESETS.map((p) => p.label)).toEqual(["1 h", "24 h", "7 d"]);
    const day = presetRange(PRESETS[1]!, NOW);
    expect(day.end).toEqual(NOW);
    expect(NOW.getTime() - day.start.getTime()).toBe(86_400_000);
  });
});
