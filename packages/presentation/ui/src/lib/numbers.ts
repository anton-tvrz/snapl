// The four numbers at the top of the page (spec 014 FR-001 to FR-009, research
// R4). Everything here is derived from answers the page already has: it makes
// no request. A number that is not known is said in words, never shown as zero.
import type { DeviceView, DriftTimeline, HistoryResponse, View } from "../api/types";
import { repairedBy } from "./episodes";
import { parseRunKey } from "./fabricHistory";
import { HOLD_MS, stateAt } from "./lanes";
import { ageText, durationText } from "./time";

export type NumberCardId = "in_sync" | "open_drifts" | "latest_repair" | "runs";
export type NumberCardState = "known" | "nothing_to_compare" | "unavailable" | "present_only";

export interface NumberCard {
  id: NumberCardId;
  label: string;
  state: NumberCardState;
  /** The large text. Null when there is no number to show. */
  value: string | null;
  /** One line: the comparison, the detail, or why there is no value. */
  context: string;
  /** The id of the panel that holds the detail. */
  target: string;
}

export interface NumbersInput {
  /** The devices as of the time shown. Undefined until the first answer. */
  devices: View<DeviceView[]> | undefined;
  /** The drift timeline as of the time shown. */
  timeline: View<DriftTimeline> | undefined;
  /** `/history` for `runs` over the server's default range: the last 24 hours, whatever range the page shows. */
  runs: HistoryResponse | undefined;
  /** The time shown: the server's now, or the "as of" time. */
  now: Date;
  /** True when a past time is shown. */
  past: boolean;
}

const DAY_MS = 86_400_000;
const NOT_READ = "Not read yet.";

function unavailable(view: View<unknown> | undefined): string {
  return view?.reason ?? NOT_READ;
}

/**
 * The timeline when it can answer "24 hours ago": its range is the default 24
 * hours. With any other range the comparison is left out, not guessed.
 */
function dayBefore(timeline: DriftTimeline | null | undefined): { at: Date; scanned: boolean } | null {
  if (!timeline) return null;
  const start = Date.parse(timeline.start);
  if (Math.abs(Date.parse(timeline.end) - start - DAY_MS) > 5 * 60_000) return null;
  // A scan's state is held for HOLD_MS, so this is "the scans around 24 h ago".
  const at = new Date(start + HOLD_MS);
  const scanned = timeline.lanes.some((lane) => lane.in_intent && stateAt(lane.points, at).point !== undefined);
  return { at, scanned };
}

function inSync(input: NumbersInput): NumberCard {
  const base = { id: "in_sync" as const, label: "Devices in sync", target: "fabric" };
  const devices = input.devices?.data;
  if (!devices) return { ...base, state: "unavailable", value: null, context: unavailable(input.devices) };
  const intent = devices.filter((device) => device.in_intent);
  if (intent.length === 0) return { ...base, state: "known", value: null, context: "No device is in intent." };
  const value = `${intent.filter((device) => device.drift_state === "in_sync").length} of ${intent.length}`;

  const timeline = input.timeline?.data;
  const before = dayBefore(timeline);
  if (!before || !timeline) return { ...base, state: "known", value, context: "Of the devices in intent." };
  if (!before.scanned) return { ...base, state: "nothing_to_compare", value, context: "No scan 24 h ago to compare with." };
  const lanes = timeline.lanes.filter((lane) => lane.in_intent);
  const then = lanes.filter((lane) => stateAt(lane.points, before.at).state === "in_sync").length;
  return { ...base, state: "known", value, context: `${then} of ${lanes.length} in sync 24 h ago.` };
}

function openDrifts(input: NumbersInput): NumberCard {
  const base = { id: "open_drifts" as const, label: "Open drifts", target: "drift-over-time" };
  const timeline = input.timeline?.data;
  if (!timeline) return { ...base, state: "unavailable", value: null, context: unavailable(input.timeline) };
  const inIntent = new Set(timeline.lanes.filter((lane) => lane.in_intent).map((lane) => lane.device_id));
  const open = timeline.episodes.filter((episode) => !episode.closed_at);
  const ours = open.filter((episode) => inIntent.has(episode.device_id)).length;
  const others = open.length - ours;
  const context = [
    ours === 0 ? "No drift open on a device in intent." : "On devices in intent.",
    others > 0 ? `${others} more on devices no longer in intent.` : "",
  ]
    .filter(Boolean)
    .join(" ");
  return { ...base, state: "known", value: String(ours), context };
}

function latestRepair(input: NumbersInput): NumberCard {
  const base = { id: "latest_repair" as const, label: "Latest repair", target: "drift-over-time" };
  const timeline = input.timeline?.data;
  if (!timeline) return { ...base, state: "unavailable", value: null, context: unavailable(input.timeline) };
  const episode = timeline.latest_closed;
  if (!episode) return { ...base, state: "known", value: null, context: "No repaired drift is recorded." };
  const closed = episode.closed_at ?? input.now.toISOString();
  const seconds = episode.duration_s ?? (Date.parse(closed) - Date.parse(episode.opened_at)) / 1000;
  return {
    ...base,
    state: "known",
    value: durationText(seconds),
    // Scan to scan: from the scan that first saw the drift to the scan that first saw it gone.
    context: `${episode.name}, scan to scan, ${repairedBy(episode)}, ${ageText(closed, input.now)}.`,
  };
}

function runs(input: NumbersInput): NumberCard {
  const base = { id: "runs" as const, label: "Runs, 24 h", target: "fabric-history" };
  // The runs are asked for the 24 hours ending now, not as of the time shown.
  if (input.past) return { ...base, state: "present_only", value: null, context: "Shows only the present." };
  if (!input.runs) return { ...base, state: "unavailable", value: null, context: NOT_READ };
  const series = input.runs.series.find((one) => one.name === "runs");
  if (!series) return { ...base, state: "unavailable", value: null, context: "Not in this recording." };
  let total = 0;
  let failed = 0;
  let running = 0;
  // Every bucket of the answer: it covers the last 24 hours, its first bucket from the hour before.
  for (const point of series.points) {
    total += point.value ?? 0;
    for (const [key, count] of Object.entries(point.by_outcome ?? {})) {
      const { outcome } = parseRunKey(key);
      if (outcome === "failed") failed += count ?? 0;
      if (outcome === "running") running += count ?? 0;
    }
  }
  if (total === 0) return { ...base, state: "known", value: "0", context: "No run started in the last 24 h." };
  const context = [failed === 0 ? "None failed" : `${failed} failed`, running > 0 ? `${running} running` : ""].filter(Boolean).join(", ");
  return { ...base, state: "known", value: String(total), context: `${context}.` };
}

/** The number row's four cards, in order. */
export function numberCards(input: NumbersInput): NumberCard[] {
  return [inSync(input), openDrifts(input), latestRepair(input), runs(input)];
}
