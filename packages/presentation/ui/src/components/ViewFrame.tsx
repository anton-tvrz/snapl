import { createContext, type ReactNode, useContext, useId } from "react";

import { sourceLabel } from "../api/open";
import type { View } from "../api/types";
import { ageText } from "../lib/time";
import { Command } from "./Command";

/** True when the page shows a history archive: what it does not hold is not a failure (FR-037). */
export const ArchiveMode = createContext(false);

// The server's words for a view an archive does not hold (archive.py NOT_IN_ARCHIVE).
export const NOT_IN_ARCHIVE = "not in this archive";

interface Props {
  title: string;
  /** Undefined until the first answer arrives. */
  view: View<unknown> | undefined;
  /** The page's "now": the wall clock when live, the archive's time otherwise. */
  now: Date;
  /** True when a past time is shown and this view's source keeps no history. */
  presentOnly?: boolean;
  /** Why the last request failed. Shown in place of "Loading…" before any answer. */
  error?: Error | null;
  children: ReactNode;
}

/**
 * One panel. It always says where its data came from and how old it is, and
 * when its source is down it gives the reason and the command that fixes it
 * (010 FR-005, FR-030, FR-031). Stale data is shown, labelled with its age.
 */
export function ViewFrame({ title, view, now, presentOnly = false, error, children }: Props) {
  const showContent = view !== undefined && (view.available || (view.stale === true && view.data != null));
  const age = ageText(view?.fetched_at, now);
  const heading = useId();
  const archived = useContext(ArchiveMode) && !!view && !view.available && !!view.reason?.startsWith(NOT_IN_ARCHIVE);
  const detail = archived ? view.reason?.slice(NOT_IN_ARCHIVE.length).replace(/^:\s*/, "") : undefined;

  return (
    <section aria-labelledby={heading} className="rounded-lg border border-line bg-surface p-4">
      <header className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id={heading} className="text-sm font-semibold">
          {title}
        </h2>
        {view !== undefined && (
          <p className="text-xs text-muted">
            {sourceLabel(view.source)} · {age}
          </p>
        )}
      </header>

      {view === undefined &&
        (error ? (
          <p role="alert" className="text-sm break-words">
            {error.message}
          </p>
        ) : (
          <p className="text-sm text-muted">Loading…</p>
        ))}

      {archived && (
        <div className="mb-3 text-sm text-muted">
          <p role="status" className="font-medium">
            Not in this archive
          </p>
          {detail && <p className="mt-1">{detail}</p>}
          {view.remedy && (
            <p className="mt-1 text-xs">
              To see it live: <code className="font-mono">{view.remedy}</code>
            </p>
          )}
        </div>
      )}

      {view !== undefined && !view.available && !archived && (
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
