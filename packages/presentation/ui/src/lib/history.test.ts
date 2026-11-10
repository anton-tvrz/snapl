import { describe, expect, it } from "vitest";

import { HISTORY } from "../test/fixtures";
import { bucketFor, historyRows, markerText } from "./history";

const DAY = 86_400_000;

describe("historyRows", () => {
  const rows = historyRows(HISTORY);

  it("gives one row per hour, newest first", () => {
    expect(rows).toHaveLength(25);
    expect(rows[0]!.start).toBe("2026-11-07T21:00:00Z");
    expect(rows.at(-1)!.start).toBe("2026-11-06T21:00:00Z");
  });

  it("folds each hour's scans into it: the count and the most any scan saw", () => {
    expect(rows[0]).toMatchObject({
      scans: 3,
      mostDrifted: 2,
      mostUndesired: 1,
      deploys: { succeeded: 8, failed: 0 },
      repairs: { succeeded: 0, failed: 0 },
      quiet: false,
    });
  });

  it("marks an hour with no scan and no deploy as quiet, not zeros", () => {
    expect(rows[1]).toMatchObject({ quiet: true, scans: 0 });
  });

  it("says unknown when the scans of an hour recorded no undesired count", () => {
    const series = HISTORY.series.map((s) =>
      s.name === "undesired_paths" ? { ...s, points: s.points.map((p) => ({ ...p, value: null })) } : s,
    );

    expect(historyRows({ ...HISTORY, series })[0]!.mostUndesired).toBe("unknown");
  });
});

describe("history helpers", () => {
  it("picks a bucket that keeps the table readable", () => {
    expect(bucketFor(DAY)).toBe("1h");
    expect(bucketFor(2 * DAY)).toBe("1h");
    expect(bucketFor(7 * DAY)).toBe("1d");
    expect(bucketFor(120 * DAY)).toBe("1w");
  });

  it("describes a repair marker", () => {
    const marker = HISTORY.series[0]!.markers![0]!;
    expect(markerText(marker, new Date("2026-11-07T21:20:48.919877Z"))).toBe(
      "33 s ago: reconcile reconcile-217b6000-0e6c-4919-81d9-9f6a1075cd1d (operator)",
    );
  });
});
