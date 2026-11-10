import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// Text from devices and the audit trail is rendered as text only (spec 011
// FR-006). ESLint bans the ways around React's escaping; this test makes sure
// nobody switches that ban off, and catches a file ESLint was told to skip.
const SRC = join(import.meta.dirname, ".");
const BANNED = ["dangerouslySetInnerHTML", "innerHTML", "outerHTML", "insertAdjacentHTML"];

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name) && !name.endsWith(".d.ts") ? [path] : [];
  });
}

describe("device text is rendered as text", () => {
  const files = sources(SRC);

  it("finds the UI's source files", () => {
    expect(files.length).toBeGreaterThan(5);
  });

  it("no source file writes raw HTML", () => {
    const offenders = files.filter((file) => BANNED.some((word) => readFileSync(file, "utf8").includes(word)));
    expect(offenders).toEqual([]);
  });

  it("no source file switches the lint ban off", () => {
    const offenders = files.filter((file) => /eslint-disable[^\n]*no-restricted-syntax/.test(readFileSync(file, "utf8")));
    expect(offenders).toEqual([]);
  });
});
