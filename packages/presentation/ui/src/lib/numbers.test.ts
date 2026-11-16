import { describe, expect, it } from "vitest";

import type { DeviceView, DriftEpisode, DriftTimeline, HistoryResponse, View } from "../api/types";
import { HISTORY, NOW, OVERVIEW, TIMELINE } from "../test/fixtures";
import { type NumberCard, type NumberCardId, numberCards } from "./numbers";

const DAY = 86_400_000;
const devicesView = OVERVIEW.devices as View<DeviceView[]>;
const timelineView = TIMELINE.timeline as View<DriftTimeline>;
const timeline = timelineView.data!;

function cards(over: Partial<Parameters<typeof numberCards>[0]> = {}) {
  const all = numberCards({ devices: devicesView, timeline: timelineView, history: HISTORY, now: NOW, past: false, ...over });
  return Object.fromEntries(all.map((card) => [card.id, card])) as Record<NumberCardId, NumberCard>;
}

function device(name: string, state: string, inIntent = true): DeviceView {
  return { device_id: `00000000-0000-0000-0000-${name.padStart(12, "0")}`, name, role: "leaf", in_intent: inIntent, drift_state: state } as DeviceView;
}

function withTimeline(change: Partial<DriftTimeline>): View<DriftTimeline> {
  return { ...timelineView, data: { ...timeline, ...change } };
}

