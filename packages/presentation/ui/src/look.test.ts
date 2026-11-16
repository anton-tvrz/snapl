import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

// The look rules of spec 014 (FR-011, FR-012, SC-006) as a test: spacing in
// multiples of 8 px, type from the one scale. dev/skills/dashboard/SKILL.md,
// "Look", says why. Files not yet restyled are listed in TO_RESTYLE, and the
// list can only shrink: a listed file with nothing left to fix fails too.

const SRC = import.meta.dirname;

// Tailwind steps: 2 = 8 px, 4 = 16 px, 6 = 24 px, 8 = 32 px.
const SPACING = new Set(["0", "2", "4", "6", "8", "px", "auto"]);
// Inside a chip, a badge, a mark or a table cell, 2, 4 and 6 px are allowed.
// The class string must carry the `chip` or `cell` marker to say so.
const SMALL = new Set(["0.5", "1", "1.5"]);
const TYPE = new Set(["meta", "body", "title", "heading", "number", "number-lg"]);

const SPACE_CLASS = /^(?:[\w-]+:)*-?(?:[mp][trblxyse]?|gap(?:-[xy])?|space-[xy])-(.+)$/;
const SIZE_CLASS = /^(?:[\w-]+:)*text-(xs|sm|base|lg|[2-9]?xl|\[[^\]]*(?:px|rem|em)\])$/;
const STRING = /"([^"\n]*)"|'([^'\n]*)'|`([^`]*)`/g;

/** The classes in `source` that break the look rules. */
export function lookViolations(source: string): string[] {
  const found: string[] = [];
  for (const match of source.matchAll(STRING)) {
    const text = match[1] ?? match[2] ?? match[3] ?? "";
    const tokens = text.split(/[\s${}'"()]+/).filter(Boolean);
    const small = tokens.includes("chip") || tokens.includes("cell");
    for (const token of tokens) {
      const space = SPACE_CLASS.exec(token);
      if (space && !SPACING.has(space[1]!) && !(small && SMALL.has(space[1]!))) found.push(token);
      if (SIZE_CLASS.test(token)) found.push(token);
    }
  }
  return found;
}

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === "test" ? [] : sources(path);
    return /\.tsx$/.test(entry.name) && !/\.test\.tsx$/.test(entry.name) ? [path] : [];
  });
}

// Still to be restyled, slice by slice (specs/014-dashboard-look/tasks.md).
// Empty when spec 014 is complete.
const TO_RESTYLE = new Set<string>([
  "components/Command.tsx",
  "components/DeviceDrawer.tsx",
  "components/LoopStrip.tsx",
  "components/ModeBanner.tsx",
  "components/Shell.tsx",
  "components/SourceStrip.tsx",
  "components/panels/Ci.tsx",
  "components/panels/LabEvidence.tsx",
  "components/panels/Links.tsx",
  "components/panels/Runs.tsx",
  "components/panels/Schedule.tsx",
  "components/panels/Undesired.tsx",
  "components/topology/DeviceList.tsx",
  "components/topology/Legend.tsx",
  "components/topology/Topology.tsx",
]);

describe("the look rules", () => {
  it("flags spacing outside the 8 px scale", () => {
    expect(lookViolations('className="mt-3 p-4 gap-x-2"')).toEqual(["mt-3"]);
    expect(lookViolations('className="sm:px-6 lg:py-1"')).toEqual(["lg:py-1"]);
    expect(lookViolations("className={`mb-3 ${open ? 'p-5' : ''}`}")).toEqual(["mb-3", "p-5"]);
  });

  it("allows 2, 4 and 6 px only where the string is marked as a chip or a cell", () => {
    expect(lookViolations('className="chip px-1.5 py-0.5"')).toEqual([]);
    expect(lookViolations('className="cell py-1 pr-3"')).toEqual(["pr-3"]);
    expect(lookViolations('className="px-1.5 py-0.5"')).toEqual(["px-1.5", "py-0.5"]);
  });

  it("flags type sizes outside the scale, and leaves colours alone", () => {
    expect(lookViolations('className="text-xs text-muted text-sm text-3xl text-[13px]"')).toEqual([
      "text-xs",
      "text-sm",
      "text-3xl",
      "text-[13px]",
    ]);
    expect(lookViolations('className="text-meta text-body text-number-lg text-ink"')).toEqual([]);
    for (const size of TYPE) expect(lookViolations(`className="text-${size}"`)).toEqual([]);
  });

  it("holds in every component, apart from the ones still to be restyled", () => {
    const broken: Record<string, string[]> = {};
    const clean: string[] = [];
    for (const path of sources(SRC)) {
      const name = relative(SRC, path);
      const found = lookViolations(readFileSync(path, "utf8"));
      if (TO_RESTYLE.has(name)) {
        if (found.length === 0) clean.push(name);
      } else if (found.length > 0) {
        broken[name] = found;
      }
    }
    expect(broken, "classes outside the look rules").toEqual({});
    expect(clean, "restyled already: remove from TO_RESTYLE").toEqual([]);
  });

  it("lists only files that exist", () => {
    const names = new Set(sources(SRC).map((path) => relative(SRC, path)));
    expect([...TO_RESTYLE].filter((name) => !names.has(name))).toEqual([]);
  });
});
