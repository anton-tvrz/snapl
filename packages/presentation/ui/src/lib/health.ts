// How a device's health is shown (spec 016). Health sits beside the drift
// state and never changes it: a device can be in sync and unhealthy (FR-010).
import { lookup } from "../api/open";
import type { DeviceHealthView, DeviceView, HealthEpisode } from "../api/types";
import type { Tone } from "./encoding";
import { clock, type Range } from "./lanes";

export interface HealthMark {
  /** The glyph drawn beside the drift mark, or null for no mark. */
  glyph: string | null;
  label: string;
  tone: Tone;
}

const MARKS: Record<string, HealthMark> = {
  healthy: { glyph: null, label: "Healthy", tone: "ok" },
  unhealthy: { glyph: "!", label: "Unhealthy", tone: "fail" },
  unknown: { glyph: "?", label: "Health unknown", tone: "unknown" },
};

/** The mark for a state. A state a newer server sends is shown by its raw text, as unknown. */
export function healthMark(state: string): HealthMark {
  return lookup<HealthMark>(MARKS, state, (raw) => ({ glyph: "?", label: raw, tone: "unknown" }));
}

/** The state and its reasons in one line: "Unhealthy: BGP 10.10.1.1 active (expected established); …". */
export function healthTitle(health: DeviceHealthView): string {
  const label = healthMark(health.state).label;
  const reasons = (health.reasons ?? []).map((reason) => reason.text);
  return reasons.length === 0 ? label : `${label}: ${reasons.join("; ")}`;
}

/**
 * The fabric's health in a few words, over the devices in intent: "2 unhealthy",
 * "1 unhealthy, 2 health unknown", or "all healthy". Null when that cannot be
 * said: no health is recorded, or a device has none while the rest are healthy.
 */
export function healthContext(devices: readonly DeviceView[]): string | null {
  const states = devices.filter((device) => device.in_intent).map((device) => device.health?.state);
  if (!states.some((state) => state !== undefined)) return null;
  const unhealthy = states.filter((state) => state === "unhealthy").length;
  const unknown = states.filter((state) => state !== undefined && state !== "healthy" && state !== "unhealthy").length;
  const parts = [unhealthy > 0 ? `${unhealthy} unhealthy` : null, unknown > 0 ? `${unknown} health unknown` : null].filter(
    (part): part is string => part !== null,
  );
  if (parts.length > 0) return parts.join(", ");
  return states.every((state) => state === "healthy") ? "all healthy" : null;
}

/** One unhealthy stretch as drawn: its times in ms, open when it has not closed. */
export interface HealthStretch {
  start: number;
  end: number;
  open: boolean;
  reasons: readonly string[];
}

/** A device's unhealthy stretches that fall inside the domain; an open one runs to now. */
export function healthStretches(episodes: readonly HealthEpisode[], domain: Range, now: Date): HealthStretch[] {
  return episodes
    .map((episode) => ({
      start: Date.parse(episode.opened_at),
      end: episode.closed_at ? Date.parse(episode.closed_at) : now.getTime(),
      open: !episode.closed_at,
      reasons: episode.reasons.map((reason) => reason.text),
    }))
    .filter((stretch) => stretch.end > domain.start.getTime() && stretch.start < domain.end.getTime());
}

/** "unhealthy 18:50:48 UTC to 19:50:48 UTC", or "unhealthy from 20:20:48 UTC, still now". */
export function stretchText(stretch: HealthStretch, domain: Range): string {
  const from = clock(domain, new Date(stretch.start));
  return stretch.open ? `unhealthy from ${from}, still now` : `unhealthy ${from} to ${clock(domain, new Date(stretch.end))}`;
}

/** The stretch's words with its reasons, for its title. */
export function stretchTitle(stretch: HealthStretch, domain: Range): string {
  const text = stretchText(stretch, domain);
  const reasons = stretch.reasons.join("; ");
  return `U${text.slice(1)}${reasons ? `: ${reasons}` : ""}`;
}