describe("numberCards", () => {
  it("gives four cards in order, each with a label and a panel to link to", () => {
    const all = numberCards({ devices: devicesView, timeline: timelineView, history: HISTORY, now: NOW, past: false });

    expect(all.map((card) => card.id)).toEqual(["in_sync", "open_drifts", "latest_repair", "deploys_repairs"]);
    expect(all.map((card) => card.label)).toEqual(["Devices in sync", "Open drifts", "Latest repair", "Deploys, 24 h"]);
    expect(all.map((card) => card.target)).toEqual(["fabric", "drift-over-time", "drift-over-time", "history"]);
  });

  describe("devices in sync", () => {
    it("counts devices in intent as x of y", () => {
      expect(cards().in_sync).toMatchObject({ value: "5 of 6" });
    });

    it("leaves out devices no longer in intent", () => {
      const data = [device("1", "in_sync"), device("2", "undesired"), device("3", "in_sync", false), device("4", "error", false)];

      expect(cards({ devices: { ...devicesView, data } }).in_sync.value).toBe("1 of 2");
    });

    it("says so when no device is in intent", () => {
      const card = cards({ devices: { ...devicesView, data: [device("1", "in_sync", false)] } }).in_sync;

      expect(card.value).toBeNull();
      expect(card.context).toBe("No device is in intent.");
    });

    it("has nothing to compare with when no scan ran 24 hours before", () => {
      // The fixture's first scan is seconds before now: nothing was scanned a day earlier.
      const card = cards().in_sync;

      expect(card.state).toBe("nothing_to_compare");
      expect(card.context).toBe("No scan 24 h ago to compare with.");
    });

    it("compares with the scans 24 hours before when there were some", () => {
      const early = new Date(Date.parse(timeline.start) + 60_000).toISOString();
      const lanes = timeline.lanes.map((lane, index) => ({
        ...lane,
        points: [{ ...lane.points[0]!, t: early, state: index < 4 ? "in_sync" : "missing" }, ...lane.points],
      })) as DriftTimeline["lanes"];

      const card = cards({ timeline: withTimeline({ lanes }) }).in_sync;

      expect(card.state).toBe("known");
      expect(card.context).toBe("4 of 6 in sync 24 h ago.");
    });

    it("leaves the comparison out when the range shown is not 24 hours", () => {
      const start = new Date(Date.parse(timeline.end) - 3_600_000).toISOString();

      const card = cards({ timeline: withTimeline({ start }) }).in_sync;

      expect(card.state).toBe("known");
      expect(card.context).toBe("Of the devices in intent.");
    });

    it("is unavailable in words, never zero, when the devices are not known", () => {
      const card = cards({ devices: { source: "audit", available: false, reason: "the audit trail could not be read" } }).in_sync;

      expect(card.state).toBe("unavailable");
      expect(card.value).toBeNull();
      expect(card.context).toBe("the audit trail could not be read");
    });

    it("is unavailable before the first answer", () => {
      const card = cards({ devices: undefined }).in_sync;

      expect(card).toMatchObject({ state: "unavailable", value: null, context: "Not read yet." });
    });
  });

  describe("open drifts", () => {
    it("counts open drifts on devices in intent", () => {
      // The fixture: leaf-01's undesired config is open, spine-01's drift is repaired.
      expect(cards().open_drifts.value).toBe("1");
    });

    it("gives drifts on devices no longer in intent as a separate count", () => {
      const gone: DriftEpisode = {
        ...timeline.episodes.find((e) => !e.closed_at)!,
        device_id: "11111111-1111-1111-1111-111111111111",
        name: "11111111-1111-1111-1111-111111111111",
      };
      const lanes = [...timeline.lanes, { device_id: gone.device_id, name: gone.name, in_intent: false, points: [] }] as DriftTimeline["lanes"];

      const card = cards({ timeline: withTimeline({ lanes, episodes: [...timeline.episodes, gone, { ...gone }] }) }).open_drifts;

      expect(card.value).toBe("1");
      expect(card.context).toContain("2 more on devices no longer in intent.");
    });

    it("reads zero as a number when nothing is open", () => {
      const episodes = timeline.episodes.filter((e) => e.closed_at);

      expect(cards({ timeline: withTimeline({ episodes }) }).open_drifts.value).toBe("0");
    });

    it("is unavailable in words when the timeline is", () => {
      const card = cards({ timeline: { source: "audit", available: false, reason: "locked" } }).open_drifts;

      expect(card).toMatchObject({ state: "unavailable", value: null, context: "locked" });
    });
  });

  describe("latest repair", () => {
    it("gives the duration scan to scan, the device, what repaired it and how long ago", () => {
      const card = cards().latest_repair;

      expect(card.value).toBe("5 s");
      expect(card.context).toBe("spine-01, scan to scan, repaired by an operator's reconcile, 30 s ago.");
      expect(card.context).not.toMatch(/detect/i);
    });

    it("says so when no repair is recorded", () => {
      const card = cards({ timeline: withTimeline({ latest_closed: null }) }).latest_repair;

      expect(card.value).toBeNull();
      expect(card.state).toBe("known");
      expect(card.context).toBe("No repaired drift is recorded.");
    });
  });

  describe("deploys and repairs", () => {
    it("sums the last 24 hours by outcome, with the scheduled repairs in the context", () => {
      const card = cards().deploys_repairs;

      expect(card.value).toBe("8 ok · 0 failed");
      expect(card.context).toBe("Deploys. Scheduled repairs: 0 ok · 0 failed.");
    });

    it("skips buckets with no count and buckets older than 24 hours", () => {
      const bucket = (ago: number, succeeded: number | null, failed: number | null) => ({
        t: new Date(NOW.getTime() - ago).toISOString(),
        value: null,
        by_outcome: succeeded === null ? null : { succeeded, failed },
      });
      const series = (name: string, points: unknown[]) => ({ ...HISTORY.series[2]!, name, points });
      const history = {
        ...HISTORY,
        series: [
          series("deploys", [bucket(2 * DAY, 9, 9), bucket(3_600_000, 2, 1), bucket(60_000, null, null), bucket(30_000, 1, 0)]),
          series("repairs", [bucket(3_600_000, 1, 2)]),
        ],
      } as unknown as HistoryResponse;

      const card = cards({ history }).deploys_repairs;

      expect(card.value).toBe("3 ok · 1 failed");
      expect(card.context).toBe("Deploys. Scheduled repairs: 1 ok · 2 failed.");
    });

    it("is unavailable when no bucket has a count", () => {
      const history = {
        ...HISTORY,
        series: HISTORY.series.map((s) => ({ ...s, points: s.points.map((p) => ({ ...p, by_outcome: null })) })),
      } as unknown as HistoryResponse;

      expect(cards({ history }).deploys_repairs).toMatchObject({ state: "unavailable", value: null });
    });

    it("is unavailable before the history answers", () => {
      expect(cards({ history: undefined }).deploys_repairs).toMatchObject({ state: "unavailable", value: null, context: "Not read yet." });
    });

    it("shows only the present at a past time", () => {
      const card = cards({ past: true }).deploys_repairs;

      expect(card).toMatchObject({ state: "present_only", value: null, context: "Shows only the present." });
    });
  });
});
