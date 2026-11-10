import { describe, expect, it } from "vitest";

import { ageText } from "./time";

const NOW = new Date("2026-11-07T21:00:00Z");
const ago = (seconds: number) => new Date(NOW.getTime() - seconds * 1000).toISOString();

describe("ageText", () => {
  it.each([
    [0, "just now"],
    [4, "just now"],
    [5, "5 s ago"],
    [59, "59 s ago"],
    [60, "1 min ago"],
    [3599, "59 min ago"],
    [3600, "1 h ago"],
    [86399, "23 h ago"],
    [86400, "1 d ago"],
    [86400 * 90, "90 d ago"],
  ])("%i seconds is %s", (seconds, expected) => {
    expect(ageText(ago(seconds), NOW)).toBe(expected);
  });

  it("is relative to the now it is given, never the wall clock", () => {
    const archiveNow = new Date("2026-08-01T09:00:00Z");
    expect(ageText("2026-08-01T08:58:00Z", archiveNow)).toBe("2 min ago");
  });

  it("says never for a source that has not answered", () => {
    expect(ageText(null, NOW)).toBe("never");
    expect(ageText(undefined, NOW)).toBe("never");
  });

  it("does not show a negative age when clocks disagree", () => {
    expect(ageText(ago(-30), NOW)).toBe("just now");
  });
});
