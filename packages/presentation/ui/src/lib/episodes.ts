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

/** The drift still open as of the shown time (spec 014 FR-019). */
export interface OpenDrifts {
  /** Open drifts on devices in intent, with any the server counted and did not list. */
  count: number;
  /** The devices in intent with open drift, and how long each has been open. */
  devices: { name: string; duration: string }[];
  /** Open drifts on devices no longer in intent. They are counted, never named: only their ids are known. */
  others: number;
}

export interface Headline {
  latest: { name: string; duration: string; sentence: string } | null;
  open: OpenDrifts;
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

  const open = timeline.episodes.filter((e) => !e.closed_at);
  // A device with no lane is taken to be in intent: nothing says it left.
  const gone = new Set(timeline.lanes.filter((lane) => !lane.in_intent).map((lane) => lane.device_id));
  const ours = open.filter((e) => !gone.has(e.device_id));
  // Drift the server counted without sending its episode.
  const unlisted = Math.max(0, timeline.open_count - open.length);
  return {
    latest,
    open: {
      count: ours.length + unlisted,
      devices: ours.map((e) => ({ name: e.name, duration: durationText(seconds(e.opened_at, asOf)) })),
      others: open.length - ours.length,
    },
  };
}
