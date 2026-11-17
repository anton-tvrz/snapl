import { describe, expect, it } from "vitest";

import { METRICS, NOW } from "../test/fixtures";
import {
  formatValue,
  groupViews,
  latest,
  leadValue,
  METRIC_GROUPS,
  METRIC_VIEWS,
  type MetricKind,
  PRESETS,
  presetRange,
  seriesLabel,
  upState,
} from "./metrics";

describe("metrics helpers", () => {
  // spec 015 data-model.md "MetricGroup".
  it("puts the views in four groups, in order", () => {
    expect(groupViews(Object.keys(METRICS)).map((group) => [group.label, group.views.map((v) => v.name)])).toEqual([
      ["The loop's timing", ["workflow-duration", "workflow-mean", "gnmi-duration", "gnmi-mean", "activity-time"]],
      ["What the loop did", ["workflows-completed", "scan-results", "drift-paths", "deploy-outcomes", "gnmi-errors"]],
      ["The worker", ["worker-up", "worker-lag", "worker-slots", "server-request-time"]],
      ["The dashboard", ["source-health", "source-fetch-time"]],
    ]);
  });

  it("has every view the server names in exactly one group", () => {
    const names = Object.keys(METRICS);
    const grouped = METRIC_GROUPS.flatMap((group) => METRIC_VIEWS.filter((v) => v.group === group.id).map((v) => v.name));

    expect([...grouped].sort()).toEqual([...names].sort());
    expect(new Set(grouped).size).toBe(grouped.length);
  });

  it("shows a view it does not know in a last group, Other, by its raw name", () => {
    const groups = groupViews(["worker-up", "scan-latency", "workflow-mean"]);

    expect(groups.map((group) => group.label)).toEqual(["The loop's timing", "The worker", "Other"]);
    expect(groups.at(-1)!.views).toEqual([{ name: "scan-latency", title: "scan-latency", kind: null, group: "other" }]);
  });

  it("says in each title whether a value is a percentile, a mean, a count, a rate or a current value", () => {
    const words: Record<MetricKind, string | null> = {
      percentile: "(95th percentile)",
      mean: "(mean)",
      events: "(count)",
      current: "(current value)",
      rate: "(rate)",
      up: null,
    };
    for (const view of METRIC_VIEWS) {
      const word = words[view.kind];
      if (word) expect(view.title.endsWith(` ${word}`), view.name).toBe(true);
      else expect(view.title, view.name).not.toMatch(/\(/);
    }
    expect(METRIC_VIEWS.find((v) => v.name === "workflow-duration")!.title).toBe("Workflow duration (95th percentile)");
    expect(METRIC_VIEWS.find((v) => v.name === "workflows-completed")!.title).toBe("Workflows completed (count)");
    expect(METRIC_VIEWS.find((v) => v.name === "worker-slots")!.title).toBe("Worker slots in use (current value)");
    // PR E: the worker's counters are counts of events.
    expect(METRIC_VIEWS.find((v) => v.name === "scan-results")!.title).toBe("Scan results (count)");
    expect(METRIC_VIEWS.find((v) => v.name === "drift-paths")!.title).toBe("Differing paths found (count)");
    expect(METRIC_VIEWS.find((v) => v.name === "deploy-outcomes")!.title).toBe("Deploys ended (count)");
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
    // A count is extrapolated by Prometheus, so it can be fractional: shown whole (spec 015 research R4).
    [1.8134, "count", "2"],
    [0.4, "count", "0"],
    [6, "count", "6"],
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
      expect(leadValue(data("workflow-duration"))).toEqual({ text: "2.10 s", series: "DeployIntent", unitNote: null, window: null });
    });

    it("names no series when the view has only one", () => {
      expect(leadValue(data("worker-lag"))).toEqual({ text: "26 ms", series: null, unitNote: null, window: null });
    });

    it("says a rare rate is per minute", () => {
      const rate = { ...data("gnmi-errors"), series: [series({ device: "leaf-01" }, 0.024), series({ device: "leaf-02" }, 0.01)] };

      expect(leadValue(rate)).toEqual({ text: "1.4/min", series: "leaf-01", unitNote: "per minute", window: null });
    });

    it("leads an up/down view with the first series that is down", () => {
      const health = { ...data("source-health"), series: [series({ source: "audit" }, 1), series({ source: "ci" }, 0), series({ source: "sot" }, 0)] };

      expect(leadValue(health)).toEqual({ text: "down", series: "ci", unitNote: null, window: null });
    });

    it("says up when every series is up, and names the one when there is only one", () => {
      expect(leadValue(data("source-health"))).toEqual({ text: "up", series: null, unitNote: null, window: null });
      expect(leadValue(data("worker-up"))).toEqual({ text: "up", series: null, unitNote: null, window: null });
    });

    it("has no lead value when there is no data", () => {
      expect(leadValue({ ...data("worker-lag"), series: [] })).toBeNull();
      expect(leadValue({ ...data("worker-lag"), series: [series({}, null)] })).toBeNull();
    });

    it("leads a count of events with the latest point and its window from the note, never a sum", () => {
      const completed = data("workflows-completed");
      expect(completed.note).toBe("events in the last 60 s");

      expect(leadValue(completed, "events")).toEqual({ text: "1", series: "ScanDrift", unitNote: null, window: "in the last 60 s" });
      const steady = { ...completed, series: [{ labels: {}, points: [3, 3, 3].map((value) => ({ t: "2026-11-07T21:20:00Z", value })) }] };
      expect(leadValue(steady, "events")?.text).toBe("3");
    });

    it("names the step as the window when the server gives no window note", () => {
      const day = { ...data("workflows-completed"), step: 360, note: null };

      expect(leadValue(day, "events")?.window).toBe("in the last 6 min");
    });

    it("leads a gauge count with its current value and no window", () => {
      expect(leadValue(data("worker-slots"), "current")).toEqual({ text: "6", series: "ActivityWorker", unitNote: null, window: null });
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
