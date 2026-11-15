// The headline's words for drift episodes (spec 011 FR-025). The duration is
// scan to scan: from the scan that first saw the drift to the scan that first
// saw it gone. It is never called time to detect.
import { lookup } from "../api/open";
import type { DriftEpisode, DriftTimeline } from "../api/types";
import { durationText } from "./time";

const REPAIRED_BY: Record<string, string> = {
  reconcile: "repaired by an operator's reconcile",
  remediation: "repaired by a scheduled remediation",
  deploy: "repaired by an operator's deploy",
};

/** What repaired an episode, in words. */
export function repairedBy(episode: DriftEpisode): string {
  const close = episode.closed_by;
  if (!close) return "repaired outside snapl, or intent changed (unknown)";
  return lookup<string>(REPAIRED_BY, close.kind, (raw) => `repaired by a ${raw}`);
}

export interface Headline {
  latest: { name: string; duration: string; sentence: string } | null;
  open: string;
}

function seconds(from: string, to: string | Date): number {
  return (new Date(to).getTime() - new Date(from).getTime()) / 1000;
}

/** The headline for a timeline answered as of `asOf`. */
export function headline(timeline: DriftTimeline, asOf: Date): Headline {
  const episode = timeline.latest_closed;
  let latest: Headline["latest"] = null;
  if (episode) {
    const duration = durationText(episode.duration_s ?? seconds(episode.opened_at, episode.closed_at ?? asOf));
    latest = {
      name: episode.name,
      duration,
      sentence: `${episode.name} drifted for ${duration}, from the scan that first saw it to the scan that first saw it gone. It was ${repairedBy(episode)}.`,
    };
  }

  // A device that left intent is not scanned any more, so its last drifted scan
  // leaves an episode that can never close. It stays in the data, and is counted
  // apart from drift an operator can still act on (#236).
  const gone = new Set(timeline.lanes.filter((lane) => !lane.in_intent).map((lane) => lane.device_id));
  const unclosed = timeline.episodes.filter((e) => !e.closed_at);
  const open = unclosed.filter((e) => !gone.has(e.device_id));
  const left = unclosed.length - open.length;
  const count = Math.max(timeline.open_count - left, 0);

  const each = open.map((e) => `${e.name} for ${durationText(seconds(e.opened_at, asOf))}`).join(", ");
  const noun = count === 1 ? "drift" : "drifts";
  const still = count === 0 ? "No drift open." : each ? `${count} ${noun} open: ${each}.` : `${count} ${noun} open.`;
  if (left === 0) return { latest, open: still };
  const where = left === 1 ? "1 episode never closed on a device" : `${left} episodes never closed on devices`;
  return { latest, open: `${still} ${where} no longer in intent.` };
}
