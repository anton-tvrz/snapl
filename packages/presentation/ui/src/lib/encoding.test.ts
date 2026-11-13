import { describe, expect, it } from "vitest";

import { contrast } from "./color";
import {
  BLOCK_ENCODING,
  JOB_ENCODING,
  jobEncoding,
  LAB_ENCODING,
  labEncoding,
  blockEncoding,
  blockName,
  DRIFT_ENCODING,
  driftEncoding,
  INK,
  RUN_ENCODING,
  runEncoding,
  shapePoints,
  SURFACE,
  TONES,
} from "./encoding";

const DRIFT_STATES = [
  "in_sync",
  "missing",
  "undesired",
  "missing_and_undesired",
  "drifted",
  "error",
  "never_scanned",
] as const;
const RUN_STATES = ["running", "succeeded", "failed", "cancelled", "unfinished"] as const;
const MODES = ["light", "dark"] as const;

describe("drift state encoding", () => {
  it("covers every drift state in the contract", () => {
    expect(Object.keys(DRIFT_ENCODING).sort()).toEqual([...DRIFT_STATES].sort());
  });

  it("gives every state its own shape and its own glyph, so colour is never the only difference", () => {
    const shapes = DRIFT_STATES.map((state) => DRIFT_ENCODING[state].shape);
    const glyphs = DRIFT_STATES.map((state) => DRIFT_ENCODING[state].glyph);

    expect(new Set(shapes).size).toBe(DRIFT_STATES.length);
    expect(new Set(glyphs).size).toBe(DRIFT_STATES.length);
  });

  it("gives every state its own label, in words", () => {
    const labels = DRIFT_STATES.map((state) => DRIFT_ENCODING[state].label);

    expect(new Set(labels).size).toBe(DRIFT_STATES.length);
    expect(DRIFT_ENCODING.in_sync.label).toBe("In sync");
    expect(DRIFT_ENCODING.missing.label).toBe("Missing intent");
    expect(DRIFT_ENCODING.undesired.label).toBe("Undesired config");
    expect(DRIFT_ENCODING.never_scanned.label).toBe("Never scanned");
  });

  it("uses colour for the kind of state only: in sync, drifted, failed, unknown", () => {
    expect(DRIFT_ENCODING.in_sync.tone).toBe("ok");
    for (const state of ["missing", "undesired", "missing_and_undesired", "drifted"] as const) {
      expect(DRIFT_ENCODING[state].tone).toBe("drift");
    }
    expect(DRIFT_ENCODING.error.tone).toBe("fail");
    expect(DRIFT_ENCODING.never_scanned.tone).toBe("unknown");
  });

  it("shows a state a newer server sent by its raw text, with a neutral mark of its own", () => {
    const unknown = driftEncoding("quarantined");

    expect(unknown.label).toBe("quarantined");
    expect(unknown.tone).toBe("unknown");
    expect(DRIFT_STATES.map((state) => DRIFT_ENCODING[state].shape)).not.toContain(unknown.shape);
    expect(DRIFT_STATES.map((state) => DRIFT_ENCODING[state].glyph)).not.toContain(unknown.glyph);
    expect(driftEncoding("in_sync")).toBe(DRIFT_ENCODING.in_sync);
  });
});

describe("contrast (WCAG 2.1 AA)", () => {
  it.each(MODES)("the outline that draws every shape has at least 3:1 on the %s surface", (mode) => {
    expect(contrast(INK[mode].outline, SURFACE[mode])).toBeGreaterThanOrEqual(3);
  });

  it.each(MODES)("text has at least 4.5:1 on the %s surface", (mode) => {
    expect(contrast(INK[mode].text, SURFACE[mode])).toBeGreaterThanOrEqual(4.5);
    expect(contrast(INK[mode].muted, SURFACE[mode])).toBeGreaterThanOrEqual(4.5);
  });

  it("the glyph inside each fill has at least 3:1 against it", () => {
    for (const tone of Object.values(TONES)) {
      expect(contrast(tone.onFill, tone.fill)).toBeGreaterThanOrEqual(3);
    }
  });

  it("the fills are the reserved status colours, the same in both modes", () => {
    expect(TONES.ok.fill).toBe("#0ca30c");
    expect(TONES.drift.fill).toBe("#fab219");
    expect(TONES.fail.fill).toBe("#d03b3b");
  });
});

