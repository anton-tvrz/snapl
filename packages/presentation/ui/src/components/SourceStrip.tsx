import { useContext } from "react";

import { sourceLabel } from "../api/open";
import type { View } from "../api/types";
import { ageText } from "../lib/time";
import { Command } from "./Command";
import { ArchiveMode, NOT_IN_ARCHIVE } from "./ViewFrame";

interface Props {
  sources: View<null>[] | undefined;
  now: Date;
}

type State = "up" | "stale" | "down" | "not in this archive";

// State is said in a word and a mark, never by colour alone (spec 011 FR-012).
const MARKS: Record<State, { mark: string; className: string }> = {
  up: { mark: "●", className: "text-ok" },
  stale: { mark: "◐", className: "text-warn" },
  down: { mark: "○", className: "text-bad" },
  // Not a failure: an archive never held it (spec 011 FR-037).
  "not in this archive": { mark: "–", className: "text-muted" },
};

function stateOf(view: View<null>): State {
  if (view.available) return "up";
  return view.stale ? "stale" : "down";
}

/** Every source the dashboard reads: whether it answered, and when. */
export function SourceStrip({ sources, now }: Props) {
  const archived = useContext(ArchiveMode);
  if (sources === undefined) return <p className="text-sm text-muted">Loading sources…</p>;

  return (
    <ul aria-label="Sources" className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      {sources.map((view) => {
        const notHeld = archived && !view.available && !!view.reason?.startsWith(NOT_IN_ARCHIVE);
        const state: State = notHeld ? NOT_IN_ARCHIVE : stateOf(view);
        return (
          <li key={view.source} className="rounded-lg border border-line bg-surface p-3 text-sm">
            <p className="flex items-baseline justify-between gap-2">
              <span className="font-medium">{sourceLabel(view.source)}</span>
              <span className="text-xs whitespace-nowrap">
                <span aria-hidden="true" className={MARKS[state].className}>
                  {MARKS[state].mark}
                </span>{" "}
                {state} · <span className="text-muted">{ageText(view.fetched_at, now)}</span>
              </span>
            </p>
            {!notHeld && !view.available && view.reason && (
              <p className="mt-1 break-words text-muted">{view.reason}</p>
            )}
            {notHeld && view.remedy && (
              <p className="mt-1 text-xs text-muted">
                To see it live: <code className="font-mono">{view.remedy}</code>
              </p>
            )}
            {!notHeld && !view.available && view.remedy && (
              <p className="mt-2">
                <Command text={view.remedy} />
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
