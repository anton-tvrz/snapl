import { describe, expect, it } from "vitest";

import type { DriftEpisode } from "../api/types";
import { TIMELINE, TIMELINE_AT_SCAN_2 } from "../test/fixtures";
import { headline, repairedBy } from "./episodes";

const timeline = TIMELINE.timeline.data!;
const spine = timeline.latest_closed!;
const NOW = new Date(timeline.end);

describe("repairedBy", () => {
  it.each([
    ["reconcile", "repaired by an operator's reconcile"],
    ["remediation", "repaired by a scheduled remediation"],
    ["deploy", "repaired by an operator's deploy"],
    ["removal", "repaired by an operator's removal"],
  ])("names a %s", (kind, expected) => {
    const episode = { ...spine, closed_by: { ...spine.closed_by!, kind } } as DriftEpisode;
    expect(repairedBy(episode)).toBe(expected);
  });

  it("says it does not know when no deploy for the device explains the repair", () => {
    expect(repairedBy({ ...spine, closed_by: null })).toBe("repaired outside snapl, or intent changed (unknown)");
  });

  it("shows a kind a newer server sent by its raw text", () => {
    const episode = { ...spine, closed_by: { ...spine.closed_by!, kind: "rollback" } } as unknown as DriftEpisode;
    expect(repairedBy(episode)).toBe("repaired by a rollback");
  });
});

describe("headline", () => {
  it("gives the latest repaired drift scan to scan, and what repaired it", () => {
    const text = headline(timeline, NOW);

    expect(text.latest).toEqual({
      name: "spine-01",
      duration: "5 s",
      sentence:
        "spine-01 drifted for 5 s, from the scan that first saw it to the scan that first saw it gone. It was repaired by an operator's reconcile.",
    });
  });

  it("counts the drift still open, with how long each has been open as of the shown time", () => {
    const text = headline(timeline, NOW);

    expect(text.open).toEqual({ count: 1, devices: [{ name: "leaf-01", duration: "35 s" }], others: 0 });
  });

  it("says nothing has been repaired yet, as of a past time", () => {
    const past = TIMELINE_AT_SCAN_2.timeline.data!;
    const text = headline(past, new Date(past.end));

    expect(text.latest).toBeNull();
    expect(text.open).toEqual({
      count: 2,
      devices: [
        { name: "leaf-01", duration: "1 s" },
        { name: "spine-01", duration: "1 s" },
      ],
      others: 0,
    });
  });

  it("says when no drift is open", () => {
    const text = headline({ ...timeline, open_count: 0, episodes: [spine] }, NOW);

    expect(text.open).toEqual({ count: 0, devices: [], others: 0 });
  });

  it("measures the duration from the scan times when the server gave none", () => {
    const text = headline({ ...timeline, latest_closed: { ...spine, duration_s: null } }, NOW);

    expect(text.latest?.duration).toBe("5 s");
  });

  it("counts open drift it has no episode for", () => {
    const text = headline({ ...timeline, open_count: 1, episodes: [] }, NOW);

    expect(text.open).toEqual({ count: 1, devices: [], others: 0 });
  });

  it("counts drift on devices no longer in intent apart, and does not name them", () => {
    // spec 014 FR-019: a device that left intent is known only by its id.
    const open = timeline.episodes.find((e) => !e.closed_at)!;
    const id = "11111111-1111-1111-1111-111111111111";
    const gone = { ...open, device_id: id, name: id };
    const lanes = [...timeline.lanes, { device_id: id, name: id, in_intent: false, points: [] }];

    const text = headline({ ...timeline, lanes, episodes: [...timeline.episodes, gone, { ...gone }], open_count: 3 }, NOW);

    expect(text.open).toEqual({ count: 1, devices: [{ name: "leaf-01", duration: "35 s" }], others: 2 });
    expect(JSON.stringify(text.open)).not.toContain(id);
  });

  describe("devices no longer in intent (#236)", () => {
    // leaf-01's episode is the open one. A re-seed gave the fabric new ids, so
    // nothing scans this leaf-01 any more and its episode can never close.
    const leaf = timeline.episodes.find((e) => !e.closed_at)!;
    const gone = {
      ...timeline,
      lanes: timeline.lanes.map((lane) => (lane.device_id === leaf.device_id ? { ...lane, in_intent: false } : lane)),
    };

    it("does not count their open episodes as drift open", () => {
      const text = headline(gone, NOW);

      expect(text.open).toEqual({ count: 0, devices: [], others: 1 });
    });

    it("counts them apart from drift on devices still in intent", () => {
      const other = { ...leaf, device_id: "00000000-0000-0000-0000-00000000aaaa", name: "leaf-09" };
      const text = headline({ ...gone, open_count: 2, episodes: [...gone.episodes, other] }, NOW);

      expect(text.open).toEqual({ count: 1, devices: [{ name: "leaf-09", duration: "35 s" }], others: 1 });
    });

    it("says episodes for several", () => {
      const second = { ...leaf, device_id: "00000000-0000-0000-0000-00000000bbbb", name: "leaf-08" };
      const lanes = [...gone.lanes, { ...gone.lanes[0]!, device_id: second.device_id, name: "leaf-08", in_intent: false }];
      const text = headline({ ...gone, lanes, open_count: 2, episodes: [...gone.episodes, second] }, NOW);

      expect(text.open).toEqual({ count: 0, devices: [], others: 2 });
    });
  });

  it("never calls the duration detection", () => {
    const text = headline(timeline, NOW);
    expect(JSON.stringify(text)).not.toMatch(/detect/i);
  });
});
