// How state is drawn. The rules are in dev/skills/dashboard/SKILL.md; this
// module is their one implementation, and encoding.test.ts checks them.
//
// The rule that shapes everything here: status green and red are about 4 apart
// (OKLab ×100) under red-green colour-vision deficiency, where 8 is the target.
// So colour never tells two states apart. Every state has its own shape, its
// own glyph and its own label; colour only groups states into four kinds.
import { lookup } from "../api/open";

export type Shape = "circle" | "triangle" | "square" | "diamond" | "hexagon" | "octagon" | "ring" | "pill";
export type Tone = "ok" | "drift" | "fail" | "unknown";

export interface Encoding {
  label: string;
  shape: Shape;
  glyph: string;
  tone: Tone;
}

/** Fills are the reserved status colours, the same in light and dark. */
export const TONES: Record<Tone, { fill: string; onFill: string }> = {
  ok: { fill: "#0ca30c", onFill: "#ffffff" },
  drift: { fill: "#fab219", onFill: "#0b0b0b" },
  fail: { fill: "#d03b3b", onFill: "#ffffff" },
  unknown: { fill: "#898781", onFill: "#ffffff" },
};

/** The panel surface each mode draws on, and the inks used on it (index.css). */
export const SURFACE = { light: "#ffffff", dark: "#1a1d21" } as const;
export const INK = {
  // The outline draws every shape, so a shape reads even where its fill is
  // close to the surface (amber on white is under 2:1).
  light: { outline: "#1b1c1e", text: "#1b1c1e", muted: "#5b5f66" },
  dark: { outline: "#eceef0", text: "#eceef0", muted: "#a3a9b1" },
} as const;

export const DRIFT_ENCODING = {
  in_sync: { label: "In sync", shape: "circle", glyph: "✓", tone: "ok" },
  missing: { label: "Missing intent", shape: "triangle", glyph: "−", tone: "drift" },
  undesired: { label: "Undesired config", shape: "square", glyph: "+", tone: "drift" },
  missing_and_undesired: { label: "Missing and undesired", shape: "diamond", glyph: "±", tone: "drift" },
  drifted: { label: "Drifted", shape: "hexagon", glyph: "≠", tone: "drift" },
  error: { label: "Scan error", shape: "octagon", glyph: "!", tone: "fail" },
  never_scanned: { label: "Never scanned", shape: "ring", glyph: "·", tone: "unknown" },
} as const satisfies Record<string, Encoding>;

/** A state's encoding. One a newer server sent keeps its raw text (FR-008). */
export function driftEncoding(state: string): Encoding {
  return lookup<Encoding>(DRIFT_ENCODING, state, (raw) => ({ label: raw, shape: "pill", glyph: "?", tone: "unknown" }));
}

export interface RunEncoding {
  label: string;
  glyph: string;
  tone: Tone;
}

export const RUN_ENCODING = {
  running: { label: "Running", glyph: "▶", tone: "unknown" },
  succeeded: { label: "Succeeded", glyph: "✓", tone: "ok" },
  failed: { label: "Failed", glyph: "✕", tone: "fail" },
  cancelled: { label: "Cancelled", glyph: "⊘", tone: "unknown" },
  unfinished: { label: "Unfinished", glyph: "…", tone: "drift" },
} as const satisfies Record<string, RunEncoding>;

export function runEncoding(state: string): RunEncoding {
  return lookup<RunEncoding>(RUN_ENCODING, state, (raw) => ({ label: raw, glyph: "?", tone: "unknown" }));
}

const SIDES: Partial<Record<Shape, { sides: number; rotate: number }>> = {
  triangle: { sides: 3, rotate: -90 },
  square: { sides: 4, rotate: 45 },
  diamond: { sides: 4, rotate: 0 },
  hexagon: { sides: 6, rotate: 0 },
  octagon: { sides: 8, rotate: 22.5 },
};

/** A polygon's corners around the origin, inside radius `r`. Empty for round shapes. */
export function shapePoints(shape: Shape, r: number): [number, number][] {
  const polygon = SIDES[shape];
  if (!polygon) return [];
  return Array.from({ length: polygon.sides }, (_, i) => {
    const angle = ((polygon.rotate + (360 / polygon.sides) * i) * Math.PI) / 180;
    return [r * Math.cos(angle), r * Math.sin(angle)];
  });
}

/**
 * What a NAF block did in one run (spec 011 FR-026). The audit trail records
 * steps that ended, never one that started, so no status reads as in progress.
 */
export const BLOCK_ENCODING = {
  done: { label: "Done", glyph: "✓", tone: "ok" },
  failed: { label: "Failed", glyph: "✕", tone: "fail" },
  not_reached: { label: "Not reached", glyph: "○", tone: "unknown" },
  not_recorded: { label: "Not recorded", glyph: "–", tone: "unknown" },
  surface: { label: "You are here", glyph: "◉", tone: "unknown" },
} as const satisfies Record<string, RunEncoding>;

export function blockEncoding(status: string): RunEncoding {
  return lookup<RunEncoding>(BLOCK_ENCODING, status, (raw) => ({ label: raw, glyph: "?", tone: "unknown" }));
}

const BLOCK_NAMES: Record<string, string> = {
  intent: "Intent",
  executor: "Executor",
  collector: "Collector",
  observability: "Observability",
  orchestrator: "Orchestrator",
  presentation: "Presentation",
};

/** A NAF block's name. One a newer server sent keeps its raw text. */
export function blockName(block: string): string {
  return lookup<string>(BLOCK_NAMES, block, (raw) => raw);
}

/** A CI job's or quality gate's result. */
export const JOB_ENCODING = {
  success: { label: "Passed", glyph: "✓", tone: "ok" },
  failure: { label: "Failed", glyph: "✕", tone: "fail" },
  cancelled: { label: "Cancelled", glyph: "⊘", tone: "unknown" },
  skipped: { label: "Skipped", glyph: "↷", tone: "unknown" },
  in_progress: { label: "Running", glyph: "▶", tone: "unknown" },
  queued: { label: "Queued", glyph: "…", tone: "unknown" },
  neutral: { label: "Neutral", glyph: "○", tone: "unknown" },
} as const satisfies Record<string, RunEncoding>;

export function jobEncoding(result: string): RunEncoding {
  return lookup<RunEncoding>(JOB_ENCODING, result, (raw) => ({ label: raw, glyph: "?", tone: "unknown" }));
}

/** A recorded live lab run's status. */
export const LAB_ENCODING = {
  running: { label: "Running", glyph: "▶", tone: "unknown" },
  passed: { label: "Passed", glyph: "✓", tone: "ok" },
  failed: { label: "Failed", glyph: "✕", tone: "fail" },
  interrupted: { label: "Interrupted", glyph: "⊘", tone: "drift" },
  incomplete: { label: "Incomplete", glyph: "…", tone: "drift" },
  unreadable: { label: "Unreadable", glyph: "!", tone: "fail" },
} as const satisfies Record<string, RunEncoding>;

export function labEncoding(status: string): RunEncoding {
  return lookup<RunEncoding>(LAB_ENCODING, status, (raw) => ({ label: raw, glyph: "?", tone: "unknown" }));
}