describe("run state encoding", () => {
  it("covers every run state with its own glyph and label", () => {
    expect(Object.keys(RUN_ENCODING).sort()).toEqual([...RUN_STATES].sort());
    expect(new Set(RUN_STATES.map((state) => RUN_ENCODING[state].glyph)).size).toBe(RUN_STATES.length);
    expect(new Set(RUN_STATES.map((state) => RUN_ENCODING[state].label)).size).toBe(RUN_STATES.length);
  });

  it("shows an unknown run state by its raw text", () => {
    expect(runEncoding("paused").label).toBe("paused");
    expect(runEncoding("failed")).toBe(RUN_ENCODING.failed);
  });
});

const BLOCK_STATUSES = ["done", "failed", "not_reached", "not_recorded", "surface"] as const;

describe("loop block encoding", () => {
  it("covers every block status with its own glyph and label", () => {
    expect(Object.keys(BLOCK_ENCODING).sort()).toEqual([...BLOCK_STATUSES].sort());
    expect(new Set(BLOCK_STATUSES.map((status) => BLOCK_ENCODING[status].glyph)).size).toBe(BLOCK_STATUSES.length);
    expect(new Set(BLOCK_STATUSES.map((status) => BLOCK_ENCODING[status].label)).size).toBe(BLOCK_STATUSES.length);
  });

  it("has no status that reads as in progress", () => {
    for (const status of BLOCK_STATUSES) {
      expect(BLOCK_ENCODING[status].label).not.toMatch(/progress|running|working/i);
    }
  });

  it("shows an unknown status and an unknown block by their raw text", () => {
    expect(blockEncoding("skipped").label).toBe("skipped");
    expect(blockEncoding("done")).toBe(BLOCK_ENCODING.done);
    expect(blockName("executor")).toBe("Executor");
    expect(blockName("verifier")).toBe("verifier");
  });
});

describe("CI job and lab run encodings", () => {
  const JOB_RESULTS = ["success", "failure", "cancelled", "skipped", "in_progress", "queued", "neutral"] as const;
  const LAB_STATUSES = ["running", "passed", "failed", "interrupted", "incomplete", "unreadable"] as const;

  it.each([
    ["CI job results", JOB_ENCODING, JOB_RESULTS],
    ["lab run statuses", LAB_ENCODING, LAB_STATUSES],
  ] as const)("covers every one of the %s with its own glyph and label", (_, table, keys) => {
    const entries = keys.map((key) => (table as Record<string, { glyph: string; label: string }>)[key]!);
    expect(Object.keys(table).sort()).toEqual([...keys].sort());
    expect(new Set(entries.map((e) => e.glyph)).size).toBe(keys.length);
    expect(new Set(entries.map((e) => e.label)).size).toBe(keys.length);
  });

  it("shows an unknown result or status by its raw text", () => {
    expect(jobEncoding("stale").label).toBe("stale");
    expect(labEncoding("archived").label).toBe("archived");
    expect(jobEncoding("failure").tone).toBe("fail");
    expect(labEncoding("passed").tone).toBe("ok");
  });
});

describe("shapePoints", () => {
  it("draws each polygon with the right number of corners inside its radius", () => {
    const corners = { triangle: 3, square: 4, diamond: 4, hexagon: 6, octagon: 8 } as const;
    for (const [shape, count] of Object.entries(corners)) {
      const points = shapePoints(shape as keyof typeof corners, 10);
      expect(points).toHaveLength(count);
      for (const [x, y] of points) expect(Math.hypot(x, y)).toBeLessThanOrEqual(10.0001);
    }
  });

  it("has no corners for the round shapes", () => {
    expect(shapePoints("circle", 10)).toEqual([]);
    expect(shapePoints("ring", 10)).toEqual([]);
  });

  it("draws a square and a diamond differently", () => {
    expect(shapePoints("square", 10)).not.toEqual(shapePoints("diamond", 10));
  });
});
