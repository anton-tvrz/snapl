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

    expect(text.open).toBe("1 drift open: leaf-01 for 35 s.");
  });

  it("says nothing has been repaired yet, as of a past time", () => {
    const past = TIMELINE_AT_SCAN_2.timeline.data!;
    const text = headline(past, new Date(past.end));

    expect(text.latest).toBeNull();
    expect(text.open).toBe("2 drifts open: leaf-01 for 1 s, spine-01 for 1 s.");
  });

  it("says when no drift is open", () => {
    const text = headline({ ...timeline, open_count: 0, episodes: [spine] }, NOW);

    expect(text.open).toBe("No drift open.");
  });

  it("measures the duration from the scan times when the server gave none", () => {
    const text = headline({ ...timeline, latest_closed: { ...spine, duration_s: null } }, NOW);

    expect(text.latest?.duration).toBe("5 s");
  });

  it("counts open drift it has no episode for", () => {
    const text = headline({ ...timeline, open_count: 1, episodes: [] }, NOW);

    expect(text.open).toBe("1 drift open.");
  });

  it("never calls the duration detection", () => {
    const text = headline(timeline, NOW);
    expect(JSON.stringify(text)).not.toMatch(/detect/i);
  });
});
