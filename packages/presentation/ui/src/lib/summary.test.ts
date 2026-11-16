import { describe, expect, it } from "vitest";

import type { DeviceView } from "../api/types";
import { OVERVIEW } from "../test/fixtures";
import { fabricSummary } from "./summary";

function device(name: string, state: string, paths: Partial<Record<"missing" | "undesired" | "unclassified", number>> = {}, inIntent = true): DeviceView {
  const list = (n = 0) => Array.from({ length: n }, (_, i) => `/p${i}`);
  return {
    device_id: `00000000-0000-0000-0000-${name.replace(/\W/g, "").padStart(12, "0")}`,
    name,
    role: "leaf",
    in_intent: inIntent,
    drift_state: state,
    missing_paths: list(paths.missing),
    undesired_paths: list(paths.undesired),
    unclassified_paths: list(paths.unclassified),
  } as DeviceView;
}

describe("fabricSummary", () => {
  it("counts the devices in intent, those in sync and those scanned", () => {
    const summary = fabricSummary(OVERVIEW.devices.data!);

    expect(summary).toMatchObject({ inIntent: 6, inSync: 5, scanned: 6 });
    expect(summary.drifted.map((d) => d.name)).toEqual(["leaf-01"]);
  });

  it("ranks drifted devices by differing paths, then by name with numbers as numbers", () => {
    const summary = fabricSummary([
      device("leaf-10", "missing", { missing: 1 }),
      device("leaf-2", "undesired", { undesired: 1 }),
      device("spine-01", "missing_and_undesired", { missing: 2, undesired: 1, unclassified: 1 }),
      device("leaf-1", "in_sync"),
    ]);

    expect(summary.drifted.map((d) => [d.name, d.paths])).toEqual([
      ["spine-01", 4],
      ["leaf-2", 1],
      ["leaf-10", 1],
    ]);
  });

  it("gives each bar its length relative to the most paths", () => {
    const summary = fabricSummary([device("a", "missing", { missing: 4 }), device("b", "missing", { missing: 1 })]);

    expect(summary.drifted.map((d) => d.share)).toEqual([1, 0.25]);
  });

  it("lists a device with a scan error after the drifted ones, with no bar", () => {
    const summary = fabricSummary([device("a", "error"), device("b", "missing", { missing: 1 })]);

    expect(summary.drifted).toEqual([
      { name: "b", state: "missing", paths: 1, share: 1 },
      { name: "a", state: "error", paths: 0, share: null },
    ]);
    expect(summary.inSync).toBe(0);
  });

  it("leaves devices no longer in intent out of every count", () => {
    const summary = fabricSummary([device("a", "in_sync"), device("gone", "missing", { missing: 3 }, false)]);

    expect(summary).toEqual({ inIntent: 1, inSync: 1, scanned: 1, drifted: [] });
  });

  it("knows when nothing has been scanned, and when nothing is in intent", () => {
    expect(fabricSummary([device("a", "never_scanned"), device("b", "never_scanned")])).toEqual({ inIntent: 2, inSync: 0, scanned: 0, drifted: [] });
    expect(fabricSummary([])).toEqual({ inIntent: 0, inSync: 0, scanned: 0, drifted: [] });
  });

  it("lists a state it does not know, so a newer server's drift is not hidden", () => {
    const summary = fabricSummary([device("a", "quarantined")]);

    expect(summary.drifted).toEqual([{ name: "a", state: "quarantined", paths: 0, share: null }]);
  });
});
