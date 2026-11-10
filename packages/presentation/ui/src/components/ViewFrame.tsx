import type { ReactNode } from "react";

import { sourceLabel } from "../api/open";
import type { View } from "../api/types";
import { ageText } from "../lib/time";
import { Command } from "./Command";

interface Props {
  title: string;
  /** Undefined until the first answer arrives. */
  view: View<unknown> | undefined;
  /** The page's "now": the wall clock when live, the archive's time otherwise. */
  now: Date;
  /** True when a past time is shown and this view's source keeps no history. */
  presentOnly?: boolean;
  children: ReactNode;
}

/**
 * One panel. It always says where its data came from and how old it is, and
 * when its source is down it gives the reason and the command that fixes it
 * (010 FR-005, FR-030, FR-031). Stale data is shown, labelled with its age.
 */
export function ViewFrame({ title, view, now, presentOnly = false, children }: Props) {
  const showContent = view !== undefined && (view.available || (view.stale === true && view.data != null));
  const age = ageText(view?.fetched_at, now);

  return (
    <section className="rounded-lg border border-line bg-surface p-4">
      <header className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-sm font-semibold">{title}</h2>
        {view !== undefined && (
          <p className="text-xs text-muted">
            {sourceLabel(view.source)} · {age}
          </p>
        )}
      </header>

      {view === undefined && <p className="text-sm text-muted">Loading…</p>}

      {view !== undefined && !view.available && (
        <div className="mb-3 rounded border border-warn-line bg-warn-surface p-3 text-sm">
          <p role="status" className="font-medium">
            {view.stale ? `Stale: last read ${age}` : "Unavailable"}
          </p>
          {view.reason && <p className="mt-1 break-words">{view.reason}</p>}
          {view.remedy && (
            <p className="mt-2">
              <Command text={view.remedy} />
            </p>
          )}
        </div>
      )}

      {presentOnly && (
        <p className="mb-3 text-xs text-muted">Shows only the present: this source keeps no history.</p>
      )}

      {showContent && children}
    </section>
  );
}
