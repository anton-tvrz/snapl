import { describe, expect, it } from "vitest";

import { lookup, SOURCE_LABELS, sourceLabel } from "./open";

describe("lookup", () => {
  const table = { in_sync: "In sync", missing: "Missing intent" };

  it("returns the entry for a value the UI knows", () => {
    expect(lookup(table, "missing", (raw) => `?${raw}`)).toBe("Missing intent");
  });

  it("builds a neutral entry from the raw text for a value a newer server sent", () => {
    expect(lookup(table, "quarantined", (raw) => `?${raw}`)).toBe("?quarantined");
  });

  it("does not mistake an inherited property name for a known value", () => {
    expect(lookup(table, "toString", (raw) => `?${raw}`)).toBe("?toString");
    expect(lookup(table, "constructor", (raw) => `?${raw}`)).toBe("?constructor");
  });
});

describe("sourceLabel", () => {
  it("names every source in the contract", () => {
    expect(Object.keys(SOURCE_LABELS).sort()).toEqual(["audit", "ci", "lab_runs", "prometheus", "sot", "temporal"]);
    expect(sourceLabel("sot")).toBe("Source of Truth");
    expect(sourceLabel("audit")).toBe("Audit trail");
  });

  it("shows a source it does not know by its raw name", () => {
    expect(sourceLabel("netbox")).toBe("netbox");
  });
});
