import { describe, expect, it } from "vitest";

import { METRICS, NOW } from "../test/fixtures";
import { formatValue, latest, leadValue, METRIC_VIEWS, PRESETS, presetRange, seriesLabel, upState } from "./metrics";

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
    // A rate under one a second reads per minute (spec 014 FR-026).
    [0.024, "per_second", "1.4/min"],
    [0.05, "per_second", "3/min"],
    [0, "per_second", "0/min"],
    [1, "per_second", "1/s"],
    [2.5, "per_second", "2.5/s"],
    [3, "widgets", "3 widgets"],
    [1, "up", "up"],
    [0, "up", "down"],
    [null, "ms", "no data"],
  ] as const)("formats %s %s as %s", (value, unit, expected) => {
    expect(formatValue(value, unit)).toBe(expected);
  });

  describe("leadValue", () => {
    const data = (name: keyof typeof METRICS) => METRICS[name].metrics.data!;
    const series = (labels: Record<string, string>, value: number | null) => ({ labels, points: [{ t: "2026-11-07T21:20:00Z", value }] });

    it("leads a duration view with its highest latest value, and says which series it is", () => {
      expect(leadValue(data("workflow-duration"))).toEqual({ text: "2.10 s", series: "DeployIntent", unitNote: null });
    });

    it("names no series when the view has only one", () => {
      expect(leadValue(data("worker-lag"))).toEqual({ text: "26 ms", series: null, unitNote: null });
    });

    it("says a rare rate is per minute", () => {
      const rate = { ...data("gnmi-errors"), series: [series({ device: "leaf-01" }, 0.024), series({ device: "leaf-02" }, 0.01)] };

      expect(leadValue(rate)).toEqual({ text: "1.4/min", series: "leaf-01", unitNote: "per minute" });
    });

    it("leads an up/down view with the first series that is down", () => {
      const health = { ...data("source-health"), series: [series({ source: "audit" }, 1), series({ source: "ci" }, 0), series({ source: "sot" }, 0)] };

      expect(leadValue(health)).toEqual({ text: "down", series: "ci", unitNote: null });
    });

    it("says up when every series is up, and names the one when there is only one", () => {
      expect(leadValue(data("source-health"))).toEqual({ text: "up", series: null, unitNote: null });
      expect(leadValue(data("worker-up"))).toEqual({ text: "up", series: null, unitNote: null });
    });

    it("has no lead value when there is no data", () => {
      expect(leadValue({ ...data("worker-lag"), series: [] })).toBeNull();
      expect(leadValue({ ...data("worker-lag"), series: [series({}, null)] })).toBeNull();
    });

    it("shows a unit it does not know raw", () => {
      expect(leadValue({ ...data("worker-lag"), unit: "widgets" as never, series: [series({}, 3)] })?.text).toBe("3 widgets");
    });
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
